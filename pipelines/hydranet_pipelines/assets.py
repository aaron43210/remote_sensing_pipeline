import io
import json
import os
import subprocess
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from hs_common import catalog, storage
from hs_common.sources import emit, landsat
from hs_common.storage import keys

DB = os.getenv("DATABASE_URL", "")
REGION = [float(v) for v in os.getenv("CATALOG_REGION", "-124.5,32.5,-114.0,42.0").split(",")]
TILE_DEG = 1.0                         # catalog the region in 1-degree tiles
PRESTAGE_PER_DAY = int(os.getenv("PRESTAGE_PER_DAY", "4"))
AGREEMENT_FLOOR = float(os.getenv("AGREEMENT_FLOOR", "0.95"))


def _tiles(region, step):
    lon = region[0]
    while lon < region[2]:
        lat = region[1]
        while lat < region[3]:
            yield [round(lon, 4), round(lat, 4), round(min(lon + step, region[2]), 4), round(min(lat + step, region[3]), 4)]
            lat += step
        lon += step


def _dq(conn, run_id, dataset, check, passed, observed):
    conn.execute("INSERT INTO dq_results (run_id, dataset, check_name, passed, observed) VALUES (%s,%s,%s,%s,%s)",
                 (run_id, dataset, check, passed, json.dumps(observed)))


@asset(description="EMIT and Landsat acquisitions over the region, with footprints, in the PostGIS catalog")
def imagery_catalog(context: AssetExecutionContext) -> MaterializeResult:
    since = (date.today() - timedelta(days=45)).isoformat()
    rows = {}
    # EMIT: every acquisition intersecting the region (the map's coverage layer)
    with psycopg.connect(DB) as conn:
        n_emit = catalog.refresh_emit(conn, REGION, since, date.today().isoformat())
    for tile in _tiles(REGION, TILE_DEG):
        try:
            item = landsat.search(tile, since, None, max_cloud=80)
            if item:
                rows[item["id"]] = ("LANDSAT", item["properties"]["datetime"],
                                    item["properties"].get("eo:cloud_cover"), item["geometry"], None)
        except Exception as e:
            context.log.warning(f"Landsat search failed for {tile}: {e}")
    run_id = context.run_id
    with psycopg.connect(DB) as conn:
        for gid, (sensor, at, cloud, fp, url) in rows.items():
            catalog.upsert(conn, sensor, gid, at, cloud, fp, url)
        _dq(conn, run_id, "catalog_granules", "found_any_acquisition", n_emit + len(rows) > 0,
            {"emit": n_emit, "landsat": len(rows)})
    return MaterializeResult(metadata={"acquisitions": n_emit + len(rows), "emit": n_emit, "landsat": len(rows)})


@asset(deps=[imagery_catalog], description="Clearest recent EMIT granules downloaded into Bronze, so user jobs skip NASA")
def prestaged_granules(context: AssetExecutionContext) -> MaterializeResult:
    if not (os.getenv("EARTHDATA_TOKEN") or os.getenv("EARTHDATA_USERNAME")):
        context.log.info("no Earthdata credentials; pre-staging skipped")
        return MaterializeResult(metadata={"staged": 0, "skipped": "no credentials"})
    staged = 0
    with psycopg.connect(DB) as conn:
        todo = conn.execute("""SELECT granule_id, url FROM catalog_granules WHERE sensor = 'EMIT' AND status = 'indexed'
            AND url IS NOT NULL AND acquired_at > now() - interval '30 days' ORDER BY cloud_pct NULLS LAST, acquired_at DESC
            LIMIT %s""", (PRESTAGE_PER_DAY,)).fetchall()
        for gid, url in todo:
            key = keys.emit_granule(gid, url.rsplit("/", 1)[-1])
            if not storage.exists(key):
                with tempfile.TemporaryDirectory() as td:
                    path = emit.download(url, os.path.join(td, "g.nc"))
                    storage.put_file(key, path)
            conn.execute("UPDATE catalog_granules SET status = 'staged', bronze_key = %s, updated_at = now() WHERE granule_id = %s",
                         (key, gid))
            conn.commit()
            staged += 1
    return MaterializeResult(metadata={"staged": staged})


def _list(prefix):
    pages = storage.client().get_paginator("list_objects_v2").paginate(Bucket=storage.bucket(), Prefix=prefix)
    return [o["Key"] for p in pages for o in p.get("Contents", [])]


@asset(description="Event sink micro-batches merged into one Parquet file per topic and finished day")
def events_compacted(context: AssetExecutionContext) -> MaterializeResult:
    today = datetime.now(timezone.utc).date().isoformat()
    merged = 0
    for key in sorted({"/".join(k.split("/")[:4]) for k in _list("bronze/events/")}):
        day = key.rsplit("dt=", 1)[-1]
        if day >= today:
            continue                                   # still being written
        parts = [k for k in _list(key + "/") if not k.endswith("/compacted.parquet")]
        if len(parts) <= 1:
            continue
        tables = [pq.read_table(io.BytesIO(storage.get_bytes(k))) for k in parts]
        existing = key + "/compacted.parquet"
        if storage.exists(existing):
            tables.append(pq.read_table(io.BytesIO(storage.get_bytes(existing))))
        t = pa.concat_tables(tables)
        # at-least-once delivery upstream: drop duplicate events here
        df = t.to_pandas().drop_duplicates(subset=["topic", "partition", "offset"])
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf, compression="zstd")
        storage.put_bytes(existing, buf.getvalue())
        storage.client().delete_objects(Bucket=storage.bucket(), Delete={"Objects": [{"Key": k} for k in parts]})
        merged += len(parts)
    return MaterializeResult(metadata={"files_merged": merged})


def _write_table(name, rows, columns):
    today = date.today().isoformat()
    table = pa.Table.from_pylist(rows) if rows else pa.table({c: [] for c in columns})
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    storage.put_bytes(keys.table(name, today, "part-0"), buf.getvalue())   # one snapshot per day, overwritten hourly


@asset(deps=[events_compacted], description="One row per job: outcome and time spent in every stage (Gold)")
def gold_job_facts(context: AssetExecutionContext) -> MaterializeResult:
    cols = ["job_id", "user_id", "tasks", "source", "status", "area_km2", "created_at", "completed_at",
            "total_s", "reused", "model_version"]
    with psycopg.connect(DB) as conn:
        cur = conn.execute("""SELECT job_id, user_id, array_to_string(tasks, ','), source, status,
                ST_Area(bbox::geography) / 1e6, created_at, completed_at,
                extract(epoch FROM completed_at - created_at), reused_from IS NOT NULL, model_version
            FROM jobs WHERE created_at > now() - interval '35 days'""")
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["total_s"] = float(r["total_s"]) if r["total_s"] is not None else None
        r["area_km2"] = float(r["area_km2"])
    _write_table("job_facts", rows, cols)
    done = [r for r in rows if r["status"] == "COMPLETED" and r["total_s"] and not r["reused"]]
    return MaterializeResult(metadata={"jobs": len(rows), "completed": len(done),
                                       "median_s": MetadataValue.float(sorted(r["total_s"] for r in done)[len(done) // 2]
                                                                       if done else 0.0)})


@asset(description="Per-layer statistics of every map produced (Gold): drift and usage analytics")
def gold_layer_stats(context: AssetExecutionContext) -> MaterializeResult:
    cols = ["job_id", "task", "name", "kind", "mean", "p5", "p50", "p95", "valid_fraction", "class_fraction", "created_at"]
    with psycopg.connect(DB) as conn:
        cur = conn.execute("""SELECT l.job_id, l.task, l.name, l.kind, l.stats, j.created_at FROM layers l
            JOIN jobs j USING (job_id) WHERE j.created_at > now() - interval '35 days' AND j.reused_from IS NULL""")
        rows = []
        for job_id, task, name, kind, stats, created in cur.fetchall():
            s = stats or {}
            rows.append({"job_id": job_id, "task": task, "name": name, "kind": kind, "mean": s.get("mean"),
                         "p5": s.get("p5"), "p50": s.get("p50"), "p95": s.get("p95"),
                         "valid_fraction": s.get("valid_fraction"),
                         "class_fraction": json.dumps(s.get("class_fraction")) if s.get("class_fraction") else None,
                         "created_at": created})
    _write_table("layer_stats", rows, cols)
    return MaterializeResult(metadata={"layers": len(rows)})


@asset(description="HydraNet vs classical agreement over the last 24 h (shadow verification); fails below the floor")
def model_agreement_report(context: AssetExecutionContext) -> MaterializeResult:
    with psycopg.connect(DB) as conn:
        cur = conn.execute("""SELECT task, model_version, verification FROM job_results r JOIN jobs j USING (job_id)
            WHERE r.created_at > now() - interval '24 hours' AND j.reused_from IS NULL""")
        agg = {}
        for task, model, ver in cur.fetchall():
            for layer, m in (ver or {}).items():
                # same scale as the live metric: share of pixels for classes, 1 - nMAE for continuous
                # maps (R² is unstable on uniform areas; only results from before nMAE fall back to it)
                v = m.get("pixel_agreement", 1 - m["nmae"] if "nmae" in m else m.get("r2"))
                if v is None:
                    continue
                a = agg.setdefault((task, layer, model), [0.0, 0])
                a[0] += v * m.get("n", 1)
                a[1] += m.get("n", 1)
        report, failing = {}, []
        for (task, layer, model), (s, n) in agg.items():
            value = s / max(n, 1)
            report[f"{task}/{layer}"] = {"model": model, "agreement": round(value, 5), "pixels": n}
            ok = value >= AGREEMENT_FLOOR
            _dq(conn, context.run_id, "hydranet_vs_classical", f"{task}/{layer}", ok, report[f"{task}/{layer}"])
            if not ok:
                failing.append(f"{task}/{layer}={value:.3f}")
    if failing:
        raise Exception(f"HydraNet disagrees with the classical method: {failing}")  # visible as a failed run
    return MaterializeResult(metadata={"layers_checked": len(report), "report": MetadataValue.json(report)})


@asset(description="Prune idempotency and outbox rows that can no longer be needed")
def db_housekeeping(context: AssetExecutionContext) -> MaterializeResult:
    with psycopg.connect(DB) as conn:
        a = conn.execute("DELETE FROM processed_events WHERE processed_at < now() - interval '14 days'").rowcount
        b = conn.execute("DELETE FROM outbox WHERE sent_at < now() - interval '7 days'").rowcount
        c = conn.execute("DELETE FROM dq_results WHERE ts < now() - interval '180 days'").rowcount
    return MaterializeResult(metadata={"processed_events": a, "outbox": b, "dq_results": c})


@asset(description="Nightly logical backup of Postgres to object storage (ops/pg_backups/, 30-day lifecycle)")
def pg_backup(context: AssetExecutionContext) -> MaterializeResult:
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "dump.sql.gz")
        subprocess.run(f"pg_dump --no-owner --format=plain '{DB}' | gzip -9 > {path}", shell=True, check=True)
        key = f"ops/pg_backups/{datetime.now(timezone.utc):%Y-%m-%d}-{uuid.uuid4().hex[:6]}.sql.gz"
        storage.put_file(key, path)
        size = os.path.getsize(path)
    return MaterializeResult(metadata={"key": key, "bytes": size})
