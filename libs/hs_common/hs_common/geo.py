"""
Georeferenced outputs: Cloud-Optimized GeoTIFF for download/GIS, colourised
PNG + bounds for the web map, and colour tables shared with the frontend.
"""

import io
import json
import tempfile
from pathlib import Path

import numpy as np

# Colours follow the platform's validated data-viz palette (frontend uses the
# same values from each layer's stats, so map and legend can never disagree).
# Magnitude: one hue, light -> dark. Blue is the default; temperature is orange.
RAMPS = {
    "blue": ["#cde2fb", "#86b6ef", "#2a78d6", "#1c5cab", "#0d366b"],
    "orange": ["#fde4d7", "#f39d74", "#eb6834", "#a04217", "#7a3110"],
}
# Categorical maps use only palette slots 1-3 (blue, orange, aqua): the only
# set that stays distinguishable for colour-vision deficiency across ALL pairs.
SLOT = {"blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a"}
NO_IDENTITY = "#898781"                      # muted gray: "unknown"
TRANSPARENT = "#00000000"

# Minerals are coloured by spectral group; each mineral stays identifiable by
# the legend, the highlight control, the pixel inspector and the table.
MINERAL_GROUPS = {
    "Al-OH clays & micas": ("blue", ["alunite", "illite", "kaolinite", "montmorillonite", "muscovite"]),
    "Fe-bearing": ("orange", ["chlorite", "goethite", "hematite"]),
    "Carbonates, sulfates & silica": ("aqua", ["calcite", "dolomite", "gypsum", "quartz"]),
}


def mineral_palette(classes):
    """Colour per mineral class index (last class = unknown)."""
    colour = {m: SLOT[c] for c, members in MINERAL_GROUPS.values() for m in members}
    return [colour.get(name, NO_IDENTITY) for name in classes]


def mineral_group_of(name):
    for group, (_, members) in MINERAL_GROUPS.items():
        if name in members:
            return group
    return "Unknown"


# Diverging (polarity): blue <-> neutral gray <-> red
THERMAL_GLOBAL_COLORS = ["#2a78d6", "#d9d8d2", "#e34948"]
THERMAL_UHI_COLORS = ["#2a78d6", "#d9d8d2", "#e34948"]
BINARY_COLORS = [TRANSPARENT, "#e34948"]
LANDCOVER_COLORS = [TRANSPARENT, SLOT["blue"], SLOT["aqua"], SLOT["orange"]]


def bounds_from_transform(transform, width, height):
    a, b, c, d, e, f = transform
    xs = [c, c + a * width]
    ys = [f, f + e * height]
    return [min(xs), min(ys), max(xs), max(ys)]


def _hex(h):
    h = h.lstrip("#")
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return rgb + [int(h[6:8], 16) if len(h) == 8 else 255]


def colorize(data, kind, colormap=None, vmin=None, vmax=None, palette=None, nodata=255):
    """2-D array -> RGBA uint8 image. Invalid pixels are fully transparent."""
    data = np.asarray(data)
    rgba = np.zeros((*data.shape, 4), np.uint8)
    if kind == "categorical":
        pal = np.array([_hex(c) for c in palette], np.uint8)
        ok = (data != nodata) & (data < len(pal))
        rgba[ok] = pal[data[ok].astype(int)]
        return rgba
    ok = np.isfinite(data)
    if not ok.any():
        return rgba
    lo = np.nanpercentile(data[ok], 2) if vmin is None else vmin
    hi = np.nanpercentile(data[ok], 98) if vmax is None else vmax
    t = np.clip((data - lo) / max(hi - lo, 1e-12), 0, 1)
    stops = np.array([_hex(c) for c in RAMPS[colormap or "blue"]], float)
    pos = t * (len(stops) - 1)
    i0 = np.clip(np.floor(pos).astype(int), 0, len(stops) - 2)
    frac = (pos - i0)[..., None]
    col = stops[i0] * (1 - frac) + stops[i0 + 1] * frac
    rgba[ok] = col[ok].astype(np.uint8)
    rgba[~ok] = 0
    return rgba


def png_bytes(rgba):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def cog_bytes(data, transform, crs="EPSG:4326", nodata=None, tags=None, band_names=None):
    """(H, W) or (H, W, B) array -> Cloud-Optimized GeoTIFF bytes."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.shutil import copy as rio_copy
    from rasterio.transform import Affine

    arr = np.asarray(data)
    if arr.ndim == 2:
        arr = arr[..., None]
    h, w, nb = arr.shape
    dtype = arr.dtype.name if arr.dtype != np.bool_ else "uint8"
    if nodata is None:
        nodata = float("nan") if np.issubdtype(arr.dtype, np.floating) else None
    with tempfile.TemporaryDirectory() as td:
        tmp, out = Path(td) / "tmp.tif", Path(td) / "cog.tif"
        profile = {"driver": "GTiff", "width": w, "height": h, "count": nb, "dtype": dtype,
                   "crs": crs, "transform": Affine(*transform[:6]), "nodata": nodata,
                   "tiled": True, "blockxsize": 256, "blockysize": 256, "compress": "deflate"}
        with rasterio.open(tmp, "w", **profile) as dst:
            for i in range(nb):
                dst.write(arr[..., i].astype(dtype), i + 1)
                if band_names:
                    dst.set_band_description(i + 1, band_names[i])
            if tags:
                dst.update_tags(**{k: json.dumps(v) if not isinstance(v, str) else v for k, v in tags.items()})
            if min(h, w) > 512:
                dst.build_overviews([2, 4, 8], Resampling.nearest)
        rio_copy(tmp, out, driver="COG", compress="DEFLATE", blocksize=256)
        return out.read_bytes()


def read_cog(path_or_bytes):
    """-> (array (H, W[, B]), transform list, crs str, nodata)."""
    import rasterio
    from rasterio.io import MemoryFile

    def _read(src):
        arr = src.read()
        arr = arr[0] if arr.shape[0] == 1 else np.moveaxis(arr, 0, -1)
        return arr, list(src.transform)[:6], str(src.crs), src.nodata

    if isinstance(path_or_bytes, (bytes, bytearray)):
        with MemoryFile(path_or_bytes) as mf, mf.open() as src:
            return _read(src)
    with rasterio.open(path_or_bytes) as src:
        return _read(src)
