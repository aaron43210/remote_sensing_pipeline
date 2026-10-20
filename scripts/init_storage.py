"""
Prepare object storage through the plain S3 API (works on MinIO and AWS).

    python scripts/init_storage.py

Creates the bucket where allowed (S3_CREATE_BUCKET=true, i.e. local MinIO;
on AWS Terraform owns it) and applies the lake's lifecycle rules.
"""

import logging

from botocore.exceptions import ClientError

from hs_common import storage
from hs_common.config import settings

log = logging.getLogger("init_storage")

LIFECYCLE = [  # prefix, days to keep
    ("bronze/emit/", 30),
    ("bronze/landsat/", 30),
    ("silver/", 14),
    ("gold/results/", 180),
    ("fixtures/", 365),
]


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    s3 = storage.client()
    storage.ensure_bucket()
    s3.head_bucket(Bucket=settings.s3_bucket)
    rules = [{"ID": f"expire-{p.strip('/').replace('/', '-')}", "Filter": {"Prefix": p}, "Status": "Enabled",
              "Expiration": {"Days": d}} for p, d in LIFECYCLE]
    rules.append({"ID": "abort-incomplete-uploads", "Filter": {"Prefix": ""}, "Status": "Enabled",
                  "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2}})
    # Apply the full set; if the provider rejects a rule type (MinIO has no
    # AbortIncompleteMultipartUpload), fall back to the expiry rules alone.
    for attempt in (rules, rules[:-1]):
        try:
            s3.put_bucket_lifecycle_configuration(Bucket=settings.s3_bucket, LifecycleConfiguration={"Rules": attempt})
            log.info("lifecycle rules applied: %s", [r["ID"] for r in attempt])
            break
        except ClientError as e:
            log.warning("lifecycle rules rejected (%s)", e)
    log.info("bucket %s ready", settings.s3_bucket)


if __name__ == "__main__":
    main()
