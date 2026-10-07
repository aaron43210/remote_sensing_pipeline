"""Thermal entry point for a selected ROI and ST_B10-only model pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from thermal_feature_pipeline import build_thermal_features
from thermal_model_trainer import train_thermal_model


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--entry-point",
        choices=("thermal", "all"),
        default="thermal",
        help="Thermal-only or shared multi-service entry point.",
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--raster", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--model-output", required=True)
    parser.add_argument("--epochs", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    if args.entry_point == "all":
        raise NotImplementedError(
            "The shared all-services entry point is intentionally not implemented "
            "inside the thermal-only service."
        )

    features = build_thermal_features(
        raster_path=args.raster,
        manifest_path=args.manifest,
        output_csv=args.csv,
    )
    metrics = train_thermal_model(
        csv_path=args.csv,
        output_dir=args.model_output,
        epochs=args.epochs,
    )
    report = {
        "entry_point": args.entry_point,
        "n_features": len(features.columns),
        "n_samples": len(features),
        "model": metrics,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
