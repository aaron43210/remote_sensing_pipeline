"""JSON logs with job_id on every line (contextvar), one format for all services."""

import contextvars
import json
import logging
import sys
import time

from .config import settings

job_id_var = contextvars.ContextVar("job_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
               "level": record.levelname, "service": settings.service,
               "logger": record.name, "msg": record.getMessage()}
        jid = job_id_var.get()
        if jid:
            out["job_id"] = jid
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def setup():
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(settings.log_level)
    for noisy in ("botocore", "urllib3", "s3fs", "aiobotocore", "numba", "rasterio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
