"""Entry point for the neural-network mineral-analysis system.

    python nn_main.py train    [--config C] [--dataset D] [--output-dir DIR]
    python nn_main.py evaluate --dataset D [--model M] [--split test]
    python nn_main.py infer    --input IMAGE [--model M] [--output-dir DIR]

Functions expected from the other project modules (edit INTEGRATION to rename):

    nn_config.load_nn_config(path | None) -> config
    training_data_generator.generate_training_spectra(config) -> spectra bundle
    teacher_generator.generate_teacher_dataset(config, bundle, output_path) -> .npz path
        (runs alignment, continuum removal, SAM and SMA; writes the SpectralDataset format)
    train_nn.train_model(config, datasets, *, model_path, logger, device) -> best checkpoint path
    nn_evaluator.evaluate_model(model, dataset, config) -> dict of metrics
    raster_io.read_hyperspectral(path) -> (cube[H, W, B], wavelengths[B], geo metadata)
    wavelength_alignment.align_cube(cube, wavelengths, target_wavelengths) -> cube[H, W, L]
    continuum.remove_continuum_cube(cube, wavelengths) -> cube[H, W, L]
    nn_inference.predict_pixels(model, pixels[N, L], *, batch_size, device)
        -> mapping with class_ids[N], abundances[N, M] and optional confidence[N]
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import logging
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import torch

# Make this folder (sibling modules) and the service package (raster_io, sam, sma,
# continuum, wavelength_alignment, spectral_library) importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Importing the service `config` sets PROJ_DATA/PROJ_LIB to rasterio's bundled
# proj_data before rasterio loads, so a stray system PostGIS proj.db can't break
# georeferenced GeoTIFF writes during inference.
import config as _service_config  # noqa: E402,F401

from mineral_map import (
    ColorConfig,
    GeoMetadata,
    MineralPredictions,
    build_grids,
    write_mineral_maps,
)
from nn_logger import TrainingLogger
from spectral_dataset import SpectralDataset, build_datasets
from spectral_transformer import SpectralTransformer

log = logging.getLogger("nn_main")

INTEGRATION = {
    "load_config": ("nn_config", "load_nn_config"),
    "generate_spectra": ("training_data_generator", "generate_training_spectra"),
    "generate_teacher_dataset": ("teacher_generator", "generate_teacher_dataset"),
    "train": ("train_nn", "train_model"),
    "evaluate": ("nn_evaluator", "evaluate_model"),
    "read_image": ("raster_io", "read_hyperspectral"),
    "align": ("wavelength_alignment", "align_cube"),
    "continuum": ("continuum", "remove_continuum_cube"),
    "predict": ("nn_inference", "predict_pixels"),
}

TEACHER_MODULES = (
    "spectral_library",
    "sam",
    "sma",
    "teacher_generator",
    "training_data_generator",
)
DEFAULT_SPLITS = (0.7, 0.15, 0.15)
CONTINUUM_RANGE = (-0.01, 1.05)


class PipelineError(Exception):
    pass


def _call(key: str, *args, **kwargs):
    module_name, attr = INTEGRATION[key]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise PipelineError(f"cannot import '{module_name}' (needed for '{key}'): {exc}") from exc
    func = getattr(module, attr, None)
    if func is None:
        raise PipelineError(
            f"'{module_name}.py' has no '{attr}'; nn_main.py expects it (see INTEGRATION)"
        )
    return func(*args, **kwargs)


def _setup_logging(log_dir: Path, mode: str, level: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"nn_main_{mode}_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")],
        force=True,
    )
    return path


def _banner(title: str) -> None:
    log.info("=" * 70)
    log.info(title)
    log.info("=" * 70)


def _require_file(path: str | Path, label: str, hint: str = "") -> Path:
    p = Path(path)
    if not p.exists():
        raise PipelineError(f"{label} not found: {p}" + (f". {hint}" if hint else ""))
    if not p.is_file():
        raise PipelineError(f"{label} is not a file: {p}")
    return p


def _cfg(config: Any, name: str, default: Any) -> Any:
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def _with_overrides(config: Any, overrides: Mapping[str, Any]) -> Any:
    overrides = {k: v for k, v in overrides.items() if v is not None}
    if not overrides:
        return config
    if dataclasses.is_dataclass(config) and not isinstance(config, type):
        unknown = set(overrides) - {f.name for f in dataclasses.fields(config)}
        if unknown:
            raise PipelineError(f"config has no field(s) {sorted(unknown)} to override")
        return dataclasses.replace(config, **overrides)
    if isinstance(config, Mapping):
        return {**config, **overrides}
    unknown = [k for k in overrides if not hasattr(config, k)]
    if unknown:
        raise PipelineError(f"config has no field(s) {sorted(unknown)} to override")
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _resolve_device(name: str) -> torch.device:
    if name == "auto":
        name = "cuda" if torch.cuda.is_available() else "cpu"
    if name == "cuda" and not torch.cuda.is_available():
        raise PipelineError("--device cuda requested but CUDA is not available")
    return torch.device(name)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    return str(value)


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_jsonable(data), f, indent=2, allow_nan=False)


def _load_model(path: str | Path, device: torch.device) -> SpectralTransformer:
    path = _require_file(path, "model checkpoint", "Train one first: python nn_main.py train")
    try:
        model = SpectralTransformer.load(path, map_location=device)
    except Exception as exc:
        raise PipelineError(f"could not load checkpoint {path}: {exc}") from exc
    return model.to(device).eval()


def _model_names(model: SpectralTransformer) -> tuple[str, ...]:
    names = model.config.mineral_names
    if names is None:
        raise PipelineError("checkpoint has no mineral names; retrain with mineral_names in the config")
    return names


def _model_grid(model: SpectralTransformer) -> np.ndarray:
    return model.wavelengths.detach().cpu().numpy().astype(np.float64)


def _log_metrics(metrics: Any) -> None:
    if not isinstance(metrics, Mapping):
        return
    for key, value in metrics.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            log.info("  %-30s %.4f", key, value)


def _default_model_path(args: argparse.Namespace) -> Path:
    return Path(args.model) if args.model else Path(args.output_dir) / "models" / "best_model.pt"


def _check_dataset_matches_model(dataset: SpectralDataset, model: SpectralTransformer) -> None:
    if dataset.num_classes != model.config.num_minerals:
        raise PipelineError(
            f"dataset has {dataset.num_classes} minerals, model expects {model.config.num_minerals}"
        )
    if dataset.mineral_names is not None and dataset.mineral_names != _model_names(model):
        raise PipelineError(
            f"mineral order differs: dataset {dataset.mineral_names} vs model {_model_names(model)}"
        )


def _prepare_teacher_dataset(args: argparse.Namespace, config: Any, out: Path) -> Path:
    if args.dataset:
        path = _require_file(args.dataset, "teacher dataset")
        log.info("[1/5] Using existing teacher dataset: %s", path)
        return path

    path = out / "teacher_dataset.npz"
    if path.exists() and not args.regenerate:
        raise PipelineError(
            f"{path} already exists. Reuse it with --dataset {path} or overwrite with --regenerate."
        )
    log.info("[1/5] Generating training spectra and teacher targets (USGS + SAM + SMA)")
    bundle = _call("generate_spectra", config)
    path.parent.mkdir(parents=True, exist_ok=True)
    produced = _call("generate_teacher_dataset", config, bundle, path)
    return _require_file(produced or path, "teacher dataset produced by the teacher generator")


def _load_splits(path: Path, config: Any) -> dict[str, SpectralDataset]:
    log.info("[2/5] Building train/validation/test splits")
    full = SpectralDataset.from_npz(path)
    if full.num_dropped:
        log.warning("dropped %d invalid samples: %s", full.num_dropped, full.invalid_report)
    if full.mineral_names is None:
        raise PipelineError(f"{path} has no 'mineral_names'; they are required for legends and checkpoints")

    seed = int(_cfg(config, "seed", 42))
    fractions = tuple(_cfg(config, "split_fractions", DEFAULT_SPLITS))
    datasets = build_datasets(full, fractions=fractions, seed=seed)
    for required in ("train", "val"):
        if required not in datasets:
            raise PipelineError(f"split '{required}' is empty; check split fractions {fractions}")

    log.info(
        "samples: %s | bands: %d | minerals: %s | seed: %d",
        {k: len(v) for k, v in datasets.items()},
        full.num_bands,
        ", ".join(full.mineral_names),
        seed,
    )
    return datasets


def _evaluate(
    model: SpectralTransformer,
    dataset: SpectralDataset,
    config: Any,
    split: str,
    out: Path,
) -> Path:
    log.info("Evaluating on '%s' split (%d samples)", split, len(dataset))
    metrics = _call("evaluate", model, dataset, config)
    _log_metrics(metrics)
    path = out / f"evaluation_{split}.json"
    _save_json(path, metrics)
    log.info("Evaluation saved: %s", path)
    return path


def run_train(args: argparse.Namespace) -> int:
    out = Path(args.output_dir)
    log_path = _setup_logging(out / "logs", "train", args.log_level)
    _banner("MODE: TRAINING  (USGS + SAM + SMA teacher -> neural network student)")

    config = _with_overrides(
        _call("load_config", args.config),
        {
            "max_epochs": args.max_epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "seed": args.seed,
        },
    )
    device = _resolve_device(args.device)
    log.info("device: %s | output dir: %s", device, out)

    dataset_path = _prepare_teacher_dataset(args, config, out)
    datasets = _load_splits(dataset_path, config)

    log.info("[3/5] Training Spectral Transformer")
    model_path = out / "models" / "best_model.pt"
    started = time.perf_counter()
    with TrainingLogger(out / "logs", run_name=f"train_{datetime.now():%Y%m%d_%H%M%S}") as train_log:
        produced = _call(
            "train", config, datasets, model_path=model_path, logger=train_log, device=device
        )
    best_path = _require_file(produced or model_path, "best model checkpoint from training")
    log.info("training finished in %.1f s; best model: %s", time.perf_counter() - started, best_path)

    log.info("[4/5] Evaluating best model")
    eval_path = None
    if args.skip_eval:
        log.warning("evaluation skipped (--skip-eval)")
    else:
        model = _load_model(best_path, device)
        split = "test" if "test" in datasets else "val"
        if split == "val":
            log.warning("no test split; evaluating on the validation split used for early stopping")
        eval_path = _evaluate(model, datasets[split], config, split, out)

    log.info("[5/5] Done")
    log.info("best model : %s", best_path)
    log.info("training log: %s", train_log.json_path)
    log.info("evaluation : %s", eval_path or "skipped")
    log.info("run log    : %s", log_path)
    return 0


def run_evaluate(args: argparse.Namespace) -> int:
    out = Path(args.output_dir)
    _setup_logging(out / "logs", "evaluate", args.log_level)
    _banner("MODE: EVALUATION  (neural network vs teacher targets)")

    config = _with_overrides(_call("load_config", args.config), {"seed": args.seed})
    device = _resolve_device(args.device)
    model = _load_model(_default_model_path(args), device)
    dataset_path = _require_file(args.dataset, "teacher dataset")
    grid = _model_grid(model)

    try:
        if args.split == "all":
            dataset = SpectralDataset.from_npz(dataset_path, expected_wavelengths=grid)
        else:
            fractions = tuple(_cfg(config, "split_fractions", DEFAULT_SPLITS))
            splits = build_datasets(
                dataset_path,
                fractions=fractions,
                seed=int(_cfg(config, "seed", 42)),
                expected_wavelengths=grid,
            )
            dataset = splits.get(args.split)
            if dataset is None:
                raise PipelineError(f"split '{args.split}' is empty for fractions {fractions}")
    except ValueError as exc:
        raise PipelineError(f"dataset is incompatible with the model: {exc}") from exc

    _check_dataset_matches_model(dataset, model)
    _evaluate(model, dataset, config, args.split, out)
    return 0


def _prepare_image(
    input_path: Path, model: SpectralTransformer
) -> tuple[np.ndarray, GeoMetadata]:
    cube, wavelengths, geo = _call("read_image", input_path)
    cube = np.asarray(cube)
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    if cube.ndim != 3:
        raise PipelineError(f"image cube must be (H, W, bands), got shape {cube.shape}")
    if cube.shape[-1] != wavelengths.size:
        raise PipelineError(
            f"cube has {cube.shape[-1]} bands in the last axis but {wavelengths.size} wavelengths"
        )
    meta = GeoMetadata.from_source(geo)
    if (meta.height, meta.width) != cube.shape[:2]:
        raise PipelineError(
            f"geo metadata {(meta.height, meta.width)} does not match cube {cube.shape[:2]}"
        )
    log.info(
        "image: %d x %d px, %d bands, %.1f-%.1f nm",
        meta.height, meta.width, wavelengths.size, wavelengths.min(), wavelengths.max(),
    )

    grid = _model_grid(model)
    expected = (*cube.shape[:2], model.config.num_bands)
    aligned = np.asarray(_call("align", cube, wavelengths, target_wavelengths=grid))
    if aligned.shape != expected:
        raise PipelineError(
            f"wavelength dimension mismatch after alignment: got {aligned.shape}, model expects {expected}"
        )
    removed = np.asarray(_call("continuum", aligned, grid))
    if removed.shape != expected:
        raise PipelineError(f"continuum removal changed shape {expected} -> {removed.shape}")
    return removed, meta


def _prediction_arrays(result: Any, n: int, num_minerals: int) -> dict[str, np.ndarray | None]:
    def field(name: str, required: bool = True):
        value = result.get(name) if isinstance(result, Mapping) else getattr(result, name, None)
        if value is None and required:
            raise PipelineError(f"nn_inference result is missing '{name}'")
        return None if value is None else np.asarray(value)

    class_ids, abundances, confidence = field("class_ids"), field("abundances"), field("confidence", False)
    if class_ids.shape != (n,):
        raise PipelineError(f"class_ids has shape {class_ids.shape}, expected ({n},)")
    if abundances.shape != (n, num_minerals):
        raise PipelineError(f"abundances has shape {abundances.shape}, expected ({n}, {num_minerals})")
    if confidence is not None and confidence.shape != (n,):
        raise PipelineError(f"confidence has shape {confidence.shape}, expected ({n},)")
    return {"class_ids": class_ids, "abundances": abundances, "confidence": confidence}


def _prediction_summary(
    predictions: MineralPredictions, meta: GeoMetadata
) -> dict[str, Any]:
    grids = build_grids(predictions, meta.height, meta.width)
    names = predictions.mineral_names
    classified = grids.class_ids >= 0
    counts = np.bincount(grids.class_ids[classified], minlength=len(names))
    total = max(int(classified.sum()), 1)

    def mean_of(values: np.ndarray) -> float | None:
        finite = values[np.isfinite(values)]
        return float(finite.mean()) if finite.size else None

    summary: dict[str, Any] = {
        "pixels_total": int(classified.size),
        "pixels_classified": int(classified.sum()),
        "minerals": [
            {
                "mineral": name,
                "pixels": int(counts[i]),
                "fraction_of_classified": float(counts[i] / total),
                "mean_abundance": mean_of(grids.abundances[i]),
            }
            for i, name in enumerate(names)
        ],
    }
    if grids.confidence is not None:
        finite = grids.confidence[np.isfinite(grids.confidence)]
        summary["confidence"] = (
            {"mean": float(finite.mean()), "min": float(finite.min()), "max": float(finite.max())}
            if finite.size
            else None
        )
    return summary


def run_infer(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    prefix = args.prefix or input_path.stem
    out_dir = Path(args.output_dir) / "inference" / prefix
    log_path = _setup_logging(out_dir, "infer", args.log_level)
    _banner("MODE: INFERENCE  (trained neural network only; no USGS / SAM / SMA)")

    _require_file(input_path, "input image")
    color_config = (
        ColorConfig.from_json(_require_file(args.colors, "color config")) if args.colors else None
    )
    device = _resolve_device(args.device)
    timings: dict[str, float] = {}

    def stage(label: str) -> float:
        log.info(label)
        return time.perf_counter()

    t = stage("[1/6] Loading trained model")
    model_path = _default_model_path(args)
    model = _load_model(model_path, device)
    names = _model_names(model)
    grid = _model_grid(model)
    log.info(
        "model: %s | %d params | %d bands (%.1f-%.1f nm) | minerals: %s",
        model_path, model.num_parameters(), model.config.num_bands, grid[0], grid[-1], ", ".join(names),
    )
    timings["load_model"] = time.perf_counter() - t

    t = stage("[2/6] Reading image, aligning wavelengths, removing continuum")
    cube, meta = _prepare_image(input_path, model)
    timings["prepare_image"] = time.perf_counter() - t

    t = stage("[3/6] Selecting valid pixels")
    valid = np.isfinite(cube).all(axis=-1)
    if not valid.any():
        raise PipelineError("no pixel has finite values across all bands after preprocessing")
    pixels = cube[valid].astype(np.float32)
    outside = float(((pixels < CONTINUUM_RANGE[0]) | (pixels > CONTINUUM_RANGE[1])).mean())
    if outside > 0.01:
        log.warning(
            "%.1f%% of values fall outside the continuum-removed range %s; "
            "check that the input is continuum-removed",
            outside * 100, CONTINUUM_RANGE,
        )
    log.info("valid pixels: %d of %d", pixels.shape[0], valid.size)
    del cube
    timings["select_pixels"] = time.perf_counter() - t

    t = stage("[4/6] Predicting minerals and abundances")
    result = _call(
        "predict", model, pixels, batch_size=args.inference_batch_size, device=device
    )
    arrays = _prediction_arrays(result, pixels.shape[0], len(names))
    predictions = MineralPredictions(
        class_ids=arrays["class_ids"],
        abundances=arrays["abundances"],
        mineral_names=names,
        confidence=arrays["confidence"],
        valid_mask=valid,
    )
    timings["predict"] = time.perf_counter() - t

    t = stage("[5/6] Writing maps and GeoTIFFs")
    outputs = write_mineral_maps(
        predictions,
        meta,
        out_dir,
        prefix=prefix,
        color_config=color_config,
        min_confidence=args.min_confidence,
        per_mineral_files=not args.no_per_mineral,
        write_preview=not args.no_preview,
        title=f"{prefix} - neural network mineral map",
    )
    timings["write_outputs"] = time.perf_counter() - t

    t = stage("[6/6] Summarising predictions")
    summary = _prediction_summary(predictions, meta)
    for item in summary["minerals"]:
        log.info(
            "  %-20s %9d px  %5.1f%%  mean abundance %s",
            item["mineral"],
            item["pixels"],
            item["fraction_of_classified"] * 100,
            "n/a" if item["mean_abundance"] is None else f"{item['mean_abundance']:.3f}",
        )
    teacher_loaded = sorted(m for m in TEACHER_MODULES if m in sys.modules)
    if teacher_loaded:
        log.warning("teacher modules were imported during inference: %s", teacher_loaded)
    timings["summarise"] = time.perf_counter() - t

    summary_path = out_dir / f"{prefix}_prediction_summary.json"
    _save_json(
        summary_path,
        {
            "input": input_path,
            "model": model_path,
            "device": str(device),
            "num_bands": model.config.num_bands,
            "wavelength_range_nm": [float(grid[0]), float(grid[-1])],
            "min_confidence": args.min_confidence,
            "teacher_modules_loaded": teacher_loaded,
            "timings_seconds": timings,
            "predictions": summary,
            "outputs": outputs,
        },
    )

    log.info("outputs written to %s", out_dir)
    for key, path in outputs.items():
        log.info("  %-22s %s", key, path.name)
    log.info("  %-22s %s", "prediction_summary", summary_path.name)
    log.info("run log: %s", log_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output-dir", default="nn_outputs")
    common.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    common.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    parser = argparse.ArgumentParser(
        prog="nn_main.py", description="Neural-network mineral analysis: train, evaluate or infer"
    )
    # Not required: a bare `python nn_main.py` prints help instead of an argparse error.
    modes = parser.add_subparsers(dest="mode")

    train = modes.add_parser("train", parents=[common], help="train the student network")
    train.add_argument("--config")
    train.add_argument("--dataset", help="reuse an existing teacher dataset (.npz)")
    train.add_argument("--regenerate", action="store_true", help="overwrite the generated dataset")
    train.add_argument("--max-epochs", type=int)
    train.add_argument("--batch-size", type=int)
    train.add_argument("--learning-rate", type=float)
    train.add_argument("--seed", type=int)
    train.add_argument("--skip-eval", action="store_true")
    train.set_defaults(func=run_train)

    evaluate = modes.add_parser("evaluate", parents=[common], help="evaluate a trained model")
    evaluate.add_argument("--config")
    evaluate.add_argument("--dataset", required=True, help="teacher dataset (.npz)")
    evaluate.add_argument("--model")
    evaluate.add_argument("--split", choices=("train", "val", "test", "all"), default="test")
    evaluate.add_argument("--seed", type=int)
    evaluate.set_defaults(func=run_evaluate)

    infer = modes.add_parser("infer", parents=[common], help="map minerals in a hyperspectral image")
    infer.add_argument("--input", required=True, help="prepared hyperspectral image")
    infer.add_argument("--model")
    infer.add_argument("--prefix")
    infer.add_argument("--colors", help="JSON file with ColorConfig settings")
    infer.add_argument("--min-confidence", type=float)
    infer.add_argument("--inference-batch-size", type=int, default=8192)
    infer.add_argument("--no-preview", action="store_true")
    infer.add_argument("--no-per-mineral", action="store_true")
    infer.set_defaults(func=run_infer)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:  # no mode given: show help, not an error
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except PipelineError as exc:
        log.error("%s", exc)
        return 2
    except (FileNotFoundError, ValueError) as exc:
        log.error("Invalid input: %s", exc)
        log.debug("details", exc_info=True)
        return 2
    except KeyboardInterrupt:
        log.error("Interrupted by user")
        return 130
    except Exception:
        log.exception("Unexpected failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
