# =============================================================
# OWNER: AARON
# =============================================================
"""
Training script for FusionLightweightNet.

Trains the lightweight neural network on fused physics features.
Supports two data sources:
    1. Indian Pines benchmark (default, for initial training)
    2. Production .npz files (real pipeline output)

Usage:
    # Train on Indian Pines benchmark (initial model)
    python train.py --data-dir ../../datasets/ --epochs 50

    # Train on production data
    python train.py --data-dir /path/to/fused_npz/ --mode production --epochs 100

    # Resume training from existing weights
    python train.py --resume model_weights.pth --epochs 20

Outputs:
    - model_weights.pth           (saved locally)
    - MLflow experiment tracking   (if MLflow server is running)
"""

import os
import sys
import argparse
import logging
import time
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [train] %(levelname)s: %(message)s',
)
logger = logging.getLogger(__name__)

# Add parent paths for imports
sys.path.insert(0, os.path.dirname(__file__))

from model import FusionLightweightNet, PhysicsConsistencyLoss, TOTAL_FEATURES_HSI
from dataset import IndianPinesDataset, FusedFeatureDataset


def train(args):
    """Main training function."""

    # ── Device ───────────────────────────────────────────────────────────
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    logger.info("Device: %s", device)

    # ── Dataset ──────────────────────────────────────────────────────────
    if args.mode == 'benchmark':
        logger.info("Loading Indian Pines benchmark dataset from %s", args.data_dir)
        dataset = IndianPinesDataset(data_dir=args.data_dir)
    else:
        logger.info("Loading production fused data from %s", args.data_dir)
        dataset = FusedFeatureDataset(npz_dir=args.data_dir)

    n_features = dataset.features.shape[1]
    n_classes  = int(dataset.labels.max()) + 1

    # Train/val split (80/20)
    n_total = len(dataset)
    n_train = int(0.8 * n_total)
    n_val   = n_total - n_train
    train_ds, val_ds = random_split(dataset, [n_train, n_val])

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_loader   = DataLoader(val_ds,   batch_size=args.batch_size, shuffle=False)

    logger.info(
        "Dataset: %d total, %d train, %d val, %d features, %d classes",
        n_total, n_train, n_val, n_features, n_classes,
    )

    # ── Model ────────────────────────────────────────────────────────────
    model = FusionLightweightNet(
        n_features=n_features,
        n_classes=n_classes,
    ).to(device)

    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    logger.info("Model parameters: %d", total_params)

    # Resume from checkpoint if requested
    if args.resume and os.path.exists(args.resume):
        state_dict = torch.load(args.resume, map_location=device)
        model.load_state_dict(state_dict)
        logger.info("Resumed from %s", args.resume)

    # ── Optimizer + Loss ─────────────────────────────────────────────────
    optimizer = torch.optim.Adam(
        model.parameters(), lr=args.lr, weight_decay=1e-4,
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='min', factor=0.5, patience=5,
    )
    loss_fn = PhysicsConsistencyLoss(lambda_physics=args.lambda_physics)

    # ── MLflow tracking (optional) ───────────────────────────────────────
    mlflow_available = False
    try:
        import mlflow
        mlflow_available = True
        mlflow.set_experiment("hyperspectral-fusion-model")
        mlflow.start_run(run_name=f"train_{time.strftime('%Y%m%d_%H%M%S')}")
        mlflow.log_params({
            'n_features':     n_features,
            'n_classes':      n_classes,
            'total_params':   total_params,
            'batch_size':     args.batch_size,
            'lr':             args.lr,
            'lambda_physics': args.lambda_physics,
            'epochs':         args.epochs,
            'mode':           args.mode,
        })
        logger.info("MLflow tracking enabled")
    except ImportError:
        logger.info("MLflow not installed — training without experiment tracking")
    except Exception as e:
        logger.warning("MLflow init failed: %s — continuing without tracking", e)

    # ── Training loop ────────────────────────────────────────────────────
    best_val_acc = 0.0
    best_val_loss = float('inf')

    for epoch in range(1, args.epochs + 1):
        # ── Train ────────────────────────────────────────────────────────
        model.train()
        train_loss  = 0.0
        train_correct = 0
        train_total   = 0

        for features, labels in train_loader:
            features = features.to(device)
            labels   = labels.to(device)

            optimizer.zero_grad()

            logits, params = model(features, return_params=True)
            loss, loss_details = loss_fn(logits, params, labels)

            loss.backward()
            optimizer.step()

            train_loss += loss.item() * len(labels)
            preds = logits.argmax(dim=1)
            train_correct += (preds == labels).sum().item()
            train_total   += len(labels)

        train_loss /= train_total
        train_acc   = train_correct / train_total

        # ── Validate ─────────────────────────────────────────────────────
        model.eval()
        val_loss  = 0.0
        val_correct = 0
        val_total   = 0

        with torch.no_grad():
            for features, labels in val_loader:
                features = features.to(device)
                labels   = labels.to(device)

                logits, params = model(features, return_params=True)
                loss, _ = loss_fn(logits, params, labels)

                val_loss += loss.item() * len(labels)
                preds = logits.argmax(dim=1)
                val_correct += (preds == labels).sum().item()
                val_total   += len(labels)

        val_loss /= val_total
        val_acc   = val_correct / val_total

        scheduler.step(val_loss)
        current_lr = optimizer.param_groups[0]['lr']

        # ── Log ──────────────────────────────────────────────────────────
        logger.info(
            "Epoch %3d/%d | Train: loss=%.4f acc=%.4f | Val: loss=%.4f acc=%.4f | lr=%.6f",
            epoch, args.epochs,
            train_loss, train_acc,
            val_loss, val_acc,
            current_lr,
        )

        if mlflow_available:
            try:
                mlflow.log_metrics({
                    'train_loss': train_loss,
                    'train_acc':  train_acc,
                    'val_loss':   val_loss,
                    'val_acc':    val_acc,
                    'lr':         current_lr,
                }, step=epoch)
            except Exception:
                pass

        # ── Save best model ──────────────────────────────────────────────
        if val_acc > best_val_acc:
            best_val_acc  = val_acc
            best_val_loss = val_loss
            torch.save(model.state_dict(), args.output)
            logger.info(
                "  → New best model saved: val_acc=%.4f (loss=%.4f)",
                val_acc, val_loss,
            )

    # ── Final summary ────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Training complete!")
    logger.info("  Best validation accuracy: %.4f", best_val_acc)
    logger.info("  Best validation loss:     %.4f", best_val_loss)
    logger.info("  Model saved to:           %s", args.output)
    logger.info("  Total parameters:         %d", total_params)
    logger.info("=" * 60)

    if mlflow_available:
        try:
            mlflow.log_metrics({
                'best_val_acc':  best_val_acc,
                'best_val_loss': best_val_loss,
            })
            mlflow.log_artifact(args.output)
            mlflow.end_run()
        except Exception:
            pass

    return best_val_acc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Train FusionLightweightNet for hyperspectral classification'
    )
    parser.add_argument(
        '--data-dir', type=str, default='../../datasets/',
        help='Path to dataset directory (Indian Pines .mat files or fused .npz files)',
    )
    parser.add_argument(
        '--mode', type=str, default='benchmark',
        choices=['benchmark', 'production'],
        help='Training mode: benchmark (Indian Pines) or production (fused .npz)',
    )
    parser.add_argument('--epochs', type=int, default=50, help='Number of epochs')
    parser.add_argument('--batch-size', type=int, default=64, help='Batch size')
    parser.add_argument('--lr', type=float, default=1e-3, help='Learning rate')
    parser.add_argument(
        '--lambda-physics', type=float, default=0.1,
        help='Weight for physics consistency loss',
    )
    parser.add_argument(
        '--output', type=str, default='model_weights.pth',
        help='Path to save trained model weights',
    )
    parser.add_argument(
        '--resume', type=str, default=None,
        help='Path to checkpoint to resume training from',
    )

    args = parser.parse_args()
    train(args)
