"""
Ingestion service: ingest.requests -> raw.hyperspectral + raw.thermal.

The hyperspectral (EMIT) and thermal (Landsat) branches run in parallel; each
reports its own status, so a cloudy EMIT scene does not hold back the thermal
map. Scale out by adding replicas (one partition each).
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import redis

from hs_common import logs, status, storage
from hs_common.config import settings
from hs_common.kafka import NoData, Permanent, Worker, publish
from hs_common.messages import IngestRequest, RawHyperspectral, RawThermal

import hyperspectral
import thermal

log = logging.getLogger("ingestion")
HYPERSPECTRAL_TASKS = {"agriculture", "mineral"}
_redis = redis.Redis.from_url(settings.redis_url, socket_timeout=5)


def _branch(name, fn, req, task):
    """Run one branch; report NO_DATA/ERROR for its tasks without failing the other branch."""
    job = req["job_id"]
    try:
        status.report(job, "ingest", "RUNNING", f"searching {name}", task=task)
        body = fn(req)
        if _redis.exists(f"cancelled:{job}"):
            return
        if name == "EMIT":
            publish("raw.hyperspectral", RawHyperspectral(**body))
            status.report(job, "preprocess", "RUNNING", f"EMIT {body['granule_id']} staged", task=task)
        else:
            publish("raw.thermal", RawThermal(**body))
            status.report(job, "preprocess", "RUNNING", f"Landsat {body['scene_id']} staged", task=task)
    except NoData as e:
        status.report(job, "ingest", "NO_DATA", str(e), task=task)
    except PermissionError as e:
        status.report(job, "ingest", "ERROR", f"{name} access: {e}", task=task)
    except Permanent as e:
        status.report(job, "ingest", "ERROR", str(e), task=task)
    except Exception as e:
        log.exception("%s branch failed", name)
        status.report(job, "ingest", "ERROR", f"{name} ingestion failed: {e}", task=task)


def handle(topic, raw):
    req = IngestRequest(**raw).model_dump()
    if _redis.exists(f"cancelled:{req['job_id']}"):
        log.info("job cancelled before ingestion")
        return
    branches = []
    if HYPERSPECTRAL_TASKS & set(req["tasks"]):
        branches.append(("EMIT", hyperspectral.run, None))
    if "thermal" in req["tasks"]:
        branches.append(("Landsat", thermal.run, "thermal"))
    with ThreadPoolExecutor(max_workers=len(branches)) as ex:
        list(ex.map(lambda b: _branch(b[0], b[1], req, b[2]), branches))


def main():
    logs.setup()
    storage.ensure_bucket()
    Worker("ingestion", ["ingest.requests"], handle).run()


if __name__ == "__main__":
    main()
