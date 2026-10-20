"""
The PostGIS imagery catalog (catalog_granules): where and when imagery exists.

    n = refresh_emit(conn, region, "2022-08-01", "2026-10-20")

Filled by the one-shot init (scripts/refresh_catalog.py) and daily by Dagster
(imagery_catalog). The gateway serves it to the map as EMIT coverage, so users
see where hyperspectral data exists before they draw a box.
"""

from .sources import emit

EMIT_FIRST_DAY = "2022-08-01"          # EMIT science data starts in August 2022


def wkt_multipolygon(geometry):
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    rings = []
    for poly in polys:
        ring = [tuple(p[:2]) for p in poly[0]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        rings.append("((" + ", ".join(f"{x} {y}" for x, y in ring) + "))")
    return "MULTIPOLYGON(" + ", ".join(rings) + ")"


UPSERT = """INSERT INTO catalog_granules (granule_id, sensor, acquired_at, cloud_pct, footprint, url)
    VALUES (%s, %s, %s, %s, ST_Multi(ST_MakeValid(ST_GeomFromText(%s, 4326))), %s)
    ON CONFLICT (granule_id) DO UPDATE SET cloud_pct = EXCLUDED.cloud_pct, footprint = EXCLUDED.footprint,
        url = EXCLUDED.url, updated_at = now()"""


def upsert(conn, sensor, granule_id, acquired_at, cloud_pct, footprint, url):
    conn.execute(UPSERT, (granule_id, sensor, acquired_at, cloud_pct, wkt_multipolygon(footprint), url))


def refresh_emit(conn, region, date_from, date_to):
    """Index every EMIT acquisition intersecting the region in the window; returns the count."""
    granules = emit.footprints(region, date_from, date_to)
    with conn.cursor() as cur:                      # executemany pipelines the inserts (one round trip)
        cur.executemany(UPSERT, [(g["granule_id"], "EMIT", g["acquired_at"], g["cloud_cover"],
                                  wkt_multipolygon(g["footprint"]), g["url"]) for g in granules])
        # older processing versions of the same acquisitions are superseded
        cur.execute("""DELETE FROM catalog_granules WHERE sensor = 'EMIT' AND status = 'indexed'
            AND split_part(granule_id, '_', 5) = ANY(%s) AND NOT (granule_id = ANY(%s))""",
                    ([g["granule_id"].split("_")[4] for g in granules], [g["granule_id"] for g in granules]))
    conn.commit()
    return len(granules)
