"""Thermal-specific PyTorch model training and validation diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import joblib
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


class ThermalMLP(nn.Module):
    def __init__(self, n_features: int, n_classes: int) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(n_features, max(16, n_features // 2)),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(max(16, n_features // 2), max(8, n_features // 4)),
            nn.ReLU(),
            nn.Linear(max(8, n_features // 4), n_classes),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return self.network(values)


def _plot_class_distribution(
    labels: np.ndarray,
    output_path: Path,
    class_names: list[str],
) -> None:
    counts = np.bincount(labels, minlength=len(class_names))
    fig, axis = plt.subplots(figsize=(8, 5))
    axis.bar(class_names, counts, color="#e67e22", alpha=0.8)
    axis.set_title("Thermal Class Distribution")
    axis.set_xlabel("Thermal class")
    axis.set_ylabel("Pixel count")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _plot_feature_importance(
    model: ThermalMLP,
    feature_names: list[str],
    output_path: Path,
) -> None:
    first_layer = model.network[0]
    importance = np.abs(first_layer.weight.detach().cpu().numpy()).mean(axis=0)
    order = np.argsort(importance)[::-1]
    fig, axis = plt.subplots(figsize=(10, 6))
    axis.barh(
        [feature_names[index] for index in order],
        importance[order],
        color="#2980b9",
    )
    axis.invert_yaxis()
    axis.set_title("Thermal Model Feature Importance")
    axis.set_xlabel("Mean absolute weight")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _plot_class_features(
    dataframe: pd.DataFrame,
    feature_names: list[str],
    output_path: Path,
) -> None:
    rows = 2
    cols = 3
    fig, axes = plt.subplots(rows, cols, figsize=(16, 9))
    for index, class_id in enumerate(sorted(dataframe["thermal_class"].unique())):
        class_rows = dataframe[dataframe["thermal_class"] == class_id]
        axis = axes.flat[index]
        for feature in feature_names:
            axis.plot(
                class_rows[feature].to_numpy(),
                label=feature,
                alpha=0.7,
            )
        axis.set_title(f"Thermal class {class_id}")
        axis.set_ylabel("Feature value")
        axis.grid(alpha=0.2)
    axes.flat[-1].legend(loc="best")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def train_thermal_model(
    csv_path: str | Path,
    output_dir: str | Path,
    epochs: int = 20,
    batch_size: int = 64,
    learning_rate: float = 0.001,
    random_state: int = 42,
) -> dict:
    """Train a thermal-specific neural network using ST_B10-derived features."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dataframe = pd.read_csv(csv_path)
    feature_names = [
        "lst",
        "local_mean_3x3",
        "local_std_3x3",
        "local_min_3x3",
        "local_max_3x3",
        "local_range_3x3",
    ]
    missing = [column for column in feature_names + ["thermal_class"] if column not in dataframe]
    if missing:
        raise ValueError(f"Training CSV is missing required columns: {missing}")

    features = dataframe[feature_names].to_numpy(dtype=np.float32)
    labels = dataframe["thermal_class"].to_numpy(dtype=np.int64)
    if len(np.unique(labels)) < 2:
        raise ValueError("Training CSV must contain at least two thermal classes.")

    features_train, features_val, labels_train, labels_val = train_test_split(
        features,
        labels,
        test_size=0.2,
        random_state=random_state,
        stratify=labels,
    )
    scaler = StandardScaler()
    features_train = scaler.fit_transform(features_train)
    features_val = scaler.transform(features_val)

    train_tensor = torch.tensor(features_train, dtype=torch.float32)
    train_labels = torch.tensor(labels_train, dtype=torch.long)
    val_tensor = torch.tensor(features_val, dtype=torch.float32)
    val_labels = torch.tensor(labels_val, dtype=torch.long)
    train_loader = DataLoader(
        TensorDataset(train_tensor, train_labels),
        batch_size=batch_size,
        shuffle=True,
        generator=torch.Generator().manual_seed(random_state),
    )

    model = ThermalMLP(n_features=len(feature_names), n_classes=int(labels.max()) + 1)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    loss_function = nn.CrossEntropyLoss()

    model.train()
    for _ in range(epochs):
        for batch_features, batch_labels in train_loader:
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(model(batch_features), batch_labels)
            loss.backward()
            optimizer.step()

    model.eval()
    with torch.no_grad():
        predictions = model(val_tensor).argmax(dim=1).numpy()

    accuracy = float(np.mean(predictions == labels_val))
    class_names = [f"class_{class_id}" for class_id in range(int(labels.max()) + 1)]
    _plot_class_distribution(labels, output_dir / "thermal_class_distribution.png", class_names)
    _plot_feature_importance(model, feature_names, output_dir / "thermal_feature_importance.png")
    _plot_class_features(dataframe, feature_names, output_dir / "thermal_class_features.png")

    model_path = output_dir / "thermal_model.pt"
    scaler_path = output_dir / "thermal_scaler.joblib"
    metrics_path = output_dir / "thermal_model_metrics.json"
    torch.save(model.state_dict(), model_path)
    joblib.dump(scaler, scaler_path)
    metrics = {
        "model_path": str(model_path),
        "scaler_path": str(scaler_path),
        "validation_accuracy": accuracy,
        "n_samples": int(len(dataframe)),
        "n_features": len(feature_names),
        "classes": class_names,
        "feature_names": feature_names,
        "random_state": random_state,
        "epochs": epochs,
    }
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Generated thermal feature CSV")
    parser.add_argument("--output-dir", required=True, help="Directory for plots and model")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    metrics = train_thermal_model(
        csv_path=args.csv,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
    )
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
