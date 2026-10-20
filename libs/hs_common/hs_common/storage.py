"""
Object storage through the S3 API only: MinIO locally, Amazon S3 (or any
S3-compatible provider) in deployment. Nothing here knows which one it is.

    S3_ENDPOINT_URL      http://minio:9000 locally, empty on AWS
    S3_FORCE_PATH_STYLE  true for MinIO
    credentials          the standard AWS chain (env keys locally, IAM role on AWS)

Key layout (one bucket, lake layers as prefixes) lives in `keys`.
"""

import io
import logging
import os
import time
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import settings

log = logging.getLogger(__name__)

for _k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
    if os.environ.get(_k) == "":
        del os.environ[_k]          # empty = not set: boto3 then uses the instance role


class keys:
    """Every object key the platform writes. Change layout here only."""

    @staticmethod
    def emit_granule(granule_id, name):
        return f"bronze/emit/{granule_id}/{name}"

    @staticmethod
    def landsat_asset(scene_id, name):
        return f"bronze/landsat/{scene_id}/{name}"

    @staticmethod
    def events(topic, date, part):
        return f"bronze/events/topic={topic}/dt={date}/{part}.parquet"

    @staticmethod
    def silver(job_id, name):
        return f"silver/{job_id}/{name}"

    @staticmethod
    def result(job_id, task, name):
        return f"gold/results/{job_id}/{task}/{name}"

    @staticmethod
    def manifest(job_id):
        return f"gold/results/{job_id}/manifest.json"

    @staticmethod
    def stac_item(job_id):
        return f"gold/results/{job_id}/stac_item.json"

    @staticmethod
    def table(name, date, part):
        return f"gold/tables/{name}/dt={date}/{part}.parquet"

    @staticmethod
    def model(version, name):
        return f"models/hydra/{version}/{name}"

    @staticmethod
    def fixture(name):
        return f"fixtures/{name}"


def _client_kwargs():
    kw = {"region_name": settings.s3_region,
          "config": Config(s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
                           retries={"max_attempts": 8, "mode": "adaptive"},
                           max_pool_connections=64)}
    if settings.s3_endpoint_url:
        kw["endpoint_url"] = settings.s3_endpoint_url
    return kw


@lru_cache(maxsize=1)
def client():
    return boto3.client("s3", **_client_kwargs())


@lru_cache(maxsize=1)
def fs():
    """s3fs filesystem (for Zarr and other chunked access)."""
    import s3fs
    ckw = {"region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        ckw["endpoint_url"] = settings.s3_endpoint_url
    return s3fs.S3FileSystem(client_kwargs=ckw,
                             config_kwargs={"s3": {"addressing_style": "path" if settings.s3_force_path_style else "auto"}})


def bucket():
    return settings.s3_bucket


def ensure_bucket(attempts=30):
    """
    Wait for object storage; create the bucket only where allowed (local MinIO).
    On AWS Terraform owns the bucket and this only checks it is reachable.
    """
    for i in range(attempts):
        try:
            client().head_bucket(Bucket=bucket())
            return
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchBucket") and settings.s3_create_bucket:
                client().create_bucket(Bucket=bucket())
                log.info("created bucket %s", bucket())
                return
            if code in ("403", "AccessDenied") and not settings.s3_create_bucket:
                return                     # least-privilege role without HeadBucket: carry on
        except Exception as e:             # endpoint not up yet
            log.info("waiting for object storage (%s)", e)
        time.sleep(min(2 * (i + 1), 10))
    raise RuntimeError(f"object storage bucket {bucket()} not reachable")


def exists(key):
    try:
        client().head_object(Bucket=bucket(), Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def put_bytes(key, data, content_type="application/octet-stream", cache_control=None):
    extra = {"ContentType": content_type}
    if cache_control:
        extra["CacheControl"] = cache_control
    client().put_object(Bucket=bucket(), Key=key, Body=data, **extra)
    return key


def put_json(key, obj):
    import json
    return put_bytes(key, json.dumps(obj, default=str).encode(), "application/json")


def get_bytes(key):
    return client().get_object(Bucket=bucket(), Key=key)["Body"].read()


def get_json(key):
    import json
    return json.loads(get_bytes(key))


def put_file(key, path, content_type=None):
    extra = {"ContentType": content_type} if content_type else None
    client().upload_file(str(path), bucket(), key, ExtraArgs=extra)
    return key


def get_file(key, path):
    client().download_file(bucket(), key, str(path))
    return path


def zarr_store(key):
    """A zarr-compatible mapper for s3://bucket/key."""
    return fs().get_mapper(f"{bucket()}/{key}")


def put_numpy(key, array):
    import numpy as np
    buf = io.BytesIO()
    np.save(buf, array)
    return put_bytes(key, buf.getvalue())


def get_numpy(key):
    import numpy as np
    return np.load(io.BytesIO(get_bytes(key)))


def delete_prefix(prefix):
    paginator = client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket(), Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objs:
            client().delete_objects(Bucket=bucket(), Delete={"Objects": objs})
