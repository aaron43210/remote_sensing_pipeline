# =============================================================
# OWNER: LANKAPRIYA
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
from lut_prosail import PROSAILLookupTable  # We have this class already

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RTMInversionService:
    def __init__(self):
        self.consumer = KafkaConsumer(
            'preprocessed',  # Switch to preprocessed to catch right after
            bootstrap_servers=Settings.KAFKA_BOOTSTRAP_SERVERS,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            group_id='rtm-inversion-group',
            auto_offset_reset='earliest'
        )
        self.producer = create_reliable_producer(Settings.KAFKA_BOOTSTRAP_SERVERS)
        self.minio = Settings.get_minio_client()
        if not self.minio.bucket_exists("biophysical-params"):
            self.minio.make_bucket("biophysical-params")
        # Initialize LUT
        self.lut = PROSAILLookupTable()  # This class must be in the same directory

    def process(self):
        logger.info("RTM inversion service started")
        for msg in self.consumer:
            try:
                data = msg.value
                scene_id = data['scene_id']
                # We need the actual spectra. This example assumes we have them in the message or can load from Zarr.
                # For brevity, we'll load preprocessed data again.
                zarr_path = data.get('zarr_path')  # if provided in message
                if not zarr_path:
                    logger.warning("No zarr_path found, skipping")
                    continue

                local_dir = tempfile.mkdtemp()
                try:
                    local_zarr = os.path.join(local_dir, 'data.zarr')
                    objects = self.minio.list_objects("preprocessed-data", prefix=zarr_path, recursive=True)
                    for obj in objects:
                        rel = os.path.relpath(obj.object_name, zarr_path)
                        local_file = os.path.join(local_zarr, rel)
                        os.makedirs(os.path.dirname(local_file), exist_ok=True)
                        self.minio.fget_object("preprocessed-data", obj.object_name, local_file)
                        
                    z = zarr.open(local_zarr, mode='r')
                    import dask.array as da
                    dask_arr = da.from_zarr(z)
                    wavelengths = z.attrs['wavelengths']

                    rows, cols, bands = dask_arr.shape
                    chl_map = np.zeros((rows, cols))
                    lai_map = np.zeros((rows, cols))
                    water_map = np.zeros((rows, cols))
                    unc_map = np.zeros((rows, cols))

                    chunk_size = 256
                    for i in range(0, rows, chunk_size):
                        for j in range(0, cols, chunk_size):
                            i_end = min(i + chunk_size, rows)
                            j_end = min(j + chunk_size, cols)
                            tile = dask_arr[i:i_end, j:j_end, :].compute()
                            
                            for ti in range(i_end - i):
                                for tj in range(j_end - j):
                                    spec = tile[ti, tj, :]
                                    try:
                                        result = self.lut.invert_fast(spec, wavelengths, k=3)
                                        chl_map[i+ti, j+tj] = result['Chlorophyll_ug_cm2']
                                        lai_map[i+ti, j+tj] = result['LAI']
                                        water_map[i+ti, j+tj] = result['Water_content']
                                        unc_map[i+ti, j+tj] = result['uncertainty']['Chlorophyll']
                                    except Exception as e:
                                        chl_map[i+ti, j+tj] = np.nan

                    # Save results
                    result_path = f"{scene_id}/biophysical.npz"
                    temp_npz = os.path.join(local_dir, 'biophysical.npz')
                    np.savez_compressed(temp_npz, chlorophyll=chl_map, lai=lai_map,
                                        water=water_map, uncertainty=unc_map)
                    self.minio.fput_object("biophysical-params", result_path, temp_npz)

                    out_msg = {
                        'scene_id': scene_id,
                        'params_path': result_path,
                        'parameters': ['chlorophyll', 'lai', 'water', 'uncertainty'],
                        'timestamp': data.get('timestamp')
                    }
                    send_with_callback(self.producer, 'rtm-results', out_msg)
                    logger.info(f"RTM inversion complete for {scene_id}")

                except Exception as e:
                    logger.error(f"Error processing RTM: {e}", exc_info=True)
                finally:
                    if 'local_dir' in locals() and os.path.exists(local_dir):
                        shutil.rmtree(local_dir)
            except Exception as e:
                logger.error(f"Error handling message: {e}", exc_info=True)

if __name__ == "__main__":
    RTMInversionService().process()
