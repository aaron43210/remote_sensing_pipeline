"""Report job progress to the orchestrator (the only writer of job state)."""

import logging

from .config import settings
from .kafka import publish
from .messages import JobStatus

log = logging.getLogger(__name__)


def report(job_id, stage, state, message="", task=None, progress=None):
    try:
        publish("job.status", JobStatus(job_id=job_id, stage=stage, state=state, message=message[:2000],
                                        task=task, progress=progress, service=settings.service))
    except Exception:            # status is best effort; never fail the work because of it
        log.exception("could not publish status for %s", job_id)
