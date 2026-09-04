# =============================================================
# OWNER: AARON
# =============================================================
"""
ML Inference Microservice — Fusion + Prediction

Consumes results from ALL domain services via Kafka, fuses them,
and runs the lightweight neural network to generate probability maps.

Kafka topics consumed:
    'analyzed'           ← spectral_analysis (LANKAPRIYA)
    'rtm-results'        ← rtm_inversion (LANKAPRIYA)
    'mineral-analyzed'   ← mineral_analysis (ANANTHAN S & HARIKRISHNAN)
    'thermal-processed'  ← thermal (BAINTY KAUR)

Kafka topic produced:
    'ml-analyzed'        → api_gateway / frontend (AARON)

Flow:
    1. Each domain service publishes its results (with scene_id)
    2. This service tracks arrivals in Redis
    3. When all requested tasks for a scene_id have arrived:
       a. Download feature maps (.npz) from MinIO
       b. Fuse into per-pixel feature vectors
       c. Run FusionLightweightNet → probability map
       d. Save results to MinIO
       e. Publish summary to Kafka 'ml-analyzed'

Performance:
    ~0.1ms per pixel on CPU (model is only ~2,000 params)
    ~2.5 seconds for 512×512 scene
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
import redis
from datetime import datetime
from minio import Minio
from kafka import KafkaConsumer
from shared.kafka_helpers import create_reliable_producer, send_with_callback
from shared.config import Settings
from prometheus_client import Counter, Histogram, Gauge, start_http_server

from model import (
    FusionLightweightNet,
    fuse_service_outputs,
    TOTAL_FEATURES_HSI,
    TOTAL_FEATURES_ALL,
)

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


# ── Kafka topic → MinIO bucket mapping ──────────────────────────────────────
TOPIC_CONFIG = {
    'analyzed': {
        'bucket': 'analyzed-data',
        'redis_key': 'spectral',
        'path_field': 'feature_path',
    },
    'rtm-results': {
        'bucket': 'biophysical-params',
        'redis_key': 'rtm',
        'path_field': 'params_path',
    },
    'mineral-analyzed': {
        'bucket': 'mineral-results',
        'redis_key': 'mineral',
        'path_field': 'result_path',
    },
    'thermal-processed': {
        'bucket': 'thermal-processed',
        'redis_key': 'thermal',
        'path_field': 'products_path',
    },
}


class MLInferenceService:
    """
    Fusion-based ML inference service.

    Waits for domain services to finish, fuses their outputs,
    and runs the lightweight neural network.
    """

    def __init__(self):
        kafka_servers = Settings.KAFKA_BOOTSTRAP_SERVERS

        # ── Kafka: consume from all domain result topics ─────────────────
        self.consumer = KafkaConsumer(
            *TOPIC_CONFIG.keys(),
            bootstrap_servers=kafka_servers,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            group_id='ml-inference-group',
            auto_offset_reset='earliest',
        )

        self.producer = create_reliable_producer(kafka_servers)

        # ── MinIO ────────────────────────────────────────────────────────
        self.minio = Settings.get_minio_client()
        for bucket in ['ml-results', 'ml-models']:
            if not self.minio.bucket_exists(bucket):
                self.minio.make_bucket(bucket)

        # ── Redis: track which services have finished per scene ──────────
        self.redis = redis.Redis(
            host=Settings.REDIS_HOST,
            port=Settings.REDIS_PORT,
            decode_responses=True,
        )

        # ── Model ────────────────────────────────────────────────────────
        self.device = 'cpu'
        self.n_classes = 16

        # Default: HSI-only features (no thermal)
        self.model = FusionLightweightNet(
            n_features=TOTAL_FEATURES_HSI,
            n_classes=self.n_classes,
        ).to(self.device)

        self._load_model_weights()

        # ── Prometheus ───────────────────────────────────────────────────
        prom_port = int(os.getenv('PROMETHEUS_PORT', '8090'))
        try:
            start_http_server(prom_port)
            logger.info("Prometheus metrics on :%d", prom_port)
        except OSError:
            logger.warning("Prometheus port %d already in use", prom_port)

        logger.info("ML Inference service initialized (fusion mode)")

    # ── Model weight loading ─────────────────────────────────────────────

    def _load_model_weights(self):
        """Load pretrained model weights from disk or MinIO."""
        weights_path = os.getenv('MODEL_WEIGHTS', 'model_weights.pth')

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
        logger.info(
            "Listening on topics: %s",
            list(TOPIC_CONFIG.keys()),
        )

        for message in self.consumer:
            try:
                topic = message.topic
                data  = message.value

                scene_id = data.get('scene_id', 'unknown')
                config   = TOPIC_CONFIG.get(topic)

                if config is None:
                    continue

                # Store result path in Redis
                redis_key      = f"ml:scene:{scene_id}"
                result_path    = data.get(config['path_field'], '')
                service_key    = config['redis_key']

                self.redis.hset(redis_key, service_key, result_path)
                self.redis.expire(redis_key, 3600)  # 1 hour TTL

                logger.info(
                    "Received %s result for %s (path=%s)",
                    service_key, scene_id, result_path,
                )

                # Check if all required services have reported
                tasks = self._get_required_tasks(scene_id)
                arrived = self.redis.hgetall(redis_key)

                if self._all_tasks_ready(tasks, arrived):
                    logger.info(
                        "All tasks ready for %s: %s — starting fusion",
                        scene_id, list(arrived.keys()),
                    )
                    self._run_fusion(scene_id, arrived)

                    # Cleanup Redis
                    self.redis.delete(redis_key)

            except Exception as exc:
                SCENES_PROCESSED.labels(status='error').inc()
                logger.error("Error: %s", exc, exc_info=True)

    # ── Fusion pipeline ──────────────────────────────────────────────────

    def _run_fusion(self, scene_id: str, arrived: dict):
        """Download results from MinIO, fuse, predict, publish."""
        local_dir = tempfile.mkdtemp(prefix="ml_fusion_")
        try:
            start_time = time.time()

            # 1. Download feature maps from each service
            spectral = self._load_spectral(arrived.get('spectral'), local_dir)
            rtm      = self._load_rtm(arrived.get('rtm'), local_dir)
            mineral  = self._load_mineral(arrived.get('mineral'), local_dir)
            thermal  = self._load_thermal(arrived.get('thermal'), local_dir)

            # Determine scene dimensions from first available result
            rows, cols = self._get_scene_shape(spectral, rtm, mineral)

            # 2. Fuse into per-pixel feature vectors
            include_thermal = thermal is not None
            fused = fuse_service_outputs(
                spectral=spectral, rtm=rtm, mineral=mineral,
                thermal=thermal, rows=rows, cols=cols,
            )

            # 3. Run neural network
            n_features = fused.shape[1]
            if n_features != self.model.n_features:
                logger.warning(
                    "Feature mismatch: got %d, model expects %d. "
                    "Padding/truncating.",
                    n_features, self.model.n_features,
                )
                if n_features < self.model.n_features:
                    pad = np.zeros(
                        (fused.shape[0], self.model.n_features - n_features),
                        dtype=np.float32,
                    )
                    fused = np.hstack([fused, pad])
                else:
                    fused = fused[:, :self.model.n_features]

            predictions, proba, inference_ms = self._predict(fused)

            class_map = predictions.reshape(rows, cols)
            proba_map = proba.reshape(rows, cols, -1)

            elapsed = time.time() - start_time
            PROCESSING_TIME.observe(elapsed)
            INFERENCE_TIME.observe(inference_ms)

            # 4. Save results to MinIO
            summary = self._save_results(
                scene_id, class_map, proba_map, local_dir, elapsed, inference_ms,
            )

            # 5. Publish to Kafka
            send_with_callback(self.producer, 'ml-analyzed', summary)
            SCENES_PROCESSED.labels(status='success').inc()

            logger.info(
                "ML complete for %s: %d classes, %.1fs total, %.3fms/pixel",
                scene_id,
                summary['n_classes_predicted'],
                elapsed,
                inference_ms,
            )

        except Exception as exc:
            SCENES_PROCESSED.labels(status='error').inc()
            logger.error("Fusion failed for %s: %s", scene_id, exc, exc_info=True)
        finally:
            shutil.rmtree(local_dir, ignore_errors=True)

    def _predict(self, fused: np.ndarray):
        """Run model inference on fused feature array."""
        tensor = torch.FloatTensor(fused).to(self.device)

        start = time.time()
        with torch.no_grad():
            logits = self.model(tensor)
            proba  = torch.softmax(logits, dim=1)

        elapsed_ms = ((time.time() - start) / len(fused)) * 1000

        predictions = logits.argmax(dim=1).cpu().numpy()
        proba_np    = proba.cpu().numpy()

        return predictions, proba_np, elapsed_ms

    # ── MinIO loaders ────────────────────────────────────────────────────

    def _load_spectral(self, path: str, local_dir: str):
        if not path:
            return None
        try:
            local = os.path.join(local_dir, 'spectral.npz')
            self.minio.fget_object('analyzed-data', path, local)
            data = np.load(local)
            return {k: data[k] for k in data.files}
        except Exception as e:
            logger.warning("Could not load spectral: %s", e)
            return None

    def _load_rtm(self, path: str, local_dir: str):
        if not path:
            return None
        try:
            local = os.path.join(local_dir, 'rtm.npz')
            self.minio.fget_object('biophysical-params', path, local)
            data = np.load(local)
            return {k: data[k] for k in data.files}
        except Exception as e:
            logger.warning("Could not load RTM: %s", e)
            return None

    def _load_mineral(self, path: str, local_dir: str):
        if not path:
            return None
        try:
            # Mineral saves multiple files: abundances.npz, confidence.npy, etc.
            prefix = path.rstrip('/')
            result = {}

            # Abundances
            abund_local = os.path.join(local_dir, 'abundances.npz')
            try:
                self.minio.fget_object(
                    'mineral-results', f"{prefix}/abundances.npz", abund_local
                )
                abund_data = np.load(abund_local)
                result['abundances'] = {k: abund_data[k] for k in abund_data.files}
            except Exception:
                result['abundances'] = {}

            # Confidence
            conf_local = os.path.join(local_dir, 'confidence.npy')
            try:
                self.minio.fget_object(
                    'mineral-results', f"{prefix}/confidence.npy", conf_local
                )
                result['confidence'] = np.load(conf_local)
            except Exception:
                pass

            return result if result.get('abundances') else None
        except Exception as e:
            logger.warning("Could not load mineral: %s", e)
            return None

    def _load_thermal(self, path: str, local_dir: str):
        if not path:
            return None
        try:
            # Thermal saves summary.json with scalar stats
            prefix = path.rstrip('/')
            summary_local = os.path.join(local_dir, 'thermal_summary.json')
            self.minio.fget_object(
                'thermal-results', f"{prefix}/summary.json", summary_local
            )
            with open(summary_local) as f:
                summary = json.load(f)
            return {
                'mean_lst':       summary.get('statistics', {}).get('mean_temp_c', 0),
                'uhi_intensity':  summary.get('uhi', {}).get('uhi_intensity_c', 0),
                'n_hotspots':     len(summary.get('hotspots', [])),
            }
        except Exception as e:
            logger.warning("Could not load thermal: %s", e)
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_scene_shape(self, spectral, rtm, mineral):
        """Determine scene (rows, cols) from first available result."""
        for data in [spectral, rtm]:
            if data is not None:
                for key, arr in data.items():
                    if hasattr(arr, 'shape') and len(arr.shape) == 2:
                        return arr.shape
        # Fallback
        if mineral and 'confidence' in mineral:
            return mineral['confidence'].shape
        return (512, 512)

    def _get_required_tasks(self, scene_id: str) -> set:
        """
        Determine which tasks are required for this scene.
        For now, require spectral + rtm + mineral (HSI core).
        Thermal is optional bonus.
        """
        return {'spectral', 'rtm', 'mineral'}

    def _all_tasks_ready(self, required: set, arrived: dict) -> bool:
        """Check if all required services have reported."""
        return required.issubset(set(arrived.keys()))

    def _save_results(self, scene_id, class_map, proba_map, local_dir,
                      elapsed, inference_ms):
        """Save classification and probability maps to MinIO."""
        # Classification map
        cls_path = os.path.join(local_dir, 'classification.npy')
        np.save(cls_path, class_map)
        self.minio.fput_object(
            'ml-results', f'{scene_id}/classification.npy', cls_path
        )

        # Probability map (top-3 classes to save space)
        top3_idx  = np.argsort(proba_map, axis=2)[:, :, -3:]
        top3_prob = np.take_along_axis(proba_map, top3_idx, axis=2)
        prob_path = os.path.join(local_dir, 'probabilities.npz')
        np.savez_compressed(
            prob_path, top3_indices=top3_idx, top3_proba=top3_prob
        )
        self.minio.fput_object(
            'ml-results', f'{scene_id}/probabilities.npz', prob_path
        )

        # Summary JSON
        summary = {
            'scene_id':             scene_id,
            'timestamp':            datetime.now().isoformat(),
            'shape':                list(class_map.shape),
            'n_classes_predicted':  int(len(np.unique(class_map))),
            'processing_time_sec':  round(elapsed, 2),
            'inference_ms_per_px':  round(inference_ms, 4),
        }

        summary_path = os.path.join(local_dir, 'summary.json')
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        self.minio.fput_object(
            'ml-results', f'{scene_id}/summary.json', summary_path
        )

        return summary


if __name__ == "__main__":
    service = MLInferenceService()
    service.process()
