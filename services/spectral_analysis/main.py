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
from derivative_analysis import (first_derivative, red_edge_position,
                                  continuum_removal, absorption_depth)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SpectralAnalysisService:
    def __init__(self):
        self.consumer = KafkaConsumer(
            'preprocessed',
            bootstrap_servers=Settings.KAFKA_BOOTSTRAP_SERVERS,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            group_id='spectral-analysis-group',
            auto_offset_reset='earliest'
        )
        self.producer = create_reliable_producer(Settings.KAFKA_BOOTSTRAP_SERVERS)
        self.minio = Settings.get_minio_client()
        if not self.minio.bucket_exists("analyzed-data"):
            self.minio.make_bucket("analyzed-data")

    def analyze_pixel(self, spectrum, wavelengths):
        features = {}
        # Red edge
        rep, slope = red_edge_position(spectrum, wavelengths)
        features['red_edge_position'] = rep
        features['red_edge_slope'] = slope
        # Absorption depths
        features['chl_depth'] = absorption_depth(spectrum, wavelengths)
        features['water_depth'] = absorption_depth(spectrum, wavelengths,
                                                    center=1450, shoulder_left=1400, shoulder_right=1500)
        # NDVI
        idx_red = np.argmin(np.abs(wavelengths - 680))
        idx_nir = np.argmin(np.abs(wavelengths - 800))
        ndvi = (spectrum[idx_nir] - spectrum[idx_red]) / (spectrum[idx_nir] + spectrum[idx_red] + 1e-10)
        features['ndvi'] = ndvi
        # Brightness
        features['brightness'] = np.mean(spectrum)
        return features

    def process(self):
        logger.info("Spectral analysis service started")
        for msg in self.consumer:
            try:
                data = msg.value
                scene_id = data['scene_id']
                zarr_path = data['zarr_path']
                wavelengths = data['wavelengths']

                # Load Zarr
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

                    rows, cols, bands = dask_arr.shape
                    # Feature maps
                    ndvi_map = np.zeros((rows, cols))
                    rep_map = np.zeros((rows, cols))
                    chl_map = np.zeros((rows, cols))
                    water_map = np.zeros((rows, cols))

                    chunk_size = 256
                    for i in range(0, rows, chunk_size):
                        for j in range(0, cols, chunk_size):
                            i_end = min(i + chunk_size, rows)
                            j_end = min(j + chunk_size, cols)
                            tile = dask_arr[i:i_end, j:j_end, :].compute()
                            
                            for ti in range(i_end - i):
                                for tj in range(j_end - j):
                                    feats = self.analyze_pixel(tile[ti, tj, :], np.array(wavelengths, dtype=float))
                                    ndvi_map[i+ti, j+tj] = feats['ndvi']
                                    rep_map[i+ti, j+tj] = feats['red_edge_position']
                                    chl_map[i+ti, j+tj] = feats['chl_depth']
                                    water_map[i+ti, j+tj] = feats['water_depth']

                    # Save maps as compressed npz (or Zarr)
                    result_path = f"{scene_id}/features.npz"
                    temp_npz = os.path.join(local_dir, 'features.npz')
                    np.savez_compressed(temp_npz, ndvi=ndvi_map, red_edge=rep_map,
                                        chlorophyll=chl_map, water=water_map)

                    # Upload
                    self.minio.fput_object("analyzed-data", result_path, temp_npz)

                    out_msg = {
                        'scene_id': scene_id,
                        'feature_path': result_path,
                        'wavelengths': wavelengths,
                        'timestamp': data.get('timestamp')
                    }
                    send_with_callback(self.producer, 'analyzed', out_msg)
                    logger.info(f"Analyzed {scene_id}")

                except Exception as e:
                    logger.error(f"Error processing spectral analysis: {e}", exc_info=True)
                finally:
                    if 'local_dir' in locals() and os.path.exists(local_dir):
                        shutil.rmtree(local_dir)
            except Exception as e:
                logger.error(f"Error handling message: {e}", exc_info=True)

if __name__ == "__main__":
    SpectralAnalysisService().process()
