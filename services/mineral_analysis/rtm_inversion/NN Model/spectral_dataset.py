from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import torch
from torch.utils.data import Dataset

logger = logging.getLogger(__name__)

SPLIT_NAMES = ("train", "val", "test")
_REQUIRED_KEYS = ("spectra", "wavelengths", "class_targets", "abundances")
_OPTIONAL_KEYS = ("sam_angles", "group_ids", "sample_ids", "mineral_names")


def _validate_wavelengths(wavelengths, expected, atol: float) -> np.ndarray:
    wl = np.asarray(wavelengths, dtype=np.float64)
    if wl.ndim != 1 or wl.size == 0:
        raise ValueError("wavelengths must be a non-empty 1D array")
    if not np.isfinite(wl).all():
        raise ValueError("wavelengths contain non-finite values")
    if np.any(np.diff(wl) <= 0):
        raise ValueError("wavelengths must be strictly increasing")
    if expected is not None:
        ref = np.asarray(expected, dtype=np.float64)
        if ref.shape != wl.shape or not np.allclose(wl, ref, rtol=0.0, atol=atol):
            raise ValueError("wavelength grid does not match the expected grid")
    return wl


def _stack_spectra(spectra, n_bands: int) -> np.ndarray:
    if isinstance(spectra, np.ndarray) and spectra.ndim == 2:
        arr = spectra.astype(np.float64, copy=False)
    else:
        rows = [np.asarray(s, dtype=np.float64) for s in spectra]
        bad = [i for i, r in enumerate(rows) if r.ndim != 1 or r.shape[0] != n_bands]
        if bad:
            raise ValueError(
                f"{len(bad)} spectra do not have {n_bands} bands (first at index {bad[0]})"
            )
        arr = np.stack(rows) if rows else np.empty((0, n_bands))
    if arr.ndim != 2 or arr.shape[1] != n_bands:
        raise ValueError(f"spectra must have shape (N, {n_bands}), got {arr.shape}")
    return arr


def _as_float_array(values, name: str, ndims: tuple[int, ...]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim not in ndims:
        raise ValueError(f"{name} must have ndim in {ndims}, got {arr.ndim}")
    return arr


def _check_length(name: str, arr: np.ndarray, n: int) -> None:
    if len(arr) != n:
        raise ValueError(f"{name} has {len(arr)} rows, expected {n}")


def _optional_ids(values, name: str, n: int) -> np.ndarray | None:
    if values is None:
        return None
    arr = np.asarray(values)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be 1D")
    _check_length(name, arr, n)
    return arr


def _find_failures(
    x: np.ndarray,
    a: np.ndarray,
    cls: np.ndarray,
    angles: np.ndarray | None,
    num_classes: int,
    multilabel: bool,
    abundance_tol: float,
) -> dict[str, np.ndarray]:
    failures: dict[str, np.ndarray] = {}
    with np.errstate(invalid="ignore"):
        failures["non_finite_spectrum"] = ~np.isfinite(x).all(axis=1)
        failures["non_finite_abundance"] = ~np.isfinite(a).all(axis=1)
        failures["negative_abundance"] = (a < -abundance_tol).any(axis=1)
        failures["abundance_sum"] = np.abs(a.sum(axis=1) - 1.0) > abundance_tol
        failures["zero_abundance"] = np.clip(a, 0.0, None).sum(axis=1) <= 0.0

        if multilabel:
            ok = np.isfinite(cls).all(axis=1) & ((cls >= 0) & (cls <= 1)).all(axis=1)
        else:
            ok = (
                np.isfinite(cls)
                & (cls >= 0)
                & (cls < num_classes)
                & (cls == np.floor(cls))
            )
        failures["invalid_class_target"] = ~ok

        if angles is not None:
            flat = angles.reshape(len(angles), -1)
            failures["non_finite_sam_angle"] = ~np.isfinite(flat).all(axis=1)
    return failures


def _take(tensor: torch.Tensor | None, index: torch.Tensor) -> torch.Tensor | None:
    return None if tensor is None else tensor[index]


class SpectralDataset(Dataset):
    """Spectral samples on a fixed wavelength grid with teacher targets.

    Each item is a dict with:
      spectrum      (L,) float32
      class_target  () int64 for single-label, (C,) float32 for multi-label
      abundance     (C,) float32
      sam_angle     (C,) or () float32, only when provided
    """

    def __init__(
        self,
        spectra,
        wavelengths,
        class_targets,
        abundances,
        *,
        sam_angles=None,
        group_ids=None,
        sample_ids=None,
        mineral_names: Sequence[str] | None = None,
        expected_wavelengths=None,
        wavelength_atol: float = 1e-3,
        abundance_tol: float = 0.05,
        normalize_abundances: bool = True,
        invalid: str = "drop",
    ):
        if invalid not in ("drop", "raise"):
            raise ValueError("invalid must be 'drop' or 'raise'")

        wl = _validate_wavelengths(wavelengths, expected_wavelengths, wavelength_atol)
        x = _stack_spectra(spectra, wl.size)
        n = x.shape[0]

        a = _as_float_array(abundances, "abundances", (2,))
        _check_length("abundances", a, n)
        num_classes = a.shape[1]

        names = None
        if mineral_names is not None:
            names = tuple(str(m) for m in mineral_names)
            if len(names) != num_classes:
                raise ValueError(
                    f"mineral_names has {len(names)} entries, abundances have {num_classes} columns"
                )

        cls = np.asarray(class_targets)
        _check_length("class_targets", cls, n)
        if cls.ndim == 1:
            multilabel = False
        elif cls.ndim == 2 and cls.shape[1] == num_classes:
            multilabel = True
        else:
            raise ValueError(
                f"class_targets must have shape (N,) or (N, {num_classes}), got {cls.shape}"
            )
        cls = cls.astype(np.float64)

        angles = None
        if sam_angles is not None:
            angles = _as_float_array(sam_angles, "sam_angles", (1, 2))
            _check_length("sam_angles", angles, n)
            if angles.ndim == 2 and angles.shape[1] != num_classes:
                raise ValueError(
                    f"sam_angles must have {num_classes} columns, got {angles.shape[1]}"
                )

        groups = _optional_ids(group_ids, "group_ids", n)
        ids = _optional_ids(sample_ids, "sample_ids", n)

        failures = _find_failures(
            x, a, cls, angles, num_classes, multilabel, abundance_tol
        )
        failed = np.zeros(n, dtype=bool)
        for mask in failures.values():
            failed |= mask
        report = {k: int(m.sum()) for k, m in failures.items() if m.any()}

        if failed.any():
            message = f"{int(failed.sum())} of {n} samples invalid: {report}"
            if invalid == "raise":
                raise ValueError(message)
            logger.warning("Dropping %s", message)
        keep = ~failed
        if not keep.any():
            raise ValueError("no valid samples remain")

        a = np.clip(a[keep], 0.0, None)
        if normalize_abundances:
            a = a / a.sum(axis=1, keepdims=True)

        self.wavelengths = torch.from_numpy(wl.astype(np.float32))
        self.spectra = torch.from_numpy(x[keep].astype(np.float32))
        self.abundances = torch.from_numpy(a.astype(np.float32))
        self.class_targets = torch.from_numpy(
            cls[keep].astype(np.float32 if multilabel else np.int64)
        )
        self.sam_angles = (
            None if angles is None else torch.from_numpy(angles[keep].astype(np.float32))
        )
        self.group_ids = None if groups is None else groups[keep]
        self.sample_ids = None if ids is None else ids[keep]
        self.mineral_names = names
        self.multilabel = multilabel
        self.num_classes = num_classes
        self.invalid_report = report
        self.num_dropped = int(failed.sum())

    @property
    def num_bands(self) -> int:
        return self.spectra.shape[1]

    def __len__(self) -> int:
        return self.spectra.shape[0]

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        item = {
            "spectrum": self.spectra[index],
            "class_target": self.class_targets[index],
            "abundance": self.abundances[index],
        }
        if self.sam_angles is not None:
            item["sam_angle"] = self.sam_angles[index]
        return item

    def subset(self, indices) -> "SpectralDataset":
        idx = np.asarray(indices, dtype=np.int64)
        if idx.ndim != 1:
            raise ValueError("indices must be 1D")
        if idx.size and (idx.min() < 0 or idx.max() >= len(self)):
            raise IndexError("indices out of range")
        t = torch.from_numpy(idx)

        new = copy.copy(self)
        new.spectra = self.spectra[t]
        new.abundances = self.abundances[t]
        new.class_targets = self.class_targets[t]
        new.sam_angles = _take(self.sam_angles, t)
        new.group_ids = None if self.group_ids is None else self.group_ids[idx]
        new.sample_ids = None if self.sample_ids is None else self.sample_ids[idx]
        new.invalid_report = {}
        new.num_dropped = 0
        return new

    @classmethod
    def from_npz(cls, path: str | Path, **kwargs) -> "SpectralDataset":
        with np.load(path, allow_pickle=False) as data:
            missing = [k for k in _REQUIRED_KEYS if k not in data.files]
            if missing:
                raise KeyError(f"{path} is missing required arrays: {missing}")
            arrays = {k: data[k] for k in _REQUIRED_KEYS}
            arrays.update({k: data[k] for k in _OPTIONAL_KEYS if k in data.files})
        if "mineral_names" in arrays:
            arrays["mineral_names"] = [str(m) for m in arrays["mineral_names"]]
        arrays.update(kwargs)
        return cls(**arrays)


def split_by_group(
    group_ids,
    fractions: Sequence[float] = (0.7, 0.15, 0.15),
    seed: int = 42,
) -> dict[str, np.ndarray]:
    frac = np.asarray(fractions, dtype=np.float64)
    if frac.shape != (3,) or (frac < 0).any() or frac[0] <= 0:
        raise ValueError("fractions must be (train, val, test), non-negative, train > 0")
    if not np.isclose(frac.sum(), 1.0):
        raise ValueError("fractions must sum to 1")

    gid = np.asarray(group_ids)
    if gid.ndim != 1 or gid.size == 0:
        raise ValueError("group_ids must be a non-empty 1D array")

    groups, inverse, counts = np.unique(gid, return_inverse=True, return_counts=True)
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(groups))
    sizes = counts[order]
    midpoints = np.cumsum(sizes) - sizes / 2.0
    bounds = np.cumsum(frac)[:-1] * gid.size

    assignment = np.empty(len(groups), dtype=np.int64)
    assignment[order] = np.searchsorted(bounds, midpoints, side="right")
    sample_split = assignment[inverse]

    splits = {
        name: np.flatnonzero(sample_split == k) for k, name in enumerate(SPLIT_NAMES)
    }
    empty = [n for n, f in zip(SPLIT_NAMES, frac) if f > 0 and splits[n].size == 0]
    if empty:
        raise ValueError(
            f"splits {empty} are empty; {len(groups)} groups are not enough for fractions {tuple(frac)}"
        )
    return splits


def assert_disjoint_groups(datasets: Mapping[str, SpectralDataset]) -> None:
    seen: dict[str, set] = {}
    for name, ds in datasets.items():
        if ds.group_ids is None:
            raise ValueError(f"dataset '{name}' has no group_ids")
        current = set(np.unique(ds.group_ids).tolist())
        for other, groups in seen.items():
            shared = current & groups
            if shared:
                raise ValueError(
                    f"{len(shared)} groups appear in both '{other}' and '{name}'"
                )
        seen[name] = current


def build_datasets(
    source: str | Path | SpectralDataset,
    *,
    fractions: Sequence[float] = (0.7, 0.15, 0.15),
    seed: int = 42,
    **dataset_kwargs,
) -> dict[str, SpectralDataset]:
    if isinstance(source, SpectralDataset):
        if dataset_kwargs:
            raise ValueError("dataset_kwargs only apply when loading from a file")
        full = source
    else:
        full = SpectralDataset.from_npz(source, **dataset_kwargs)

    if full.group_ids is None:
        raise ValueError("group_ids are required for a leakage-free split")

    indices = split_by_group(full.group_ids, fractions, seed)
    datasets = {name: full.subset(idx) for name, idx in indices.items() if idx.size}
    assert_disjoint_groups(datasets)
    return datasets
