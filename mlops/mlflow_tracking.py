# =============================================================
# OWNER: AARON
# =============================================================
import mlflow
import mlflow.pytorch
import torch
import numpy as np
import logging
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HyperspectralMLflowTracker:
    """
    MLflow experiment tracking for hyperspectral models.
    Handles experiment logging, model registry, and versioning.
    """

    def __init__(self, tracking_uri="http://mlflow:5000"):
        mlflow.set_tracking_uri(tracking_uri)
        self.tracking_uri = tracking_uri

    def start_experiment(self, experiment_name="hyperspectral_physics_nn"):
        mlflow.set_experiment(experiment_name)
        return mlflow.start_run(run_name=f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    def log_physics_features(self, feature_names, feature_importance):
        """Log which physics features matter most."""
        for name, importance in zip(feature_names, feature_importance):
            mlflow.log_metric(f"feature_importance_{name}", importance)

    def log_training_epoch(self, epoch, train_loss, val_loss, train_acc, val_acc,
                           physics_loss=None):
        mlflow.log_metrics({
            "train_loss": train_loss,
            "val_loss": val_loss,
            "train_accuracy": train_acc,
            "val_accuracy": val_acc,
            "epoch": epoch
        }, step=epoch)

        if physics_loss is not None:
            mlflow.log_metric("physics_loss", physics_loss, step=epoch)

    def log_model_params(self, model_config):
        mlflow.log_params({
            "n_bands": model_config.get("n_bands"),
            "n_classes": model_config.get("n_classes"),
            "embedding_dim": model_config.get("embedding_dim"),
            "lambda_physics": model_config.get("lambda_physics"),
            "learning_rate": model_config.get("learning_rate"),
            "batch_size": model_config.get("batch_size"),
            "model_type": "PhysicsGuidedLightweightNet"
        })

    def log_benchmark_results(self, benchmark_results):
        """Log comparison between physics, lightweight NN, CNN."""
        for model_name, metrics in benchmark_results.items():
            for metric_name, value in metrics.items():
                mlflow.log_metric(f"{model_name}_{metric_name}", value)

    def register_model(self, model, model_name="hyperspectral_lightweight"):
        """Register model to MLflow model registry."""
        mlflow.pytorch.log_model(
            model,
            artifact_path="model",
            registered_model_name=model_name
        )
        logger.info(f"Model registered as: {model_name}")

    def load_production_model(self, model_name="hyperspectral_lightweight",
                               stage="Production"):
        """Load the current production model."""
        model_uri = f"models:/{model_name}/{stage}"
        model = mlflow.pytorch.load_model(model_uri)
        logger.info(f"Loaded production model: {model_uri}")
        return model

    def transition_model_to_production(self, model_name, version):
        """Promote a model version to production."""
        client = mlflow.tracking.MlflowClient()
        client.transition_model_version_stage(
            name=model_name,
            version=version,
            stage="Production"
        )
        logger.info(f"Model {model_name} v{version} promoted to Production")

    def compare_runs(self, experiment_name="hyperspectral_physics_nn", metric="val_accuracy"):
        """Compare all runs in an experiment."""
        client = mlflow.tracking.MlflowClient()
        experiment = client.get_experiment_by_name(experiment_name)

        if not experiment:
            logger.warning(f"Experiment {experiment_name} not found")
            return []

        runs = client.search_runs(
            experiment_ids=[experiment.experiment_id],
            order_by=[f"metrics.{metric} DESC"]
        )

        results = []
        for run in runs:
            results.append({
                "run_id": run.info.run_id,
                "metric": run.data.metrics.get(metric, 0),
                "params": run.data.params,
                "status": run.info.status
            })

        return results
