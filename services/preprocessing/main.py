# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
import os
import logging
import numpy as np
import msgpack
import zarr
import tempfile
import shutil
from minio import Minio
from kafka import KafkaConsumer
from shared.kafka_helpers import create_reliable_producer, send_with_callback
from shared.config import Settings
from shared.chunked_processor import process_chunked
from atmospheric_correction import quac_correction, save_bad_bands, apply_savitzky_golay

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PreprocessingService:
    def __init__(self):
        self.consumer = KafkaConsumer(
            'raw-data',
            bootstrap_servers=Settings.KAFKA_BOOTSTRAP_SERVERS,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            group_id='preprocessing-group'
        )
        self.producer = create_reliable_producer(Settings.KAFKA_BOOTSTRAP_SERVERS)
        self.minio = Settings.get_minio_client()
        # Ensure buckets exist
        if not self.minio.bucket_exists("preprocessed-data"):
            self.minio.make_bucket("preprocessed-data")


    def download_zarr(self, bucket, prefix, local_dir):
        objects = self.minio.list_objects(bucket, prefix=prefix, recursive=True)
        for obj in objects:
            rel_path = os.path.relpath(obj.object_name, prefix)
            local_path = os.path.join(local_dir, rel_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            self.minio.fget_object(bucket, obj.object_name, local_path)
        return local_dir

    def process(self):
        logger.info("Preprocessing service started")
        for msg in self.consumer:
            try:
                data = msg.value
                scene_id = data['scene_id']
                zarr_path = data['zarr_path']
                wavelengths = data['wavelengths']

                logger.info(f"Processing {scene_id}")

                local_dir = tempfile.mkdtemp()
                try:
                    local_zarr = os.path.join(local_dir, 'data.zarr')
                    self.download_zarr("raw-hyperspectral", zarr_path, local_zarr)

                    out_zarr_path = f"{scene_id}/preprocessed.zarr"
                    local_out = os.path.join(local_dir, 'preprocessed.zarr')
                    
                    from atmospheric_correction import quac_correction, get_good_bands_indices, apply_bad_bands_filter, apply_savitzky_golay
                    
                    good_indices, clean_wl = get_good_bands_indices(wavelengths)

                    def chunk_processor(tile, wls):
                        corrected = quac_correction(tile)
                        clean = apply_bad_bands_filter(corrected, good_indices)
                        return apply_savitzky_golay(clean)

                    process_chunked(local_zarr, chunk_processor, local_out)
                    
                    # Update wavelengths in the output Zarr metadata
                    zout = zarr.open(local_out, mode='a')
                    zout.attrs['wavelengths'] = clean_wl
                    final_shape = zout.shape

                    # Upload to MinIO
                    for root, dirs, files in os.walk(local_out):
                        for f in files:
                            full_path = os.path.join(root, f)
                            rel_path = os.path.relpath(full_path, local_out)
                            self.minio.fput_object(
                                "preprocessed-data",
                                f"{out_zarr_path}/{rel_path}",
                                full_path
                            )

                    # Publish to Kafka
                    out_msg = {
                        'scene_id': scene_id,
                        'zarr_path': out_zarr_path,
                        'wavelengths': clean_wl,
                        'shape': list(final_shape),
                        'tasks': data.get('tasks', ["agriculture", "mineral"]),
                        'timestamp': data.get('timestamp')
                    }
                    send_with_callback(self.producer, 'preprocessed', out_msg)
                    logger.info(f"Preprocessed {scene_id}")

            except Exception as e:
                logger.error(f"Error: {e}", exc_info=True)
            finally:
                if 'local_dir' in locals() and os.path.exists(local_dir):
                    shutil.rmtree(local_dir)

if __name__ == "__main__":
    PreprocessingService().process()
