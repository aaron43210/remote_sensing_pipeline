"""Hyperparameter sweep: train several SpectralTransformer configs on the
output/training_set data and keep the best (by test accuracy, then abundance MAE).

Run:  python sweep_train.py            # writes runs/sweep/ + models/best_model.pt
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nn_config
from nn_evaluator import evaluate_model
from spectral_dataset import SpectralDataset, build_datasets
from spectral_transformer import SpectralTransformer
from train_nn import train_model

_SERVICE_DIR = Path(__file__).resolve().parents[2]
DATASET = _SERVICE_DIR / "output" / "training_set" / "nn_dataset.npz"
SWEEP_DIR = Path(__file__).resolve().parent / "runs" / "sweep"
BEST_MODEL = Path(__file__).resolve().parent / "models" / "best_model.pt"

# Each entry overrides nn_config.NNConfig fields. max_epochs/patience kept modest;
# early stopping ends runs that have converged.
CONFIGS = {
    "base":  dict(d_model=64,  num_layers=4, patch_size=1, batch_size=128, learning_rate=5e-4, abundance_loss="mse"),
    "wide":  dict(d_model=128, num_layers=4, patch_size=5, batch_size=256, learning_rate=1e-3, abundance_loss="mse"),
    "deep":  dict(d_model=128, num_layers=6, patch_size=5, batch_size=256, learning_rate=5e-4, abundance_loss="mse"),
    "kl":    dict(d_model=128, num_layers=4, patch_size=3, batch_size=256, learning_rate=1e-3, abundance_loss="kl"),
}
COMMON = dict(max_epochs=40, early_stopping_patience=8, seed=42)


def main():
    import dataclasses
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    base = nn_config.load_nn_config()
    full = SpectralDataset.from_npz(DATASET)
    datasets = build_datasets(full, fractions=(0.7, 0.15, 0.15), seed=42)
    sizes = {k: len(v) for k, v in datasets.items()}
    print(f"device={device} | samples={sizes} "
          f"| bands={full.num_bands} | minerals={len(full.mineral_names)}")

    leaderboard = []
    for name, overrides in CONFIGS.items():
        cfg = dataclasses.replace(base, **{**COMMON, **overrides})
        run_dir = SWEEP_DIR / name
        model_path = run_dir / "best_model.pt"
        print(f"\n=== config '{name}': {overrides} ===")
        train_model(cfg, datasets, model_path=model_path, logger=None, device=device)
        model = SpectralTransformer.load(model_path, map_location=device)
        metrics = evaluate_model(model, datasets["test"], cfg)
        entry = {"name": name, "overrides": overrides,
                 "accuracy": metrics["accuracy"], "macro_f1": metrics["macro_f1"],
                 "abundance_mae": metrics["abundance_mae"], "abundance_rmse": metrics["abundance_rmse"],
                 "model_path": str(model_path)}
        leaderboard.append(entry)
        print(f"    test: acc {entry['accuracy']:.4f} | macroF1 {entry['macro_f1']:.4f} | "
              f"abund MAE {entry['abundance_mae']:.4f} RMSE {entry['abundance_rmse']:.4f}")

    # Rank: higher accuracy first, then lower abundance MAE.
    leaderboard.sort(key=lambda e: (-e["accuracy"], e["abundance_mae"]))
    best = leaderboard[0]
    BEST_MODEL.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(best["model_path"], BEST_MODEL)

    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    (SWEEP_DIR / "leaderboard.json").write_text(
        json.dumps({"best": best["name"], "leaderboard": leaderboard}, indent=2), encoding="utf-8")

    print("\n==== LEADERBOARD (best first) ====")
    for e in leaderboard:
        print(f"  {e['name']:5s}  acc {e['accuracy']:.4f}  macroF1 {e['macro_f1']:.4f}  "
              f"abundMAE {e['abundance_mae']:.4f}")
    print(f"\nBEST: {best['name']} -> {BEST_MODEL}")


if __name__ == "__main__":
    main()
