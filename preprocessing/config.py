# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Preprocessing configuration. Every tunable number lives here and nowhere else.

All values can be overridden with environment variables without a rebuild.
"""

import os

# ── Kafka: the contract with ingestion (in) and ml_inference (out) ───────
KAFKA_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
TOPIC_IN = os.getenv("PREPROC_INPUT_TOPIC", "raw-data")
TOPIC_OUT = os.getenv("PREPROC_OUTPUT_TOPIC", "preprocessed-multiband")
CONSUMER_GROUP = "preprocessing-group"

# ── Object storage (MinIO locally, S3 on AWS) ────────────────────────────
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
# Leave unset locally. On AWS set it to "" so s3fs talks to real S3.
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", f"http://{MINIO_ENDPOINT}")
BUCKET_IN = os.getenv("PREPROC_BUCKET_IN", "raw-scenes")          # ingestion
BUCKET_OUT = os.getenv("PREPROC_BUCKET_OUT", "preprocessed-data")  # ml reads
# ml_inference reads multiband.npy today. Turn off once it reads the Zarr.
WRITE_NPY = os.getenv("PREPROC_WRITE_NPY", "1") == "1"

# ── Dask chunking ────────────────────────────────────────────────────────
# Chunks split x/y only; every chunk holds the FULL spectrum, because each
# step works along one pixel's spectrum. 256 x 256 x 285 x 4 B = ~75 MB.
CHUNK_SIZE = int(os.getenv("PREPROC_CHUNK_SIZE", "256"))

# ── Radiometry ───────────────────────────────────────────────────────────
FILL_VALUE = float(os.getenv("PREPROC_FILL_VALUE", "-9999.0"))   # EMIT, AVIRIS
# Integer reflectance (0..10000) is detected once per scene by its 99th
# percentile and divided down. EMIT L2A is already 0..1, so normally a no-op.
SCALED_INPUT_THRESHOLD = float(os.getenv("PREPROC_SCALE_THRESHOLD", "1.5"))
REFLECTANCE_SCALE = float(os.getenv("PREPROC_REFLECTANCE_SCALE", "10000.0"))

# Dark-object subtraction removes atmospheric haze from RAW radiance. On a
# product that is already surface reflectance it removes real signal: on a
# real EMIT L2A granule it collapsed NDVI from +0.361 to -0.041.
#   "auto"    skip for surface reflectance (store flag, or satellite below)
#   "always"  / "never"   force it
DARK_SUBTRACTION = os.getenv("PREPROC_DARK_SUBTRACTION", "auto")
DARK_PERCENTILE = float(os.getenv("PREPROC_DARK_PERCENTILE", "1.0"))
# Ingestion fetches EMIT L2A RFL and EnMAP L2A: both already corrected.
SURFACE_REFLECTANCE_SOURCES = set(
    os.getenv("PREPROC_SR_SOURCES", "EMIT,EnMAP").split(","))

# ── Bad bands (nm) ───────────────────────────────────────────────────────
# Water-vapour absorption cores: not retrievable, so INTERPOLATED, never
# deleted. bands.py keeps every network input outside both.
WATER_WINDOWS = [(1340.0, 1460.0), (1790.0, 1960.0)]
WAVELENGTH_MIN = float(os.getenv("PREPROC_WL_MIN", "400.0"))
WAVELENGTH_MAX = float(os.getenv("PREPROC_WL_MAX", "2450.0"))
MIN_BAND_SNR = float(os.getenv("PREPROC_MIN_BAND_SNR", "5.0"))

# ── Savitzky-Golay smoothing: odd window > polyorder ─────────────────────
SG_WINDOW = int(os.getenv("PREPROC_SG_WINDOW", "7"))
SG_POLYORDER = int(os.getenv("PREPROC_SG_POLYORDER", "2"))

# ── Quality gate ─────────────────────────────────────────────────────────
# Reject only a scene that is mostly no-data; a partly cloudy one is kept.
MAX_NODATA_FRACTION = float(os.getenv("PREPROC_MAX_NODATA_FRACTION", "0.5"))
# Scene-wide statistics come from every Nth pixel: cheap, and seam-free.
STATS_STRIDE = int(os.getenv("PREPROC_STATS_STRIDE", "4"))
