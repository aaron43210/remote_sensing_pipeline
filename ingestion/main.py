# =============================================================
# OWNER: AARON
# SERVICE: Data Ingestion (Python) — Production Grade
# =============================================================
# Consumes Kafka topic "raw-ingest-requests" (published by Go API Gateway)
# Downloads satellite data from NASA EMIT or EnMAP API
# Saves as Zarr chunks to MinIO
# Publishes to "preprocessed-multiband" via Kafka for the preprocessing service
# =============================================================

import os
import json
import logging
import signal
import sys
import time
import uuid
import tempfile
import shutil
from datetime import datetime

import msgpack
import numpy as np
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ingestion] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────
KAFKA_SERVERS  = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
MINIO_ENDPOINT = os.getenv('MINIO_ENDPOINT', 'minio:9000')
MINIO_ACCESS   = os.getenv('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET   = os.getenv('MINIO_SECRET_KEY', 'minioadmin')
RAW_BUCKET     = 'raw-scenes'
EMIT_TOKEN     = os.getenv('NASA_EARTHDATA_TOKEN', '')

# ── Satellite Band Wavelengths (nm) ───────────────────────────
# These are the 10 target bands your Hydra Neural Network expects
TARGET_WAVELENGTHS_NM = [450, 680, 720, 800, 1450, 2205, 2265, 2320, 2350, 900]

class IngestionService:
    def __init__(self):
        # Kafka consumer — listens for requests from Go API Gateway
        self.consumer = KafkaConsumer(
            'raw-ingest-requests',
            bootstrap_servers=KAFKA_SERVERS,
            group_id='ingestion-prod-group',
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            auto_offset_reset='earliest',
            enable_auto_commit=False,
            max_poll_interval_ms=600_000,  # 10 min: large files take time
        )

        # Kafka producer — sends completion messages to preprocessing
        self.producer = KafkaProducer(
            bootstrap_servers=KAFKA_SERVERS,
            value_serializer=lambda m: msgpack.packb(m, use_bin_type=True),
            acks='all',
            retries=5,
        )

        # MinIO client
        self.minio = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS,
            secret_key=MINIO_SECRET,
            secure=False,
        )
        for bucket in [RAW_BUCKET, 'preprocessed-data']:
            if not self.minio.bucket_exists(bucket):
                self.minio.make_bucket(bucket)
                logger.info(f"Created bucket: {bucket}")

        logger.info("Ingestion service initialized")

    def run(self):
        logger.info("Listening on Kafka topic: raw-ingest-requests")

        # Graceful shutdown
        running = [True]
        def _stop(signum, frame):
            logger.info("Shutdown signal received")
            running[0] = False
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while running[0]:
            records = self.consumer.poll(timeout_ms=1000, max_records=1)
            for tp, messages in records.items():
                for msg in messages:
                    self._process_message(msg)
                    self.consumer.commit()

        self.consumer.close()
        logger.info("Ingestion service stopped")

    def _process_message(self, msg):
        payload = msg.value
        scene_id  = payload.get('scene_id', f"SCENE_{uuid.uuid4().hex[:8]}")
        bbox      = payload.get('bbox')        # [lon_min, lat_min, lon_max, lat_max]
        satellite = payload.get('satellite', 'EMIT')

        logger.info(f"Processing scene={scene_id} satellite={satellite} bbox={bbox}")

        local_dir = tempfile.mkdtemp(prefix="ingest_")
        try:
            if satellite == 'EMIT':
                data, wavelengths, meta = self._fetch_emit(bbox, scene_id, local_dir)
            elif satellite == 'EnMAP':
                data, wavelengths, meta = self._fetch_enmap(bbox, scene_id, local_dir)
            else:
                raise ValueError(f"Unsupported satellite: {satellite}")

            if data is None:
                logger.error(f"No data found for scene={scene_id}")
                return

            # Save raw Zarr to MinIO
            zarr_path = self._save_zarr(data, wavelengths, scene_id, local_dir)

            # Extract 10 target bands and save multiband.npy for ML
            multiband_path = self._extract_target_bands(data, wavelengths, scene_id, local_dir)

            # Publish to preprocessing topic (and then to ML)
            out_msg = {
                'scene_id':       scene_id,
                'satellite':      satellite,
                'bbox':           bbox,
                'zarr_path':      zarr_path,
                'data_path':      multiband_path,
                'wavelengths':    wavelengths,
                'shape':          list(data.shape),
                'timestamp':      datetime.utcnow().isoformat(),
            }
            self.producer.send('preprocessed-multiband', out_msg)
            self.producer.flush()
            logger.info(f"Published to preprocessed-multiband: scene={scene_id}")

        except Exception as e:
            logger.error(f"Failed scene={scene_id}: {e}", exc_info=True)
        finally:
            shutil.rmtree(local_dir, ignore_errors=True)

    def _fetch_emit(self, bbox, scene_id, local_dir):
        """Fetch NASA EMIT data from CMR STAC API."""
        import requests

        lon_min, lat_min, lon_max, lat_max = bbox
        stac_url = "https://cmr.earthdata.nasa.gov/stac/LPCLOUD/search"

        params = {
            "collections": ["EMITL2ARFL_001"],
            "bbox": f"{lon_min},{lat_min},{lon_max},{lat_max}",
            "limit": 1,
            "datetime": "2022-08-10T00:00:00Z/2024-12-31T23:59:59Z",
        }
        headers = {"Authorization": f"Bearer {EMIT_TOKEN}"} if EMIT_TOKEN else {}

        resp = requests.get(stac_url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        features = resp.json().get('features', [])

        if not features:
            return None, None, None

        # Get the download URL for the reflectance file
        item = features[0]
        assets = item.get('assets', {})
        rfl_asset = assets.get('reflectance', assets.get('data', None))
        if not rfl_asset:
            return None, None, None

        download_url = rfl_asset['href']
        local_nc = os.path.join(local_dir, f"{scene_id}.nc")

        logger.info(f"Downloading EMIT: {download_url}")
        r = requests.get(download_url, headers=headers, stream=True, timeout=300)
        r.raise_for_status()
        with open(local_nc, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
                f.write(chunk)

        # Parse NetCDF
        import netCDF4 as nc
        ds = nc.Dataset(local_nc)
        data = np.array(ds.variables['reflectance'][:]).astype(np.float32)  # (rows, cols, bands)
        wavelengths = list(np.array(ds.variables['wavelengths'][:]))
        meta = {'source': 'NASA_EMIT', 'item_id': item.get('id')}
        ds.close()

        return data, wavelengths, meta

    def _fetch_enmap(self, bbox, scene_id, local_dir):
        """
        Placeholder for EnMAP data fetching via EOWEB GeoPortal OData API.
        Returns synthetic data for now — replace with real API call.
        """
        logger.warning("EnMAP fetch: using synthetic placeholder data")
        rows, cols, bands = 512, 512, 242
        data = np.random.rand(rows, cols, bands).astype(np.float32) * 0.6
        wavelengths = list(np.linspace(420, 2450, bands))
        return data, wavelengths, {'source': 'EnMAP_PLACEHOLDER'}

    def _save_zarr(self, data, wavelengths, scene_id, local_dir):
        """Save data as Zarr chunks to MinIO."""
        import zarr
        zarr_path = os.path.join(local_dir, f"{scene_id}.zarr")
        store = zarr.open(zarr_path, mode='w', shape=data.shape,
                          dtype='float32', chunks=(64, 64, data.shape[2]))
        store[:] = data
        store.attrs['wavelengths'] = wavelengths
        store.attrs['scene_id'] = scene_id

        # Upload Zarr directory to MinIO
        for root, dirs, files in os.walk(zarr_path):
            for file in files:
                local_file = os.path.join(root, file)
                object_name = f"{scene_id}/raw.zarr/{os.path.relpath(local_file, zarr_path)}"
                self.minio.fput_object(RAW_BUCKET, object_name, local_file)

        logger.info(f"Zarr saved: {scene_id}/raw.zarr → MinIO/{RAW_BUCKET}")
        return f"{scene_id}/raw.zarr"

    def _extract_target_bands(self, data, wavelengths, scene_id, local_dir):
        """
        Select the 10 target wavelengths and save as a flat .npy array.
        This is the exact input the Hydra ML neural network expects.
        """
        wavelengths_arr = np.array(wavelengths)
        selected_indices = []
        for target_nm in TARGET_WAVELENGTHS_NM:
            idx = int(np.argmin(np.abs(wavelengths_arr - target_nm)))
            selected_indices.append(idx)

        multiband = data[:, :, selected_indices]  # (rows, cols, 10)

        # Normalize to 0-1 range
        multiband = np.clip(multiband, 0, 1)
        multiband = np.nan_to_num(multiband, nan=0.0)

        local_path = os.path.join(local_dir, 'multiband.npy')
        np.save(local_path, multiband)

        object_name = f"{scene_id}/multiband.npy"
        self.minio.fput_object('preprocessed-data', object_name, local_path)

        logger.info(f"10-band selection saved: preprocessed-data/{object_name}")
        return object_name


if __name__ == '__main__':
    # Retry loop: wait for Kafka to be ready on startup
    retries = 10
    for i in range(retries):
        try:
            service = IngestionService()
            service.run()
            break
        except Exception as e:
            logger.warning(f"Startup attempt {i+1}/{retries} failed: {e}")
            time.sleep(5 * (i + 1))
    else:
        logger.error("Could not start ingestion service after retries")
        sys.exit(1)
