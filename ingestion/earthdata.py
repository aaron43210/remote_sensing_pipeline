"""
The job owner's NASA Earthdata token, handed over by the gateway.

Users connect their own Earthdata account in the app. For a job that needs an
EMIT download the gateway seals the owner's token for that job alone
(AES-256-GCM, CREDENTIALS_KEY, bound to "earthdata:job:{job_id}"; see
pkg/secretbox) under Redis key earthdata:{job_id}, expiring with the job's
ingest deadline. It never travels through Kafka, logs or job status, and is
deleted as soon as the job's downloads are over.
"""

import os

import redis
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hs_common.config import settings

NONCE = 12
_redis = redis.Redis.from_url(settings.redis_url, socket_timeout=5)


def _key(job_id):
    return f"earthdata:{job_id}"


def token_for(job_id):
    """The owner's token, or None (nothing handed over: the platform's EARTHDATA_* settings, if any, apply)."""
    sealed = _redis.get(_key(job_id))
    key = os.getenv("CREDENTIALS_KEY", "")
    if not sealed or not key:
        return None
    try:
        plain = AESGCM(bytes.fromhex(key)).decrypt(sealed[:NONCE], sealed[NONCE:], f"earthdata:job:{job_id}".encode())
    except (InvalidTag, ValueError) as e:
        raise PermissionError("could not read your NASA Earthdata connection; reconnect it") from e
    return plain.decode()


def forget(job_id):
    _redis.delete(_key(job_id))
