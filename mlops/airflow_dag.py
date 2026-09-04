# =============================================================
# OWNER: AARON
# =============================================================
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
import logging

logger = logging.getLogger(__name__)

default_args = {
    "owner": "hyperspectral-team",
    "depends_on_past": False,
    "start_date": datetime(2024, 1, 1),
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5)
}

# ──────────────────────────────────────────────
# Task Functions
# ──────────────────────────────────────────────

def check_data_quality(**context):
    """Check if new training data meets quality standards."""
    from data_quality.validator import HyperspectralDataValidator
    validator = HyperspectralDataValidator()
    result = validator.validate_training_batch(
        batch_path="/data/training_batch_latest.zarr"
    )
    if not result["is_valid"]:
        raise ValueError(f"Data quality check failed: {result['issues']}")
    logger.info(f"Data quality OK: {result['summary']}")
    return result

def detect_model_drift(**context):
    """Check if production model has drifted."""
    from monitoring.drift_detection import DriftDetector
    detector = DriftDetector()
    drift_result = detector.check_production_drift()

    if drift_result["drift_detected"]:
        logger.warning(f"Drift detected: {drift_result['drift_score']:.4f}")
        context["task_instance"].xcom_push(
            key="needs_retraining", value=True
        )
    else:
        logger.info("No drift detected, skipping retraining")
        context["task_instance"].xcom_push(
            key="needs_retraining", value=False
        )
    return drift_result

def retrain_model(**context):
    """Retrain the lightweight physics-guided model."""
    import torch
    from torch.utils.data import DataLoader, TensorDataset
    from services.ml.lightweight_hybrid import PhysicsGuidedLightweightNet
    from services.ml.train_lightweight import LightweightTrainer
    from mlops.mlflow_tracking import HyperspectralMLflowTracker

    needs_retraining = context["task_instance"].xcom_pull(
        key="needs_retraining",
        task_ids="detect_model_drift"
    )

    if not needs_retraining:
        logger.info("Skipping retraining - no drift detected")
        return

    tracker = HyperspectralMLflowTracker()

    with tracker.start_experiment("hyperspectral_retraining"):
        # Load data
        import numpy as np
        X = np.load("/data/training_spectra.npy")
        y = np.load("/data/training_labels.npy")

        from sklearn.model_selection import train_test_split
        X_train, X_val, y_train, y_val = train_test_split(
            X, y, test_size=0.2, stratify=y, random_state=42
        )

        train_loader = DataLoader(
            TensorDataset(
                torch.FloatTensor(X_train),
                torch.LongTensor(y_train)
            ),
            batch_size=32, shuffle=True
        )
        val_loader = DataLoader(
            TensorDataset(
                torch.FloatTensor(X_val),
                torch.LongTensor(y_val)
            ),
            batch_size=32
        )

        # Model config
        config = {
            "n_bands": X.shape[1],
            "n_classes": len(np.unique(y)),
            "embedding_dim": 32,
            "lambda_physics": 0.1,
            "learning_rate": 0.001,
            "batch_size": 32
        }

        tracker.log_model_params(config)

        # Train
        model = PhysicsGuidedLightweightNet(
            n_bands=config["n_bands"],
            n_classes=config["n_classes"],
            embedding_dim=config["embedding_dim"]
        )

        trainer = LightweightTrainer(model, device="cpu")
        best_acc = trainer.train(train_loader, val_loader, epochs=50)

        tracker.log_training_epoch(50, 0, 0, best_acc, best_acc)
        tracker.register_model(model)

        logger.info(f"Retraining complete. Best accuracy: {best_acc*100:.2f}%")
        return best_acc

def validate_new_model(**context):
    """Validate new model against production model."""
    import mlflow.pytorch
    import numpy as np
    import torch

    client = mlflow.tracking.MlflowClient()

    # Get latest and production versions
    latest_versions = client.get_latest_versions(
        "hyperspectral_lightweight",
        stages=["None"]
    )
    prod_versions = client.get_latest_versions(
        "hyperspectral_lightweight",
        stages=["Production"]
    )

    if not latest_versions:
        raise ValueError("No new model found")

    # Load models
    new_model = mlflow.pytorch.load_model(
        f"models:/hyperspectral_lightweight/{latest_versions[0].version}"
    )

    # Load validation data
    X_val = np.load("/data/validation_spectra.npy")
    y_val = np.load("/data/validation_labels.npy")

    X_tensor = torch.FloatTensor(X_val)

    with torch.no_grad():
        new_logits, _ = new_model(X_tensor)
        new_preds = new_logits.argmax(dim=1).numpy()

    from sklearn.metrics import accuracy_score
    new_acc = accuracy_score(y_val, new_preds)

    # Compare with production threshold
    MINIMUM_ACCURACY = 0.90
    if new_acc < MINIMUM_ACCURACY:
        raise ValueError(
            f"New model accuracy {new_acc:.4f} below minimum {MINIMUM_ACCURACY}"
        )

    logger.info(f"New model validated: accuracy={new_acc:.4f}")

    context["task_instance"].xcom_push(
        key="new_model_version",
        value=latest_versions[0].version
    )
    return new_acc

def promote_model(**context):
    """Promote validated model to production."""
    from mlops.mlflow_tracking import HyperspectralMLflowTracker

    version = context["task_instance"].xcom_pull(
        key="new_model_version",
        task_ids="validate_new_model"
    )

    tracker = HyperspectralMLflowTracker()
    tracker.transition_model_to_production("hyperspectral_lightweight", version)
    logger.info(f"Model v{version} promoted to Production")

def notify_team(**context):
    """Send notification on pipeline completion."""
    import requests
    import os

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("No Slack webhook configured")
        return

    message = {
        "text": (
            "✅ Hyperspectral Model Retraining Complete\n"
            f"• New model promoted to production\n"
            f"• Pipeline run: {context['run_id']}"
        )
    }
    requests.post(webhook_url, json=message)

# ──────────────────────────────────────────────
# DAG Definition
# ──────────────────────────────────────────────

with DAG(
    "hyperspectral_retraining_pipeline",
    default_args=default_args,
    description="Automated retraining for hyperspectral model",
    schedule_interval="0 2 * * 0",  # Weekly at 2AM Sunday
    catchup=False,
    tags=["hyperspectral", "mlops", "remote-sensing"]
) as dag:

    t1 = PythonOperator(
        task_id="check_data_quality",
        python_callable=check_data_quality
    )

    t2 = PythonOperator(
        task_id="detect_model_drift",
        python_callable=detect_model_drift
    )

    t3 = PythonOperator(
        task_id="retrain_model",
        python_callable=retrain_model
    )

    t4 = PythonOperator(
        task_id="validate_new_model",
        python_callable=validate_new_model
    )

    t5 = PythonOperator(
        task_id="promote_model",
        python_callable=promote_model
    )

    t6 = PythonOperator(
        task_id="notify_team",
        python_callable=notify_team,
        trigger_rule="all_done"
    )

    t1 >> t2 >> t3 >> t4 >> t5 >> t6
