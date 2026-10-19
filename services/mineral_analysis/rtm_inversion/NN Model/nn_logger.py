from __future__ import annotations

import csv
import dataclasses
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

METRIC_KEYS = (
    "loss",
    "classification_loss",
    "abundance_loss",
    "distillation_loss",
    "accuracy",
    "abundance_mae",
)


@dataclass
class EpochRecord:
    epoch: int
    train_loss: float | None = None
    val_loss: float | None = None
    train_classification_loss: float | None = None
    val_classification_loss: float | None = None
    train_abundance_loss: float | None = None
    val_abundance_loss: float | None = None
    train_distillation_loss: float | None = None
    val_distillation_loss: float | None = None
    train_accuracy: float | None = None
    val_accuracy: float | None = None
    train_abundance_mae: float | None = None
    val_abundance_mae: float | None = None
    learning_rate: float | None = None
    epoch_seconds: float | None = None
    is_best: bool = False


_CSV_FIELDS = [f.name for f in dataclasses.fields(EpochRecord)]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    value = float(value)
    return value if math.isfinite(value) else None


def _to_builtin(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _to_builtin(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_builtin(v) for v in value]
    if hasattr(value, "item"):
        count = value.numel() if hasattr(value, "numel") else getattr(value, "size", 1)
        if count != 1:
            raise ValueError("arrays and tensors cannot be written to the log")
        return _to_builtin(value.item())
    return str(value)


def _prefixed_metrics(prefix: str, metrics: Mapping[str, Any]) -> dict[str, float | None]:
    unknown = set(metrics) - set(METRIC_KEYS)
    if unknown:
        raise ValueError(f"unknown {prefix} metrics {sorted(unknown)}; allowed: {METRIC_KEYS}")
    if "loss" not in metrics:
        raise ValueError(f"{prefix} metrics must include 'loss'")
    return {f"{prefix}_{key}": _to_float(metrics.get(key)) for key in METRIC_KEYS}


class TrainingLogger:
    def __init__(
        self,
        log_dir: str | Path,
        run_name: str | None = None,
        *,
        save_json: bool = True,
        save_csv: bool = True,
        overwrite: bool = False,
    ) -> None:
        if not (save_json or save_csv):
            raise ValueError("at least one of save_json or save_csv must be True")

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_name = run_name or datetime.now().strftime("nn_train_%Y%m%d_%H%M%S")
        self.json_path = self.log_dir / f"{self.run_name}.json" if save_json else None
        self.csv_path = self.log_dir / f"{self.run_name}_epochs.csv" if save_csv else None

        existing = [p for p in (self.json_path, self.csv_path) if p and p.exists()]
        if existing and not overwrite:
            raise FileExistsError(f"log files already exist: {[str(p) for p in existing]}")

        self._records: list[EpochRecord] = []
        self._model_settings: dict[str, Any] | None = None
        self._num_parameters: int | None = None
        self._training_settings: dict[str, Any] = {}
        self._early_stopping_settings: dict[str, Any] = {}
        self._model_save_path: str | None = None
        self._best_epoch: int | None = None
        self._best_val_loss: float | None = None
        self._early_stopping_result: dict[str, Any] | None = None
        self._status = "running"
        self._finished = False
        self._finished_at: str | None = None
        self._duration: float | None = None

        self.start()
        if self.csv_path:
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=_CSV_FIELDS).writeheader()
        self._write_json()

    def start(self) -> None:
        self._started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._t0 = time.perf_counter()
        self._last_tick = self._t0

    def set_run_info(
        self,
        *,
        model_settings: Any = None,
        num_parameters: int | None = None,
        batch_size: int | None = None,
        learning_rate: float | None = None,
        max_epochs: int | None = None,
        early_stopping: Mapping[str, Any] | None = None,
        model_save_path: str | Path | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        if model_settings is not None:
            self._model_settings = _to_builtin(model_settings)
        if num_parameters is not None:
            self._num_parameters = int(num_parameters)
        if early_stopping is not None:
            self._early_stopping_settings = _to_builtin(early_stopping)
        if model_save_path is not None:
            self._model_save_path = str(model_save_path)

        settings = {
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "max_epochs": max_epochs,
        }
        self._training_settings.update({k: v for k, v in settings.items() if v is not None})
        if extra:
            self._training_settings.update(_to_builtin(extra))
        self._write_json()

    def log_epoch(
        self,
        epoch: int,
        train_metrics: Mapping[str, Any],
        val_metrics: Mapping[str, Any],
        learning_rate: float,
        is_best: bool | None = None,
    ) -> EpochRecord:
        if self._finished:
            raise RuntimeError("logger is already finished")
        if self._records and epoch <= self._records[-1].epoch:
            raise ValueError(f"epoch {epoch} is not after epoch {self._records[-1].epoch}")

        now = time.perf_counter()
        values = {
            **_prefixed_metrics("train", train_metrics),
            **_prefixed_metrics("val", val_metrics),
        }
        record = EpochRecord(
            epoch=int(epoch),
            learning_rate=_to_float(learning_rate),
            epoch_seconds=now - self._last_tick,
            **values,
        )
        self._last_tick = now

        val_loss = record.val_loss
        if is_best is None:
            is_best = val_loss is not None and (
                self._best_val_loss is None or val_loss < self._best_val_loss
            )
        if is_best:
            if val_loss is None:
                raise ValueError("an epoch cannot be best without a finite validation loss")
            self._best_epoch = record.epoch
            self._best_val_loss = val_loss
        record.is_best = bool(is_best)

        self._records.append(record)
        self._append_csv(record)
        self._write_json()
        return record

    def finish(
        self,
        *,
        stopped_early: bool = False,
        stop_reason: str | None = None,
        model_save_path: str | Path | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if self._finished:
            raise RuntimeError("logger is already finished")
        if model_save_path is not None:
            self._model_save_path = str(model_save_path)

        self._duration = time.perf_counter() - self._t0
        self._finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._early_stopping_result = {
            **self._early_stopping_settings,
            "stopped_early": bool(stopped_early),
            "stop_epoch": self._records[-1].epoch if self._records else None,
            "reason": stop_reason,
        }
        self._status = status or ("early_stopped" if stopped_early else "completed")
        self._finished = True
        self._write_json()
        return self.summary()

    def summary(self) -> dict[str, Any]:
        duration = self._duration
        if duration is None:
            duration = time.perf_counter() - self._t0

        accuracies = [r for r in self._records if r.val_accuracy is not None]
        best_acc = max(accuracies, key=lambda r: r.val_accuracy) if accuracies else None
        at_best = next((r for r in self._records if r.epoch == self._best_epoch), None)

        return _to_builtin(
            {
                "run_name": self.run_name,
                "status": self._status,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "training_duration_seconds": duration,
                "model": {
                    "settings": self._model_settings,
                    "num_parameters": self._num_parameters,
                },
                "training_settings": self._training_settings,
                "results": {
                    "epochs_completed": len(self._records),
                    "best_epoch": self._best_epoch,
                    "best_val_loss": self._best_val_loss,
                    "val_accuracy_at_best_epoch": at_best.val_accuracy if at_best else None,
                    "best_val_accuracy": best_acc.val_accuracy if best_acc else None,
                    "best_val_accuracy_epoch": best_acc.epoch if best_acc else None,
                    "early_stopping": self._early_stopping_result,
                },
                "model_save_path": self._model_save_path,
                "epochs": [dataclasses.asdict(r) for r in self._records],
            }
        )

    @property
    def records(self) -> list[EpochRecord]:
        return list(self._records)

    @property
    def best_epoch(self) -> int | None:
        return self._best_epoch

    @property
    def best_val_loss(self) -> float | None:
        return self._best_val_loss

    def _append_csv(self, record: EpochRecord) -> None:
        if not self.csv_path:
            return
        row = _to_builtin(dataclasses.asdict(record))
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_CSV_FIELDS).writerow(row)

    def _write_json(self) -> None:
        if not self.json_path:
            return
        tmp = self.json_path.with_name(self.json_path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.summary(), f, indent=2, allow_nan=False)
        os.replace(tmp, self.json_path)

    def __enter__(self) -> "TrainingLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._finished:
            if exc_type is None:
                self.finish()
            else:
                self.finish(status="failed", stop_reason=f"{exc_type.__name__}: {exc}")
        return False


def read_training_log(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
