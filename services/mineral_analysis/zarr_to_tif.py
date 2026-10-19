"""Convert real_emit.zarr (NASA EMIT L2A reflectance cube) into the GeoTIFF(s)
the mineral-analysis pipeline consumes.

The Zarr store is a single v2 array shaped (rows, cols, bands) = (553, 553, 285),
little-endian float32, with .zattrs carrying the bbox, CRS, per-band wavelengths
(nm) and good-quality flags. main.py / raster_io.py need:
  * a (bands, height, width) GeoTIFF,
  * georeferencing (CRS + affine transform), and
  * wavelengths in metadata so the library can be aligned.

Primary output: real_emit_multiband_cube.tif  (== config.INPUT_RASTER)
Extras (visual QA, best-effort): RGB true-colour and NIR false-colour previews.

Run:  python zarr_to_tif.py
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np

# Point PROJ at rasterio's bundled proj_data BEFORE importing rasterio, so a
# stray system PostgreSQL/PostGIS proj.db cannot shadow it (same fix as config.py).
_spec = importlib.util.find_spec("rasterio")
if _spec and _spec.submodule_search_locations:
    _proj = os.path.join(list(_spec.submodule_search_locations)[0], "proj_data")
    if os.path.isdir(_proj):
        os.environ["PROJ_DATA"] = _proj
        os.environ["PROJ_LIB"] = _proj

import rasterio
from rasterio.transform import from_bounds

BASE_DIR = Path(__file__).resolve().parent
ZARR_PATH = BASE_DIR / "real_emit.zarr"
CUBE_TIF = BASE_DIR / "real_emit_multiband_cube.tif"
RGB_TIF = BASE_DIR / "real_emit_rgb_truecolor.tif"
NIR_TIF = BASE_DIR / "real_emit_nir_falsecolor.tif"
BSQ_OUT = BASE_DIR / "real_emit_good.bsq"

NODATA = -9999.0  # EMIT fill; chosen so legitimate ~0 reflectance is NOT masked


def load_zarr(path):
    """Return (array[rows, cols, bands] float32, attrs dict)."""
    try:
        import zarr
    except ImportError:
        sys.exit(
            "The 'zarr' package is required to read the store.\n"
            "Install it with:  pip install zarr numcodecs"
        )
    arr = zarr.open(str(path), mode="r")
    data = np.asarray(arr[:], dtype=np.float32)  # materialise the full cube
    attrs = dict(arr.attrs)
    return data, attrs


def wavelengths_and_quality(attrs, n_bands):
    """Pull wavelengths (nm) and good-quality flags from .zattrs, falling back
    to the band_metadata.json sidecar, then to an index if all else fails."""
    wl = attrs.get("wavelengths") or attrs.get("wavelength")
    good = attrs.get("good_wavelengths") or attrs.get("good_quality")

    if wl is None:
        sidecar = BASE_DIR / "band_metadata.json"
        if sidecar.is_file():
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            bands = meta.get("bands", [])
            wl = [b.get("wavelength_nm") or b.get("wavelength") for b in bands]
            good = [b.get("good_quality", True) for b in bands]

    if wl is None or len(wl) != n_bands:
        raise ValueError(
            f"could not resolve {n_bands} wavelengths from zarr .zattrs or band_metadata.json"
        )
    wl = [float(w) for w in wl]
    if good is None or len(good) != n_bands:
        good = [True] * n_bands
    good = [bool(g) for g in good]
    return wl, good


def build_transform(attrs, height, width):
    """North-up affine transform from the bbox [west, south, east, north]."""
    bbox = attrs.get("bbox")
    if not bbox or len(bbox) != 4:
        raise ValueError(f"zarr .zattrs has no usable 4-element bbox: {bbox!r}")
    west, south, east, north = (float(v) for v in bbox)
    return from_bounds(west, south, east, north, width, height)


def write_cube(data_bhw, transform, crs, wl, good):
    """Write the full (bands, h, w) reflectance cube.

    Wavelengths and good-quality flags are intentionally NOT written as raster
    tags: raster_io._read_wavelengths then falls through to the band_metadata.json
    sidecar (already present next to the cube), which carries BOTH wavelength_nm
    and good_quality.  That is what makes `main.py --good-bands-only` work.
    """
    bands, height, width = data_bhw.shape
    profile = dict(
        driver="GTiff", height=height, width=width, count=bands,
        dtype="float32", crs=crs, transform=transform, nodata=NODATA,
        compress="lzw", interleave="band", bigtiff="yes",
    )
    with rasterio.open(CUBE_TIF, "w", **profile) as dst:
        dst.write(data_bhw)
        # Human-readable band labels only (not parsed as wavelength tags).
        for i, (w, g) in enumerate(zip(wl, good), start=1):
            flag = "" if g else " [bad]"
            dst.set_band_description(i, f"{w:.2f} nm{flag}")
    return CUBE_TIF


def write_bsq(data_bhw, wl, good, good_only=True, transform=None, crs=None):
    """Write an ENVI band-sequential (.bsq) cube + .hdr sidecar.

    good_only=True exports only the good EMIT bands (the atmospheric water-vapour
    and noisy bands flagged in band_metadata.json are dropped).  When `transform`
    (and EPSG:4326 `crs`) are given, geographic `map info` is written so GDAL/
    rasterio read the file georeferenced.
    """
    good = np.asarray(good, dtype=bool)
    sel = np.flatnonzero(good) if good_only else np.arange(len(wl))
    sub = np.ascontiguousarray(data_bhw[sel], dtype="<f4")  # little-endian float32
    bands, height, width = sub.shape
    sub.tofile(str(BSQ_OUT))  # BSQ = bands outer loop, then rows, then cols

    wl_sel = [wl[i] for i in sel]
    desc = "EMIT L2A reflectance, good bands only" if good_only else "EMIT L2A reflectance, all bands"
    hdr = BSQ_OUT.with_suffix(".hdr")
    lines = [
        "ENVI",
        "description = {" + desc + "}",
        f"samples = {width}",
        f"lines = {height}",
        f"bands = {bands}",
        "header offset = 0",
        "file type = ENVI Standard",
        "data type = 4",            # 4 = 32-bit float
        "interleave = bsq",
        "byte order = 0",           # 0 = little-endian
        f"data ignore value = {NODATA}",
    ]
    if transform is not None:
        ulx, uly = transform.c, transform.f           # upper-left corner
        xsize, ysize = abs(transform.a), abs(transform.e)
        lines += [
            "map info = {Geographic Lat/Lon, 1.0000, 1.0000, "
            f"{ulx:.10f}, {uly:.10f}, {xsize:.12f}, {ysize:.12f}, WGS-84, units=Degrees}}",
            'coordinate system string = {GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
            'SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
            'UNIT["Degree",0.0174532925199433]]}',
        ]
    lines += [
        "wavelength units = Nanometers",
        "wavelength = {" + ", ".join(f"{w:.6f}" for w in wl_sel) + "}",
    ]
    hdr.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return BSQ_OUT, bands


def _stretch(band, lo_pct=2, hi_pct=98):
    """Percentile stretch a single band to uint8 for preview rasters."""
    valid = band[np.isfinite(band) & (band > NODATA)]
    if valid.size == 0:
        return np.zeros(band.shape, dtype=np.uint8)
    lo, hi = np.percentile(valid, [lo_pct, hi_pct])
    if hi <= lo:
        hi = lo + 1e-6
    out = np.clip((band - lo) / (hi - lo), 0, 1) * 255
    return out.astype(np.uint8)


def _nearest_band(wl, target):
    return int(np.argmin(np.abs(np.asarray(wl) - target)))


def write_previews(data_bhw, transform, crs, wl):
    """Best-effort 3-band uint8 previews; failure here must not block the cube."""
    try:
        combos = [
            (RGB_TIF, [640.0, 550.0, 470.0]),   # true colour: R, G, B
            (NIR_TIF, [860.0, 650.0, 550.0]),   # false colour: NIR, R, G
        ]
        for path, targets in combos:
            idx = [_nearest_band(wl, t) for t in targets]
            stacked = np.stack([_stretch(data_bhw[i]) for i in idx])
            profile = dict(
                driver="GTiff", height=stacked.shape[1], width=stacked.shape[2],
                count=3, dtype="uint8", crs=crs, transform=transform,
                photometric="rgb", compress="lzw",
            )
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(stacked)
            print(f"  wrote preview {path.name} from bands {idx} "
                  f"(~{', '.join(f'{wl[i]:.0f}nm' for i in idx)})")
    except Exception as exc:  # previews are optional
        print(f"  WARNING: preview generation skipped: {exc}")


def main():
    if not ZARR_PATH.exists():
        sys.exit(f"zarr store not found: {ZARR_PATH}")

    print(f"Reading {ZARR_PATH} ...")
    data, attrs = load_zarr(ZARR_PATH)
    if data.ndim != 3:
        sys.exit(f"expected a 3-D array, got shape {data.shape}")
    rows, cols, bands = data.shape
    print(f"  loaded cube: rows={rows} cols={cols} bands={bands} dtype={data.dtype}")

    wl, good = wavelengths_and_quality(attrs, bands)
    crs = attrs.get("crs", "EPSG:4326")
    transform = build_transform(attrs, rows, cols)
    print(f"  crs={crs}  wl={wl[0]:.1f}-{wl[-1]:.1f} nm  good_bands={sum(good)}/{bands}")

    # (rows, cols, bands) -> (bands, rows, cols); make non-finite values nodata.
    cube = np.transpose(data, (2, 0, 1)).copy()
    cube[~np.isfinite(cube)] = NODATA

    out = write_cube(cube, transform, crs, wl, good)
    print(f"Wrote {out}  ({out.stat().st_size / 1e6:.1f} MB)")

    bsq_path, n_good = write_bsq(cube, wl, good, good_only=True, transform=transform, crs=crs)
    print(f"Wrote {bsq_path.name} + .hdr  ({n_good} good bands of {bands}, georeferenced)")

    write_previews(cube, transform, crs, wl)
    print("Done. main.py will now pick up real_emit_multiband_cube.tif automatically.")


if __name__ == "__main__":
    main()
