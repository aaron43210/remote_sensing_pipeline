# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Where scenes are read from and written to. MinIO locally, S3 on AWS.

s3fs speaks the S3 API, so the only difference between the two is the
endpoint: config.S3_ENDPOINT_URL points at MinIO, and empty means AWS.
Zarr reads and writes chunk by chunk through it -- nothing is downloaded
to local disk first.
"""

import numpy as np

import config


def filesystem():
    """An s3fs filesystem for MinIO, or for AWS when no endpoint is set."""
    import s3fs

    if not config.S3_ENDPOINT_URL:
        return s3fs.S3FileSystem()             # AWS: IAM role / env creds
    return s3fs.S3FileSystem(
        key=config.MINIO_ACCESS_KEY, secret=config.MINIO_SECRET_KEY,
        client_kwargs={"endpoint_url": config.S3_ENDPOINT_URL})


def ensure_bucket(fs, bucket):
    if not fs.exists(bucket):
        fs.mkdir(bucket)


def input_store(fs, zarr_path):
    """The raw cube ingestion wrote: raw-scenes/<scene_id>/raw.zarr."""
    return fs.get_mapper(f"{config.BUCKET_IN}/{zarr_path}")


def output_store(fs, scene_id):
    return fs.get_mapper(f"{config.BUCKET_OUT}/{scene_id}/preprocessed.zarr")


def npy_writer(fs, scene_id):
    """
    A save_npy callable for pipeline.run_scene().

    Returns the key relative to the bucket, which is what ml_inference
    passes to fget_object('preprocessed-data', key).
    """
    key = f"{scene_id}/multiband.npy"

    def save(array):
        with fs.open(f"{config.BUCKET_OUT}/{key}", "wb") as handle:
            np.save(handle, array)
        return key

    return save
