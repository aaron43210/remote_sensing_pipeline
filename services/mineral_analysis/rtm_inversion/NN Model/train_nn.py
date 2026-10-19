"""Trains the Spectral Transformer (student) on teacher-labelled spectra.

The teacher targets (SAM class + SMA abundance) are produced offline by
training_data_generator.py + teacher_generator.py; nothing here calls SAM/SMA.

This module exposes `train_model(config, datasets, *, model_path, logger, device)`
which nn_main.py drives. `datasets` is a dict of spectral_dataset.SpectralDataset
({"train", "val", ...}); the model and checkpoint use spectral_transformer.py.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

# allow `import spectral_transformer` etc. when run from this folder
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spectral_transformer import (
    MultiTaskLoss,
    MultiTaskLossConfig,
    SpectralTransformer,
    SpectralTransformerConfig,
)


def _loader(dataset, batch_size, shuffle, device, seed):
    pin = device.type == "cuda"
    gen = torch.Generator().manual_seed(int(seed)) if shuffle else None
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle,
                      generator=gen, pin_memory=pin)


def _run_epoch(model, loss_fn, loader, device, optimizer=None):
    training = optimizer is not None
    model.train(training)
    sums, n, correct, abs_sum, elems = defaultdict(float), 0, 0, 0.0, 0
    with torch.set_grad_enabled(training):
        for batch in loader:
            batch = {k: v.to(device, non_blocking=True) for k, v in batch.items()}
            out = model(batch["spectrum"])
            losses = loss_fn(out, batch)
            if training:
                optimizer.zero_grad(set_to_none=True)
                losses["total"].backward()
                optimizer.step()
            bs = batch["spectrum"].size(0)
            n += bs
            sums["loss"] += losses["total"].item() * bs
            sums["classification_loss"] += losses["classification"].item() * bs
            sums["abundance_loss"] += losses["abundance"].item() * bs
            correct += (out.class_logits.argmax(1) == batch["class_target"]).sum().item()
            diff = (out.abundances.detach() - batch["abundance"]).abs()
            abs_sum += diff.sum().item()
            elems += diff.numel()
    return {
        "loss": sums["loss"] / n,
        "classification_loss": sums["classification_loss"] / n,
        "abundance_loss": sums["abundance_loss"] / n,
        "accuracy": correct / n,
        "abundance_mae": abs_sum / elems,
    }


def train_model(config, datasets, *, model_path, logger=None, device=None):
    """Train the student network, saving the best (lowest val loss) checkpoint."""
    device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    seed = int(getattr(config, "seed", 42))
    torch.manual_seed(seed)
    np.random.seed(seed)

    train_ds = datasets["train"]
    val_ds = datasets.get("val") or train_ds
    names = tuple(train_ds.mineral_names)
    n_bands = int(np.asarray(train_ds.wavelengths).size)

    model_cfg = SpectralTransformerConfig(
        num_bands=n_bands, num_minerals=train_ds.num_classes, mineral_names=names,
        d_model=config.d_model, num_heads=config.num_heads, num_layers=config.num_layers,
        ff_dim=config.ff_dim, dropout=config.dropout, pooling=config.pooling,
        patch_size=getattr(config, "patch_size", 1),
    )
    model = SpectralTransformer(model_cfg, train_ds.wavelengths).to(device)
    loss_fn = MultiTaskLoss(MultiTaskLossConfig(
        classification_weight=config.classification_weight,
        abundance_weight=config.abundance_weight,
        abundance_loss=getattr(config, "abundance_loss", "mse"),
    )).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate,
                                  weight_decay=config.weight_decay)

    train_loader = _loader(train_ds, config.batch_size, True, device, seed)
    val_loader = _loader(val_ds, config.batch_size, False, device, seed)

    model_path = Path(model_path)
    print(f"device={device} | train={len(train_ds)} val={len(val_ds)} | "
          f"bands={n_bands} classes={train_ds.num_classes} | "
          f"params={model.num_parameters():,}")

    best, best_epoch, stale = float("inf"), 0, 0
    for epoch in range(1, config.max_epochs + 1):
        tm = _run_epoch(model, loss_fn, train_loader, device, optimizer)
        vm = _run_epoch(model, loss_fn, val_loader, device)
        if not (np.isfinite(tm["loss"]) and np.isfinite(vm["loss"])):
            raise RuntimeError(f"non-finite loss at epoch {epoch}; check data/learning rate")

        improved = vm["loss"] < best
        if improved:
            best, best_epoch, stale = vm["loss"], epoch, 0
            model.save(model_path, extra={"mineral_names": list(names)})
        else:
            stale += 1
        if logger is not None:
            logger.log_epoch(epoch, tm, vm, config.learning_rate, is_best=improved)
        print(f"epoch {epoch:3d}/{config.max_epochs} | train {tm['loss']:.4f} | "
              f"val {vm['loss']:.4f} acc {vm['accuracy']:.3f} mae {vm['abundance_mae']:.4f}"
              f"{' *best*' if improved else ''}")
        if stale >= config.early_stopping_patience:
            print(f"early stopping: no val improvement for {stale} epochs")
            break

    if best_epoch == 0:  # degenerate (1 epoch / no improvement): still save something
        model.save(model_path, extra={"mineral_names": list(names)})
    print(f"best epoch {best_epoch} (val loss {best:.4f}) -> {model_path}")
    return model_path


if __name__ == "__main__":
    print("Use the orchestrator:  python nn_main.py train")
