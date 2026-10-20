"""
Index EMIT coverage over the service region into the PostGIS catalog (idempotent).

    python scripts/refresh_catalog.py                       # since EMIT's first day
    python scripts/refresh_catalog.py --since 2026-01-01

Runs in the one-shot init so the map shows coverage on first start; Dagster's
imagery_catalog keeps it fresh daily. Needs no NASA login (CMR is public).
"""

import argparse
import logging
import os
import sys
import time
from datetime import date

import psycopg

from hs_common import catalog, logs

log = logging.getLogger("refresh_catalog")


def main(argv=None):
    logs.setup()
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=catalog.EMIT_FIRST_DAY)
    ap.add_argument("--region", default=os.getenv("ALLOWED_REGION", "-124.5,32.5,-114.0,42.0"))
    a = ap.parse_args(argv)
    region = [float(v) for v in a.region.split(",")]
    t0 = time.time()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        n = catalog.refresh_emit(conn, region, a.since, date.today().isoformat())
    log.info("indexed %d EMIT acquisitions over %s since %s (%.0fs)", n, region, a.since, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
