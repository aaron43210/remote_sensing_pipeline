# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Mineral Analysis Microservice
"""
import os
import logging
import numpy as np
import zarr
import tempfile
import shutil
import msgpack
import json
import time
from datetime import datetime
from minio import Minio
from kafka import KafkaConsumer
from shared.kafka_helpers import create_reliable_producer, send_with_callback
from shared.config import Settings

from classifier import MineralClassifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

class MineralAnalysisService:
    def __init__(self):
        kafka_servers = Settings.KAFKA_BOOTSTRAP_SERVERS
        self.consumer = KafkaConsumer(
            'preprocessed',
            bootstrap_servers=kafka_servers,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            group_id='mineral-analysis-group',
            auto_offset_reset='earliest'
        )
        self.producer = create_reliable_producer(kafka_servers)
        self.minio = Settings.get_minio_client()
        if not self.minio.bucket_exists("mineral-results"):
            self.minio.make_bucket("mineral-results")

        self.classifier = MineralClassifier()
        logger.info("Mineral analysis service initialized")

    def process_scene(self, local_zarr_path, wavelengths):
        import dask.array as da
        start_t = time.time()
        wvl = np.array(wavelengths, dtype=float)
        if self.classifier.sensor_wavelengths is None or len(self.classifier.sensor_wavelengths) != len(wvl):
            self.classifier.initialize_for_sensor(wvl)
            
        z = zarr.open(local_zarr_path, mode='r')
        dask_arr = da.from_zarr(z)
        rows, cols, bands = dask_arr.shape
        
        names = sorted(self.classifier.aligned_library.keys())
        
        mineral_map = np.empty((rows, cols), dtype=object)
        class_indices = np.zeros((rows, cols), dtype=np.int32)
        abund_maps = {n: np.zeros((rows, cols), dtype=np.float32) for n in names}
        confidence_map = np.zeros((rows, cols), dtype=np.float32)
        angle_map = np.zeros((rows, cols), dtype=np.float32)
        error_map = np.zeros((rows, cols), dtype=np.float32)
        quality_map = np.zeros((rows, cols), dtype=np.float32)

        chunk_size = 256
        for i in range(0, rows, chunk_size):
            for j in range(0, cols, chunk_size):
                i_end = min(i + chunk_size, rows)
                j_end = min(j + chunk_size, cols)
                
                tile = dask_arr[i:i_end, j:j_end, :].compute()
                res = self.classifier.analyze_scene(tile, wvl)
                
                mineral_map[i:i_end, j:j_end] = res["classification"]["mineral_map"]
                class_indices[i:i_end, j:j_end] = res["classification"]["class_indices"]
                for n in names:
                    abund_maps[n][i:i_end, j:j_end] = res["abundance"][n]
                confidence_map[i:i_end, j:j_end] = res["confidence"]
                angle_map[i:i_end, j:j_end] = res["angle_map"]
                error_map[i:i_end, j:j_end] = res["reconstruction_error"]
                quality_map[i:i_end, j:j_end] = res["quality"]
                
        return {
            "classification": {
                "mineral_map": mineral_map,
                "class_indices": class_indices,
                "mineral_names": names
            },
            "abundance": abund_maps,
            "confidence": confidence_map,
            "angle_map": angle_map,
            "reconstruction_error": error_map,
            "quality": quality_map,
            "processing_time_sec": time.time() - start_t,
            "pixels_processed": rows * cols
        }

    def save_results(self, scene_id, results, local_dir):
        class_map = results["classification"]["mineral_map"]
        class_path = os.path.join(local_dir, "mineral_class.npy")
        np.save(class_path, class_map)
        self.minio.fput_object("mineral-results", f"{scene_id}/mineral_class.npy", class_path)

        abund_path = os.path.join(local_dir, "abundances.npz")
        np.savez_compressed(abund_path, **results["abundance"])
        self.minio.fput_object("mineral-results", f"{scene_id}/abundances.npz", abund_path)

        conf_path = os.path.join(local_dir, "confidence.npy")
        np.save(conf_path, results["confidence"])
        self.minio.fput_object("mineral-results", f"{scene_id}/confidence.npy", conf_path)

        err_path = os.path.join(local_dir, "recon_error.npy")
        np.save(err_path, results["reconstruction_error"])
        self.minio.fput_object("mineral-results", f"{scene_id}/recon_error.npy", err_path)

        summary = {
            "scene_id": scene_id,
            "timestamp": datetime.now().isoformat(),
            "shape": list(class_map.shape),
            "minerals_found": list(np.unique(class_map)),
            "mean_confidence": float(np.nanmean(results["confidence"])),
            "mean_error": float(np.nanmean(results["reconstruction_error"])),
            "processing_time_sec": results["processing_time_sec"],
            "pixels_processed": results["pixels_processed"],
            "quality": results["quality"]
        }
        summary_path = os.path.join(local_dir, "summary.json")
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
        self.minio.fput_object("mineral-results", f"{scene_id}/summary.json", summary_path)
        return summary

    def process(self):
        logger.info("Mineral analysis service listening on 'preprocessed'")
        for message in self.consumer:
            try:
                data = message.value
                scene_id = data['scene_id']
                tasks = data.get('tasks', ["agriculture", "mineral"])
                
                if "mineral" not in tasks:
                    logger.info(f"Skipping Mineral Analysis for {scene_id}... (Not requested)")
                    continue
                    
                zarr_path = data['zarr_path']
                wavelengths = data['wavelengths']

                logger.info(f"Processing minerals for {scene_id}")

                local_dir = tempfile.mkdtemp()
                local_zarr = os.path.join(local_dir, 'data.zarr')
                objects = self.minio.list_objects("preprocessed-data", prefix=zarr_path, recursive=True)
                for obj in objects:
                    obj_name = obj.object_name
                    rel = os.path.relpath(obj_name, zarr_path)
                    local_file = os.path.join(local_zarr, rel)
                    os.makedirs(os.path.dirname(local_file), exist_ok=True)
                    self.minio.fget_object("preprocessed-data", obj_name, local_file)

                results = self.process_scene(local_zarr, wavelengths)
                summary = self.save_results(scene_id, results, local_dir)

                out_message = {
                    'scene_id': scene_id,
                    'result_path': f"{scene_id}/",
                    'minerals_found': summary['minerals_found'],
                    'mean_confidence': summary['mean_confidence'],
                    'mean_error': summary['mean_error'],
                    'processing_time_sec': summary['processing_time_sec'],
                    'timestamp': summary['timestamp']
                }

                send_with_callback(self.producer, 'mineral-analyzed', out_message)
                logger.info(f"Mineral analysis complete for {scene_id}")
            except Exception as e:
                logger.error(f"Error processing {data.get('scene_id', 'unknown')}: {e}", exc_info=True)
            finally:
                if 'local_dir' in locals() and os.path.exists(local_dir):
                    shutil.rmtree(local_dir)

if __name__ == "__main__":
    service = MineralAnalysisService()
    service.process()
