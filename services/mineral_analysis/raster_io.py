"""Raster input/output for the mineral-analysis service (Rasterio + NumPy).

Georeferencing (CRS, transform, width, height) of every output comes from the
input raster. No wavelengths are ever guessed.
"""
import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.transform import array_bounds

WAVELENGTH_KEYS = ("wavelength", "wavelengths")
UNIT_KEYS = ("wavelength_units", "wavelength_unit")


@dataclass
class Raster:
    data: np.ndarray  # (bands, height, width) float32; nodata -> NaN
    profile: dict  # source rasterio profile (its dtype describes the file, not `data`)
    wavelengths: Optional[np.ndarray] = None  # None if not found in metadata
    wavelength_units: Optional[str] = None  # as written in metadata; never converted
    good_quality_mask: Optional[np.ndarray] = None  # True for valid bands, False for noisy/atmospheric bands

    @property
    def count(self):
        return self.data.shape[0]

    @property
    def height(self):
        return self.data.shape[1]

    @property
    def width(self):
        return self.data.shape[2]

    @property
    def crs(self):
        return self.profile.get("crs")

    @property
    def transform(self):
        return self.profile["transform"]

    @property
    def nodata(self):
        return self.profile.get("nodata")

    @property
    def resolution(self):
        """(x_size, y_size) in CRS units."""
        t = self.transform
        return abs(t.a), abs(t.e)

    @property
    def bounds(self):
        """(west, south, east, north)."""
        return array_bounds(self.height, self.width, self.transform)


def _numbers(text):
    """Parse '{0.45, 0.46}' / '0.45,0.46' into a float array, or None."""
    try:
        parts = str(text).strip("{} ").replace(";", ",").split(",")
        return np.array([float(p) for p in parts if p.strip()], dtype=float)
    except ValueError:
        return None


def _lookup(tags, keys):
    tags = {k.lower(): v for k, v in tags.items()}
    return next((tags[k] for k in keys if k in tags), None)


def _envi_hdr_wavelengths(path, count):
    """Parse wavelengths + units from an ENVI '.hdr' sidecar next to `path`.

    Returns (values, units) or (None, None). Handles the brace list spanning
    multiple lines. Used as a fallback for ENVI .bsq/.dat/.img inputs when GDAL
    does not surface the wavelengths as raster tags.
    """
    p = Path(path)
    for hdr in (p.with_suffix(".hdr"), p.parent / (p.name + ".hdr")):
        if not hdr.is_file():
            continue
        try:
            text = hdr.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"wavelength\s*=\s*\{([^}]*)\}", text, re.IGNORECASE | re.DOTALL)
            if not m:
                continue
            values = _numbers(m.group(1))
            if values is None or values.size != count:
                continue
            um = re.search(r"wavelength\s+units?\s*=\s*\{?\s*([A-Za-z]+)", text, re.IGNORECASE)
            units = um.group(1) if um else None
            return values, units
        except Exception:
            continue
    return None, None


def _read_wavelengths(src, path=None):
    """Return (wavelengths, units, quality_mask) from metadata, or (None, None, None) if unavailable."""
    values, units, quality = None, None, None
    for ns in ("ENVI", None):  # dataset-level: ENVI header domain, then default
        raw = _lookup(src.tags(ns=ns), WAVELENGTH_KEYS)
        if raw is not None:
            values, units = _numbers(raw), _lookup(src.tags(ns=ns), UNIT_KEYS)
            break
    else:  # per-band tags, only if every band has one
        raws = [_lookup(src.tags(i), WAVELENGTH_KEYS) for i in src.indexes]
        if all(r is not None for r in raws):
            values = _numbers(",".join(str(r) for r in raws))
            units = _lookup(src.tags(1), UNIT_KEYS)

    if (values is None or values.size != src.count) and path is not None:
        p = Path(path)
        candidates = [
            p.with_name("band_metadata.json"),
            p.with_suffix(".json"),
            p.parent / "band_metadata.json",
        ]
        for c in candidates:
            if c.is_file():
                try:
                    with open(c, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    if "bands" in meta and isinstance(meta["bands"], list):
                        wls = [
                            b.get("wavelength_nm") or b.get("wavelength")
                            for b in meta["bands"]
                            if isinstance(b, dict) and (b.get("wavelength_nm") is not None or b.get("wavelength") is not None)
                        ]
                        if len(wls) == src.count:
                            values = np.array(wls, dtype=float)
                            units = "nm"
                            q_flags = [
                                b.get("good_quality", True)
                                for b in meta["bands"]
                                if isinstance(b, dict) and (b.get("wavelength_nm") is not None or b.get("wavelength") is not None)
                            ]
                            quality = np.array(q_flags, dtype=bool)
                            break
                    elif "wavelengths" in meta:
                        wls = meta["wavelengths"]
                        if len(wls) == src.count:
                            values = np.array(wls, dtype=float)
                            units = meta.get("wavelength_units", meta.get("wavelength_unit", "nm"))
                            if "good_quality" in meta and len(meta["good_quality"]) == src.count:
                                quality = np.array(meta["good_quality"], dtype=bool)
                            break
                except Exception:
                    pass

    if (values is None or values.size != src.count) and path is not None:
        hdr_values, hdr_units = _envi_hdr_wavelengths(path, src.count)
        if hdr_values is not None:
            values, units = hdr_values, hdr_units or units

    if values is None or values.size != src.count or not np.isfinite(values).all():
        if values is not None:
            warnings.warn("wavelength metadata found but invalid; treating as unavailable")
        return None, None, None
    return values, units, quality


def read_raster(path, apply_scale_offset=False):
    """Read a hyperspectral GeoTIFF into a Raster.

    Nodata pixels become NaN. If the file declares per-band scale/offset, it is
    applied only when apply_scale_offset=True (otherwise a warning is issued).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"raster not found: {path}")

    with rasterio.open(path) as src:
        if src.crs is None:
            warnings.warn("raster has no CRS; outputs will not be georeferenced")
        data = np.ma.filled(src.read(masked=True).astype("float32"), np.nan)

        scales = np.asarray(src.scales, dtype="float32")
        offsets = np.asarray(src.offsets, dtype="float32")
        if np.any(scales != 1) or np.any(offsets != 0):
            if apply_scale_offset:
                data = data * scales[:, None, None] + offsets[:, None, None]
            else:
                warnings.warn(
                    "raster declares scale/offset that was NOT applied; "
                    "use apply_scale_offset=True if values should be scaled"
                )

        wavelengths, units, quality = _read_wavelengths(src, path=path)
        return Raster(data, dict(src.profile), wavelengths, units, quality)


def to_pixels(data):
    """(bands, h, w) -> (h*w, bands) for SAM/SMA (a view, no copy)."""
    if data.ndim != 3:
        raise ValueError("data must be (bands, height, width)")
    return data.reshape(data.shape[0], -1).T


def to_map(values, height, width):
    """(h*w,) -> (h, w); (h*w, k) -> (k, h, w). Inverse of to_pixels for results."""
    v = np.asarray(values)
    if v.ndim not in (1, 2) or v.shape[0] != height * width:
        raise ValueError(f"values must have {height * width} rows (height*width)")
    return v.reshape(height, width) if v.ndim == 1 else v.T.reshape(v.shape[1], height, width)


def _write(path, ref, array, dtype, nodata, descriptions=None, tags=None, colormap=None):
    arr = np.asarray(array)
    if arr.ndim == 2:
        arr = arr[None]
    if arr.ndim != 3 or arr.shape[1:] != (ref.height, ref.width):
        raise ValueError(
            f"array shape {arr.shape} does not match raster size "
            f"({ref.height}, {ref.width}); expected (h, w) or (n, h, w)"
        )
    if descriptions is not None and len(descriptions) != arr.shape[0]:
        raise ValueError(f"{len(descriptions)} names for {arr.shape[0]} band(s)")

    # Fresh profile: only georeferencing comes from the input, nothing else is inherited.
    profile = dict(
        driver="GTiff", height=ref.height, width=ref.width, count=arr.shape[0],
        dtype=dtype, crs=ref.crs, transform=ref.transform, nodata=nodata, compress="lzw",
    )
    if colormap is not None:
        profile["photometric"] = "palette"

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype(dtype))
        for i, name in enumerate(descriptions or [], start=1):
            dst.set_band_description(i, str(name))
        if colormap is not None:
            for b in range(1, arr.shape[0] + 1):
                dst.write_colormap(b, colormap)
        if tags:
            dst.update_tags(**tags)
    return path


def _write_sidecar_styles(path: Path, colormap: dict, class_names: dict, nodata: int = 0) -> None:
    """Generate .qml (QGIS Layer Style) and .clr (ESRI Color Map) sidecar files."""
    path = Path(path)
    base_stem = path.stem

    # 1. Write ESRI .clr file
    clr_path = path.parent / f"{base_stem}.clr"
    clr_lines = []
    for cid, color in sorted(colormap.items()):
        r, g, b = color[0], color[1], color[2]
        name = class_names.get(cid, "Unclassified" if cid == nodata else f"Class_{cid}")
        clr_lines.append(f"{cid} {r} {g} {b} {name}")
    try:
        with open(clr_path, "w", encoding="utf-8") as f:
            f.write("\n".join(clr_lines) + "\n")
    except Exception as exc:
        warnings.warn(f"Could not write .clr sidecar: {exc}")

    # 2. Write QGIS .qml file
    qml_path = path.parent / f"{base_stem}.qml"
    entries = []
    for cid, color in sorted(colormap.items()):
        r, g, b = color[0], color[1], color[2]
        alpha = 0 if cid == nodata else (color[3] if len(color) > 3 else 255)
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        name = class_names.get(cid, "Unclassified / Background" if cid == nodata else f"Class {cid}")
        entries.append(f'        <paletteEntry value="{cid}" color="{hex_color}" alpha="{alpha}" label="{name}"/>')
    palette_xml = "\n".join(entries)
    qml_content = f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.28.0" styleCategories="AllStyleCategories">
  <pipe>
    <rasterrenderer type="paletted" opacity="1" band="1">
      <rasterTransparency/>
      <colorPalette>
{palette_xml}
      </colorPalette>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0"/>
    <huesaturation colorizeGreen="128" colorizeBlue="128" colorizeOn="0" colorizeStrength="100" colorizeRed="255" saturation="0" grayscaleMode="0"/>
    <rasterresampler maxOversampling="2"/>
  </pipe>
  <blendMode>0</blendMode>
</qgis>
"""
    try:
        with open(qml_path, "w", encoding="utf-8") as f:
            f.write(qml_content)
    except Exception as exc:
        warnings.warn(f"Could not write .qml sidecar: {exc}")


def write_classification(path, ref, class_ids, class_names=None, nodata=0, colormap=None, dtype=None):
    """Write an integer class map (h, w). The caller chooses the ids.

    class_names: optional {id: name}, stored as tags class_<id>. `nodata` is the id
    for unclassified/invalid pixels and must not be used for a real class.
    colormap: optional dict {class_id: (r, g, b, [a])} for paletted GeoTIFF and QGIS/ESRI styles.
    """
    ids = np.asarray(class_ids)
    if not np.issubdtype(ids.dtype, np.integer):
        raise ValueError("class_ids must be an integer array")

    if dtype is None:
        # Default to uint8 if values fit in 0..255 (enables GDAL Color Tables), else int16
        if ids.size and ids.min() >= 0 and ids.max() <= 255:
            dtype = "uint8"
        else:
            dtype = "int16"

    tags = None
    if class_names:
        if nodata in class_names:
            raise ValueError(f"id {nodata} is the nodata value; it cannot name a class")
        missing = set(np.unique(ids).tolist()) - set(class_names) - {nodata}
        if missing:
            raise ValueError(f"class ids without a name: {sorted(missing)}")
        tags = {f"class_{k}": str(v) for k, v in class_names.items()}

    written_path = _write(path, ref, ids, dtype, nodata, descriptions=["mineral_classification"], tags=tags, colormap=colormap)

    if colormap and class_names:
        _write_sidecar_styles(Path(path), colormap, class_names, nodata)

    return written_path


def write_abundance(path, ref, abundances, names=None):
    """Write abundance map(s): (h, w) for one mineral or (n, h, w), one band each."""
    tags = {"description": "estimated abundance fraction (model output, not validated)"}
    return _write(path, ref, abundances, "float32", np.nan, names, tags)


def write_quality(path, ref, quality):
    """Write the (h, w) quality/confidence map from confidence.py."""
    tags = {"description": "heuristic quality indicator in [0, 1]; not a probability"}
    return _write(path, ref, quality, "float32", np.nan, ["quality"], tags)


def read_hyperspectral(path, apply_scale_offset=False):
    """Adapter for the NN inference path (nn_main.py).

    Returns (cube, wavelengths, raster) where cube is (height, width, bands) and
    `raster` is the Raster (its crs/transform/height/width feed
    mineral_map.GeoMetadata.from_source).
    """
    r = read_raster(path, apply_scale_offset)
    if r.wavelengths is None:
        raise ValueError("input raster has no usable wavelength metadata")
    cube = np.moveaxis(r.data, 0, -1)  # (bands, h, w) -> (h, w, bands)
    return cube, r.wavelengths, r
