# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
import os
import logging
import msgpack
from kafka import KafkaConsumer
from shared.kafka_helpers import create_reliable_producer, send_with_callback
from shared.config import Settings
from atmospheric_correction import quac_correction_lazy, bad_band_mask, savgol_lazy
import s3fs
import xarray as xr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The 10 spectral bands (nm) required by the Hydra MTL Neural Network.
# Preprocessing MUST output exactly these bands — the ML service will assert n_bands == 10.
TARGET_BANDS_NM = [450, 680, 720, 800, 900, 1450, 2205, 2265, 2320, 2350]


class PreprocessingService:
    def __init__(self):
        # ── Kafka Consumer ────────────────────────────────────────────────────
        # Listens to 'raw-ingest-requests' — set in docker-compose.yml
        self.consumer = KafkaConsumer(
            'raw-ingest-requests',
            bootstrap_servers=Settings.KAFKA_BOOTSTRAP_SERVERS,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            group_id='preprocessing-group'
        )
        self.producer = create_reliable_producer(Settings.KAFKA_BOOTSTRAP_SERVERS)

    def _get_s3fs(self) -> s3fs.S3FileSystem:
        """Returns an s3fs filesystem pointed at our MinIO instance."""
        return s3fs.S3FileSystem(
            key=Settings.MINIO_ACCESS_KEY,
            secret=Settings.MINIO_SECRET_KEY,
            endpoint_url=f"http://{Settings.MINIO_ENDPOINT}",
            use_ssl=False
        )

    def open_scene_from_minio(self, zarr_path: str) -> xr.Dataset:
        """
        Opens a raw hyperspectral Zarr dataset directly from MinIO via S3FS.

        No data is downloaded to disk. Xarray + Dask create a lazy computation
        graph — chunks are only streamed from MinIO when a terminal operation
        (.compute() or .to_zarr()) is actually called.
        """
        fs = self._get_s3fs()
        store = s3fs.S3Map(root=f"raw-hyperspectral/{zarr_path}", s3=fs)
        logger.info(f"Opening scene from MinIO: raw-hyperspectral/{zarr_path}")
        # chunks='auto' lets Dask choose optimal chunk sizes based on array shape
        return xr.open_zarr(store, chunks='auto')

    def preprocess_lazy(self, ds: xr.Dataset) -> xr.DataArray:
        """
        Builds a lazy Dask computation graph for the full preprocessing pipeline.

        Operations are applied in this order:
          1. QUAC atmospheric correction (dark pixel subtraction)
          2. Bad band removal (water absorption windows masked out)
          3. Savitzky-Golay spectral smoothing
          4. 10-band selection for Hydra MTL model

        Nothing is computed until save_to_minio() triggers .to_zarr().
        RAM usage stays ~1-2GB regardless of input image size.
        """
        cube = ds['reflectance']  # shape: (y, x, band)

        # Step 1: QUAC Atmospheric Correction
        corrected = quac_correction_lazy(cube)

        # Step 2: Bad Band Removal — mask out water absorption windows
        mask = bad_band_mask(corrected.coords['wavelength'])
        filtered = corrected.isel(band=mask)

        # Step 3: Spectral Smoothing via Dask-parallelized Savitzky-Golay
        smoothed = savgol_lazy(filtered)

        # Step 4: Select the exact 10 bands the Hydra MTL model requires
        ten_band = smoothed.sel(wavelength=TARGET_BANDS_NM, method='nearest')

        logger.info(f"Lazy graph built — output will have {len(TARGET_BANDS_NM)} bands")
        return ten_band  # Still lazy — nothing computed yet

    def save_to_minio(self, data: xr.DataArray, out_path: str):
        """
        Writes the processed DataArray directly to MinIO as a Zarr store.

        Dask computes and writes chunks in parallel — the full image is never
        loaded into RAM at once. Replaces the old file-walk upload loop.
        """
        fs = self._get_s3fs()
        out_store = s3fs.S3Map(root=f"preprocessed-data/{out_path}", s3=fs)
        logger.info(f"Writing preprocessed scene to MinIO: preprocessed-data/{out_path}")
        # .to_zarr() triggers the entire Dask computation graph and streams to S3
        data.to_zarr(out_store, mode='w')

    def process(self):
        logger.info("Preprocessing service started — listening on 'raw-ingest-requests'")
        for msg in self.consumer:
            try:
                data = msg.value
                scene_id = data['scene_id']
                zarr_path = data['zarr_path']

                logger.info(f"Received scene: {scene_id}")

                # 1. Open raw scene — lazy, no RAM cost
                ds = self.open_scene_from_minio(zarr_path)

                # 2. Build the full preprocessing graph — still lazy
                processed = self.preprocess_lazy(ds)

                # 3. Compute + write to MinIO — this is where Dask does the work
                out_zarr_path = f"{scene_id}/preprocessed.zarr"
                self.save_to_minio(processed, out_zarr_path)

                # 4. Publish to 'preprocessed-multiband' — wakes up ML inference service
                out_msg = {
                    'scene_id': scene_id,
                    'zarr_path': out_zarr_path,
                    'wavelengths': TARGET_BANDS_NM,
                    'n_bands': len(TARGET_BANDS_NM),   # ML service asserts this == 10
                    'shape': list(processed.shape),
                    'tasks': data.get('tasks', ['agriculture', 'mineral', 'thermal']),
                    'timestamp': data.get('timestamp')
                }
                send_with_callback(self.producer, 'preprocessed-multiband', out_msg)
                logger.info(f"Published preprocessed scene {scene_id} -> 'preprocessed-multiband'")

            except Exception as e:
                logger.error(f"Error processing scene: {e}", exc_info=True)


if __name__ == "__main__":
    PreprocessingService().process()
