from __future__ import annotations

import colorsys
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

logger = logging.getLogger(__name__)

ABUNDANCE_NODATA = -9999.0
CLASS_NODATA = 0
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_PALETTE = (
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
    "#e377c2", "#bcbd22", "#17becf", "#aec7e8", "#ffbb78", "#98df8a",
    "#ff9896", "#c5b0d5", "#c49c94", "#f7b6d2",
)

GeoTiffWriter = Callable[..., None]


@dataclass(frozen=True)
class GeoMetadata:
    crs: Any
    transform: Any
    height: int
    width: int

    @classmethod
    def from_source(cls, source: Any) -> "GeoMetadata":
        if isinstance(source, cls):
            return source

        def get(key: str) -> Any:
            if isinstance(source, Mapping):
                return source.get(key)
            return getattr(source, key, None)

        values = {k: get(k) for k in ("crs", "transform", "height", "width")}
        missing = [k for k in ("transform", "height", "width") if values[k] is None]
        if missing:
            raise ValueError(f"raster metadata is missing: {missing}")
        if values["crs"] is None:
            logger.warning("raster metadata has no CRS; outputs will be unreferenced")
        return cls(values["crs"], values["transform"], int(values["height"]), int(values["width"]))


@dataclass
class MineralPredictions:
    class_ids: np.ndarray
    abundances: np.ndarray
    mineral_names: Sequence[str]
    confidence: np.ndarray | None = None
    valid_mask: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.mineral_names = tuple(str(m) for m in self.mineral_names)
        if len(set(n.lower() for n in self.mineral_names)) != len(self.mineral_names):
            raise ValueError("mineral_names must be unique")


@dataclass
class MapGrids:
    class_ids: np.ndarray
    abundances: np.ndarray
    confidence: np.ndarray | None


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    if not _HEX.match(color):
        raise ValueError(f"invalid color '{color}', expected '#RRGGBB'")
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def _hashed_color(name: str) -> str:
    digest = hashlib.md5(name.lower().encode("utf-8")).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.85)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


@dataclass
class ColorConfig:
    colors: dict[str, str] = field(default_factory=dict)
    palette: Sequence[str] = DEFAULT_PALETTE
    unclassified_color: str = "#d9d9d9"
    unclassified_label: str = "Unclassified / no data"

    @classmethod
    def from_json(cls, path: str | Path) -> "ColorConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls(**json.load(f))

    def resolve(self, mineral_names: Sequence[str]) -> dict[str, str]:
        overrides = {k.lower(): v for k, v in self.colors.items()}
        resolved: dict[str, str] = {}
        for index, name in enumerate(mineral_names):
            if name.lower() in overrides:
                color = overrides[name.lower()]
            elif index < len(self.palette):
                color = self.palette[index]
            else:
                color = _hashed_color(name)
            _hex_to_rgb(color)
            resolved[name] = color.lower()

        _hex_to_rgb(self.unclassified_color)
        used = list(resolved.values()) + [self.unclassified_color.lower()]
        if len(set(used)) != len(used):
            raise ValueError("colors must be unique across minerals and the unclassified color")
        return resolved


def rasterio_writer(
    path: str | Path,
    array: np.ndarray,
    meta: GeoMetadata,
    *,
    dtype: str,
    nodata: float | None = None,
    band_names: Sequence[str] | None = None,
) -> None:
    import rasterio

    data = np.asarray(array)
    if data.ndim == 2:
        data = data[None]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=meta.height,
        width=meta.width,
        count=data.shape[0],
        dtype=dtype,
        crs=meta.crs,
        transform=meta.transform,
        nodata=nodata,
        compress="deflate",
    ) as dst:
        dst.write(data.astype(dtype))
        for i, name in enumerate(band_names or [], start=1):
            dst.set_band_description(i, name)


def _to_grid(values, flat_ndim: int, valid_mask, height: int, width: int, fill):
    arr = np.asarray(values)
    if arr.ndim == flat_ndim + 1:
        if arr.shape[:2] != (height, width):
            raise ValueError(f"gridded predictions have shape {arr.shape[:2]}, expected {(height, width)}")
        return arr
    if arr.ndim != flat_ndim:
        raise ValueError(f"unexpected prediction ndim {arr.ndim}")

    if valid_mask is None:
        if arr.shape[0] != height * width:
            raise ValueError(f"{arr.shape[0]} predictions do not cover {height * width} pixels; pass valid_mask")
        return arr.reshape((height, width) + arr.shape[1:])

    if arr.shape[0] != int(valid_mask.sum()):
        raise ValueError(f"{arr.shape[0]} predictions but valid_mask selects {int(valid_mask.sum())} pixels")
    dtype = np.float32 if isinstance(fill, float) else np.int32
    grid = np.full((height, width) + arr.shape[1:], fill, dtype=dtype)
    grid[valid_mask] = arr
    return grid


def build_grids(predictions: MineralPredictions, height: int, width: int) -> MapGrids:
    num_minerals = len(predictions.mineral_names)
    valid_mask = None
    if predictions.valid_mask is not None:
        valid_mask = np.asarray(predictions.valid_mask, dtype=bool)
        if valid_mask.shape != (height, width):
            raise ValueError(f"valid_mask has shape {valid_mask.shape}, expected {(height, width)}")

    class_ids = _to_grid(predictions.class_ids, 1, valid_mask, height, width, -1).astype(np.int32)
    class_ids[(class_ids < 0) | (class_ids >= num_minerals)] = -1

    abundances = _to_grid(predictions.abundances, 2, valid_mask, height, width, np.nan).astype(np.float32)
    if abundances.shape[-1] != num_minerals:
        raise ValueError(f"abundances have {abundances.shape[-1]} minerals, expected {num_minerals}")
    abundances = np.moveaxis(abundances, -1, 0)
    abundances[:, ~np.isfinite(abundances).all(axis=0)] = np.nan

    confidence = None
    if predictions.confidence is not None:
        confidence = _to_grid(predictions.confidence, 1, valid_mask, height, width, np.nan).astype(np.float32)

    return MapGrids(class_ids, abundances, confidence)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _extent(meta: GeoMetadata) -> tuple[float, float, float, float]:
    t = meta.transform
    return (t.c, t.c + meta.width * t.a, t.f + meta.height * t.e, t.f)


def render_preview(
    path: str | Path,
    rgb: np.ndarray,
    meta: GeoMetadata,
    legend_entries: Sequence[tuple[str, str]],
    title: str,
) -> None:
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch

    fig = Figure(figsize=(10, 7), dpi=150)
    ax = fig.add_subplot(111)
    ax.imshow(rgb, extent=_extent(meta), interpolation="nearest")
    ax.set_title(title)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.ticklabel_format(style="plain", useOffset=False)
    handles = [Patch(facecolor=color, edgecolor="black", label=label) for label, color in legend_entries]
    ax.legend(
        handles=handles,
        title="Mineral",
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        frameon=True,
    )
    fig.savefig(path, bbox_inches="tight")


def write_mineral_maps(
    predictions: MineralPredictions,
    meta: Any,
    out_dir: str | Path,
    *,
    prefix: str = "mineral",
    color_config: ColorConfig | None = None,
    min_confidence: float | None = None,
    per_mineral_files: bool = True,
    write_preview: bool = True,
    title: str | None = None,
    writer: GeoTiffWriter | None = None,
) -> dict[str, Path]:
    meta = GeoMetadata.from_source(meta)
    writer = writer or rasterio_writer
    color_config = color_config or ColorConfig()
    names = predictions.mineral_names
    colors = color_config.resolve(names)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    grids = build_grids(predictions, meta.height, meta.width)

    class_ids = grids.class_ids.copy()
    if min_confidence is not None:
        if grids.confidence is None:
            raise ValueError("min_confidence requires predictions.confidence")
        with np.errstate(invalid="ignore"):
            class_ids[~(grids.confidence >= min_confidence)] = -1

    labels = class_ids + 1
    class_dtype = "uint8" if len(names) + 1 <= 255 else "uint16"
    outputs: dict[str, Path] = {}

    outputs["classification"] = out_dir / f"{prefix}_classification.tif"
    writer(
        outputs["classification"],
        labels.astype(class_dtype),
        meta,
        dtype=class_dtype,
        nodata=CLASS_NODATA,
        band_names=["mineral_class"],
    )

    lut = np.zeros((len(names) + 1, 3), dtype=np.uint8)
    lut[0] = _hex_to_rgb(color_config.unclassified_color)
    for i, name in enumerate(names, start=1):
        lut[i] = _hex_to_rgb(colors[name])
    rgb = lut[labels]

    outputs["classification_rgb"] = out_dir / f"{prefix}_classification_rgb.tif"
    writer(
        outputs["classification_rgb"],
        np.moveaxis(rgb, -1, 0),
        meta,
        dtype="uint8",
        nodata=None,
        band_names=["red", "green", "blue"],
    )

    abundance = np.where(np.isfinite(grids.abundances), grids.abundances, ABUNDANCE_NODATA)
    outputs["abundance"] = out_dir / f"{prefix}_abundance.tif"
    writer(
        outputs["abundance"],
        abundance,
        meta,
        dtype="float32",
        nodata=ABUNDANCE_NODATA,
        band_names=list(names),
    )
    if per_mineral_files:
        for i, name in enumerate(names):
            key = f"abundance_{_slug(name)}"
            outputs[key] = out_dir / f"{prefix}_{key}.tif"
            writer(
                outputs[key],
                abundance[i],
                meta,
                dtype="float32",
                nodata=ABUNDANCE_NODATA,
                band_names=[name],
            )

    if grids.confidence is not None:
        outputs["confidence"] = out_dir / f"{prefix}_confidence.tif"
        writer(
            outputs["confidence"],
            np.where(np.isfinite(grids.confidence), grids.confidence, ABUNDANCE_NODATA),
            meta,
            dtype="float32",
            nodata=ABUNDANCE_NODATA,
            band_names=["confidence"],
        )

    counts = np.bincount(labels.ravel(), minlength=len(names) + 1)
    total = labels.size
    legend = {
        "unclassified": {
            "value": CLASS_NODATA,
            "label": color_config.unclassified_label,
            "color": color_config.unclassified_color.lower(),
            "pixel_count": int(counts[0]),
            "fraction": float(counts[0] / total),
        },
        "minerals": [
            {
                "value": i + 1,
                "mineral": name,
                "color": colors[name],
                "pixel_count": int(counts[i + 1]),
                "fraction": float(counts[i + 1] / total),
            }
            for i, name in enumerate(names)
        ],
        "abundance_bands": list(names),
        "abundance_nodata": ABUNDANCE_NODATA,
    }
    outputs["legend"] = out_dir / f"{prefix}_legend.json"
    with open(outputs["legend"], "w", encoding="utf-8") as f:
        json.dump(legend, f, indent=2)

    if write_preview:
        entries = [
            (f"{m['mineral']} ({m['fraction'] * 100:.1f}%)", m["color"])
            for m in legend["minerals"]
            if m["pixel_count"] > 0
        ]
        if counts[0] > 0:
            entries.append(
                (
                    f"{color_config.unclassified_label} ({legend['unclassified']['fraction'] * 100:.1f}%)",
                    color_config.unclassified_color,
                )
            )
        outputs["preview"] = out_dir / f"{prefix}_preview.png"
        render_preview(outputs["preview"], rgb, meta, entries, title or f"{prefix} mineral map")

    return outputs
