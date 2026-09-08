# =============================================================
# OWNER: AARON
# =============================================================
"""
ML Inference Microservice — Hydra Multi-Task Learning

Consumes preprocessed, band-selected hyperspectral data directly,
and runs the HydraMTLNet to generate probability maps for
agriculture, minerals, and thermal anomalies simultaneously.

Kafka topic consumed:
    'preprocessed-multiband' ← preprocessing (TEAMMATE)

Kafka topic produced:
    'ml-analyzed'        → api_gateway / frontend (AARON)

Flow:
    1. Preprocessing service publishes message (with scene_id & path to 10-band data)
    2. Download 10-band feature maps (.npy or .zarr) from MinIO
    3. Run HydraMTLNet (extracts features & branches to 3 heads)
    4. Save outputs (Agriculture params, Mineral maps, Anomaly maps) to MinIO
    5. Publish summary to Kafka 'ml-analyzed'

Performance:
    ~0.1ms per pixel on CPU (model is ~3,000 params)
"""

import os
import logging
import time
import json
import numpy as np
import torch
import tempfile
import shutil
import msgpack
from datetime import datetime
from minio import Minio
from kafka import KafkaConsumer
from shared.kafka_helpers import create_reliable_producer, send_with_callback
from shared.config import Settings
from prometheus_client import Counter, Histogram, start_http_server

from model import HydraMTLNet, INPUT_BANDS, MINERAL_CLASSES

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ml-inference] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# ── Prometheus metrics ───────────────────────────────────────────────────────
SCENES_PROCESSED = Counter(
    'ml_scenes_processed_total', 'Scenes processed', ['status']
)
PROCESSING_TIME = Histogram(
    'ml_processing_seconds', 'Processing time',
    buckets=[0.5, 1, 2, 5, 10, 30, 60]
)
INFERENCE_TIME = Histogram(
    'ml_inference_ms', 'Inference time per pixel (ms)',
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5]
)


class MLInferenceService:
    """
    Hydra-based ML inference service.
    Processes pre-selected bands through a multi-task network.
    """

    def __init__(self):
        kafka_servers = Settings.KAFKA_BOOTSTRAP_SERVERS

        # ── Kafka: consume only from the preprocessed multiband topic ────
        self.consumer = KafkaConsumer(
            'preprocessed-multiband',
            bootstrap_servers=kafka_servers,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            group_id='ml-inference-hydra-group',
            auto_offset_reset='earliest',
        )

        self.producer = create_reliable_producer(kafka_servers)

        # ── MinIO ────────────────────────────────────────────────────────
        self.minio = Settings.get_minio_client()
        for bucket in ['ml-results', 'ml-models', 'preprocessed-data']:
            if not self.minio.bucket_exists(bucket):
                self.minio.make_bucket(bucket)

        # ── Model ────────────────────────────────────────────────────────
        self.device = 'cpu'

        self.model = HydraMTLNet(
            n_features=INPUT_BANDS,
            n_mineral_classes=MINERAL_CLASSES,
        ).to(self.device)

        self._load_model_weights()

        # ── Prometheus ───────────────────────────────────────────────────
        prom_port = int(os.getenv('PROMETHEUS_PORT', '8090'))
        try:
            start_http_server(prom_port)
            logger.info("Prometheus metrics on :%d", prom_port)
        except OSError:
            logger.warning("Prometheus port %d already in use", prom_port)

        logger.info("ML Inference service initialized (Hydra MTL mode)")

    # ── Model weight loading ─────────────────────────────────────────────

    def _load_model_weights(self):
        """Load pretrained model weights from disk or MinIO."""
        weights_path = os.getenv('MODEL_WEIGHTS', 'hydra_weights.pth')

        if os.path.exists(weights_path):
            state_dict = torch.load(weights_path, map_location=self.device)
            self.model.load_state_dict(state_dict)
            logger.info("Loaded model weights from %s", weights_path)
        else:
            logger.warning(
                "Weights not found at %s — using random initialization. "
                "Run train.py first to produce trained weights.",
                weights_path,
            )

        self.model.eval()

    # ── Main loop ────────────────────────────────────────────────────────

    def process(self):
        """Main Kafka consumer loop."""
        logger.info("Listening on topic: preprocessed-multiband")

        for message in self.consumer:
            try:
                data = message.value
                scene_id = data.get('scene_id', 'unknown')
                data_path = data.get('data_path', '')

                logger.info(
                    "Received multiband data for %s (path=%s) — starting Hydra inference",
                    scene_id, data_path,
                )

                self._run_inference(scene_id, data_path)

            except Exception as exc:
                SCENES_PROCESSED.labels(status='error').inc()
                logger.error("Error: %s", exc, exc_info=True)

    # ── Inference pipeline ───────────────────────────────────────────────

    def _run_inference(self, scene_id: str, data_path: str):
        """Download multiband data from MinIO, predict, publish."""
        local_dir = tempfile.mkdtemp(prefix="ml_hydra_")
        try:
            start_time = time.time()

            # 1. Download multiband data
            bands_array, rows, cols = self._load_multiband_data(data_path, local_dir)
            if bands_array is None:
                raise ValueError(f"Failed to load data for {scene_id}")

            # 2. Reshape and Pad/Truncate if necessary
            n_pixels, n_features = bands_array.shape
            if n_features != self.model.n_features:
                logger.warning(
                    "Feature mismatch: got %d bands, model expects %d.",
                    n_features, self.model.n_features,
                )
                if n_features < self.model.n_features:
                    pad = np.zeros((n_pixels, self.model.n_features - n_features), dtype=np.float32)
                    bands_array = np.hstack([bands_array, pad])
                else:
                    bands_array = bands_array[:, :self.model.n_features]

            # 3. Run neural network
            predictions, inference_ms = self._predict(bands_array)

            agri_map = predictions['agriculture_params'].reshape(rows, cols, 4)
            mineral_map = predictions['mineral_probs'].reshape(rows, cols, -1)
            anomaly_map = predictions['anomaly_prob'].reshape(rows, cols)

            elapsed = time.time() - start_time
            PROCESSING_TIME.observe(elapsed)
            INFERENCE_TIME.observe(inference_ms)

            # 4. Save all results to a single MinIO bucket directory
            summary = self._save_results(
                scene_id, agri_map, mineral_map, anomaly_map, local_dir, elapsed, inference_ms
            )

            # 5. Publish to Kafka
            send_with_callback(self.producer, 'ml-analyzed', summary)
            SCENES_PROCESSED.labels(status='success').inc()

            logger.info(
                "Hydra ML complete for %s: %.1fs total, %.3fms/pixel",
                scene_id, elapsed, inference_ms,
            )

        except Exception as exc:
            SCENES_PROCESSED.labels(status='error').inc()
            logger.error("Inference failed for %s: %s", scene_id, exc, exc_info=True)
        finally:
            shutil.rmtree(local_dir, ignore_errors=True)

    def _load_multiband_data(self, path: str, local_dir: str):
        """Load the pre-selected bands array from MinIO."""
        if not path:
            return None, 0, 0
        try:
            local = os.path.join(local_dir, 'multiband.npy')
            self.minio.fget_object('preprocessed-data', path, local)
            data = np.load(local)
            
            # Assuming data is shaped (rows, cols, bands)
            if len(data.shape) == 3:
                rows, cols, bands = data.shape
                data_flat = data.reshape(rows * cols, bands).astype(np.float32)
                data_flat = np.nan_to_num(data_flat, nan=0.0)
                return data_flat, rows, cols
            else:
                logger.error("Unexpected data shape: %s", data.shape)
                return None, 0, 0
        except Exception as e:
            logger.warning("Could not load multiband data: %s", e)
            return None, 0, 0

    def _predict(self, x: np.ndarray):
        """Run model inference."""
        tensor = torch.FloatTensor(x).to(self.device)

        start = time.time()
        preds = self.model.predict(tensor)
        elapsed_ms = ((time.time() - start) / len(x)) * 1000

        # Move to CPU/numpy
        out = {
            'agriculture_params': preds['agriculture_params'].cpu().numpy(),
            'mineral_probs': preds['mineral_probs'].cpu().numpy(),
            'anomaly_prob': preds['anomaly_prob'].cpu().numpy(),
        }

        return out, elapsed_ms

    def _save_results(self, scene_id, agri_map, mineral_map, anomaly_map, local_dir, elapsed, inference_ms):
        """Save all multi-task output maps to MinIO."""
        
        # Save Agriculture Map (Cab, Cw, LAI, N)
        agri_path = os.path.join(local_dir, 'agriculture.npy')
        np.save(agri_path, agri_map)
        self.minio.fput_object('ml-results', f'{scene_id}/agriculture.npy', agri_path)

        # Save Mineral Map (Top probability indices to save space)
        mineral_class = np.argmax(mineral_map, axis=2)
        min_cls_path = os.path.join(local_dir, 'mineral_class.npy')
        np.save(min_cls_path, mineral_class)
        self.minio.fput_object('ml-results', f'{scene_id}/mineral_class.npy', min_cls_path)

        min_prob_path = os.path.join(local_dir, 'mineral_probabilities.npy')
        np.save(min_prob_path, np.max(mineral_map, axis=2))
        self.minio.fput_object('ml-results', f'{scene_id}/mineral_probabilities.npy', min_prob_path)

        # Save Anomaly Map
        anomaly_path = os.path.join(local_dir, 'anomaly.npy')
        np.save(anomaly_path, anomaly_map)
        self.minio.fput_object('ml-results', f'{scene_id}/anomaly.npy', anomaly_path)

        # Summary JSON
        summary = {
            'scene_id':             scene_id,
            'timestamp':            datetime.now().isoformat(),
            'shape':                list(anomaly_map.shape),
            'processing_time_sec':  round(elapsed, 2),
            'inference_ms_per_px':  round(inference_ms, 4),
            'output_layers':        ['agriculture', 'mineral_class', 'mineral_probabilities', 'anomaly']
        }

        summary_path = os.path.join(local_dir, 'summary.json')
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        self.minio.fput_object('ml-results', f'{scene_id}/summary.json', summary_path)

        return summary


if __name__ == "__main__":
    service = MLInferenceService()
    service.process()
