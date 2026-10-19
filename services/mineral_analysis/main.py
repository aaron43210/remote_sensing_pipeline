"""Entry point: runs the mineral-analysis workflow end to end (standalone, no Kafka).

Run from this folder:  python main.py [--input R.tif] [--library DIR] [--output DIR]
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure local service modules can be imported regardless of working directory
_service_dir = str(Path(__file__).resolve().parent)
if _service_dir not in sys.path:
    sys.path.insert(0, _service_dir)

import numpy as np

import config
import confidence
import continuum
import raster_io
import sam
import sma
import spectral_library

METADATA_FILE = "processing_metadata.json"
log = logging.getLogger("mineral_analysis")


def _select_bands(wavelengths, ranges, good_quality_mask=None):
    """Indices of bands inside any (min, max) range and having good quality."""
    keep = np.ones(wavelengths.size, dtype=bool)
    if good_quality_mask is not None and len(good_quality_mask) == wavelengths.size:
        keep &= np.asarray(good_quality_mask, dtype=bool)
    if ranges:
        range_mask = np.zeros(wavelengths.size, dtype=bool)
        for lo, hi in ranges:
            range_mask |= (wavelengths >= lo) & (wavelengths <= hi)
        keep &= range_mask
    idx = np.flatnonzero(keep)
    if idx.size == 0:
        raise ValueError(f"no image bands fall inside the selected requirement bands {ranges}")
    return idx


def _load_aligned_library(library_dir, target_wl, mineral_names=None):
    """Load the USGS library and align it to the image wavelengths.

    Optionally filters the library to only `mineral_names` (e.g. minerals specific to the requirement band).
    """
    if not Path(library_dir).is_dir():
        raise FileNotFoundError(f"spectral library directory not found: {library_dir}")
    names, lib_wl, spectra = spectral_library.load_library(library_dir)
    lib_wl = np.asarray(lib_wl, dtype=float)

    if mineral_names:
        filter_set = {str(m).strip().lower() for m in mineral_names}
        keep_indices = [i for i, n in enumerate(names) if n.lower() in filter_set]
        if not keep_indices:
            raise ValueError(f"none of the requested minerals {mineral_names} found in library: {names}")
        names = [names[i] for i in keep_indices]
        spectra = spectra[keep_indices]

    # No extrapolation: every selected image wavelength must be covered by the library.
    if target_wl.min() < lib_wl.min() or target_wl.max() > lib_wl.max():
        raise ValueError(
            f"selected image wavelengths ({target_wl.min():.6g}-{target_wl.max():.6g}) are not "
            f"inside the library range ({lib_wl.min():.6g}-{lib_wl.max():.6g}); check wavelength "
            "units or narrow SELECTED_WAVELENGTH_RANGES"
        )
    aligned = np.asarray(spectral_library.align_to_wavelengths(lib_wl, spectra, target_wl), dtype=float)
    if aligned.shape != (len(names), target_wl.size):
        raise ValueError(f"aligned library has shape {aligned.shape}, expected {(len(names), target_wl.size)}")
    return list(names), aligned


def run(input_raster=None, library_dir=None, output_dir=None,
        wavelength_ranges=None, preset=None, feature=None,
        minerals=None, good_bands_only=False):
    """Run the mineral-analysis workflow on specific requirement bands.

    Parameters:
      input_raster:       Path to hyperspectral GeoTIFF.
      library_dir:        Path to reference USGS library CSV directory.
      output_dir:         Output directory for classification & abundance maps.
      wavelength_ranges:  List of (min, max) requirement wavelength ranges.
      preset:             Name of preset ('swir', 'vnir', 'clean', 'alteration', etc.).
      feature:            Diagnostic feature name ('Al-OH', 'CO3', 'Fe3+', etc.).
      minerals:           List of specific minerals to identify.
      good_bands_only:    If True, excludes noisy atmospheric bands.
    """
    input_raster = input_raster or config.INPUT_RASTER
    library_dir = library_dir or config.SPECTRAL_LIBRARY_DIR
    output_dir = Path(output_dir or config.OUTPUT_DIR)
    config.validate()

    # Resolve requirement bands and target minerals
    ranges_to_use = None
    suggested_minerals = None

    if feature:
        ranges_to_use, suggested_minerals = config.resolve_wavelength_ranges(feature)
        log.info("Using diagnostic feature '%s': %s (diagnostic minerals: %s)",
                 feature, ranges_to_use, suggested_minerals)
    elif preset:
        ranges_to_use, suggested_minerals = config.resolve_wavelength_ranges(preset)
        log.info("Using requirement band preset '%s': %s", preset, ranges_to_use)
    elif wavelength_ranges:
        ranges_to_use, suggested_minerals = config.resolve_wavelength_ranges(wavelength_ranges)
        log.info("Using custom requirement bands: %s", ranges_to_use)
    elif config.SELECTED_WAVELENGTH_RANGES:
        ranges_to_use, suggested_minerals = config.resolve_wavelength_ranges(config.SELECTED_WAVELENGTH_RANGES)
        log.info("Using configured requirement bands: %s", ranges_to_use)

    target_minerals = minerals or suggested_minerals

    # 1. Read raster; wavelengths must come from its metadata
    ref = raster_io.read_raster(input_raster, config.APPLY_SCALE_OFFSET)
    if ref.wavelengths is None:
        raise ValueError("input raster has no usable wavelength metadata; cannot align with the library")

    # 2. Select requirement bands, then load and align the library to exactly those wavelengths
    q_mask = ref.good_quality_mask if good_bands_only else None
    idx = _select_bands(ref.wavelengths, ranges_to_use, good_quality_mask=q_mask)
    image_wl = ref.wavelengths[idx]
    log.info("using %d of %d bands (%.6g-%.6g %s)", idx.size, ref.count,
             image_wl.min(), image_wl.max(), ref.wavelength_units)
    names, library = _load_aligned_library(library_dir, image_wl, mineral_names=target_minerals)
    log.info("library: %d minerals: %s", len(names), ", ".join(names))

    pixels = raster_io.to_pixels(ref.data[idx])  # (n_pixels, n_bands)
    n_pixels = pixels.shape[0]

    # 3. Continuum-removed references for SAM (SMA always uses original reflectance)
    use_cr = config.SAM_USE_CONTINUUM_REMOVAL
    sam_refs = continuum.continuum_remove_many(image_wl, library)[1] if use_cr else library

    with_quality = config.SAM_MAX_ANGLE is not None and config.SMA_MAX_RMSE is not None
    if not with_quality:
        log.warning("quality map skipped: set SAM_MAX_ANGLE and SMA_MAX_RMSE in config.py")

    class_index = np.full(n_pixels, sam.INVALID, dtype=np.int32)
    abundances = np.full((n_pixels, len(names)), np.nan, dtype=np.float32)
    quality = np.full(n_pixels, np.nan, dtype=np.float32)

    # 4. SAM + SMA + quality, in chunks to limit memory
    log.info("Processing %d pixels in chunks of %d...", n_pixels, config.CHUNK_PIXELS)
    for start in range(0, n_pixels, config.CHUNK_PIXELS):
        sl = slice(start, start + config.CHUNK_PIXELS)
        chunk = pixels[sl]
        sam_in = continuum.continuum_remove_many(image_wl, chunk)[1] if use_cr else chunk

        sam_out = sam.classify_sam(sam_in, sam_refs, max_angle=config.SAM_MAX_ANGLE)
        sma_out = sma.unmix(chunk, library, config.SMA_NON_NEGATIVE, config.SMA_SUM_TO_ONE)
        class_index[sl] = sam_out["class_index"]
        abundances[sl] = sma_out["abundances"]

        if with_quality:
            q_sam = confidence.sam_scores(sam_out["angles"], config.SAM_MAX_ANGLE)["quality"]
            q_sma = confidence.sma_quality(sma_out["rmse"], config.SMA_MAX_RMSE)
            quality[sl] = confidence.combine_quality(q_sam, q_sma)
        log.info("processed %d / %d pixels", min(sl.stop, n_pixels), n_pixels)

    counts = {
        "classified": int((class_index >= 0).sum()),
        "unknown": int((class_index == sam.UNKNOWN).sum()),
        "invalid": int((class_index == sam.INVALID).sum()),
    }
    if counts["classified"] + counts["unknown"] == 0:
        raise RuntimeError("no valid pixels were classified; check nodata values and wavelength alignment")

    # 5. Write georeferenced outputs (georeferencing comes from the input raster)
    h, w = ref.height, ref.width
    class_ids = np.where(class_index >= 0, class_index + 1, config.CLASS_NODATA_ID)
    class_names = {i + 1: n for i, n in enumerate(names)}
    outputs = {
        "classification": raster_io.write_classification(
            output_dir / config.CLASSIFICATION_FILE, ref,
            raster_io.to_map(class_ids, h, w), class_names, config.CLASS_NODATA_ID),
        "abundance": raster_io.write_abundance(
            output_dir / config.ABUNDANCE_FILE, ref, raster_io.to_map(abundances, h, w), names),
    }
    if with_quality:
        outputs["quality"] = raster_io.write_quality(
            output_dir / config.QUALITY_FILE, ref, raster_io.to_map(quality, h, w))

    # 6. Processing metadata
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input_raster": str(input_raster),
        "spectral_library_dir": str(library_dir),
        "minerals": class_names,
        "image": {"height": h, "width": w, "crs": str(ref.crs),
                  "resolution": ref.resolution, "bounds": ref.bounds},
        "bands": {"available": ref.count, "used": int(idx.size),
                  "wavelength_units": ref.wavelength_units,
                  "range_used": [float(image_wl.min()), float(image_wl.max())],
                  "selected_ranges": ranges_to_use,
                  "good_bands_only": good_bands_only},
        "settings": {"sam_max_angle_rad": config.SAM_MAX_ANGLE, "sma_max_rmse": config.SMA_MAX_RMSE,
                     "sam_continuum_removal": use_cr, "sma_non_negative": config.SMA_NON_NEGATIVE,
                     "sma_sum_to_one": config.SMA_SUM_TO_ONE, "class_nodata_id": config.CLASS_NODATA_ID},
        "pixel_counts": counts,
        "outputs": {k: str(v) for k, v in outputs.items()},
        "notes": ["quality is a heuristic indicator, not a probability",
                  "SMA used original (not continuum-removed) reflectance"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    log.info("done: %s", ", ".join(f"{k}={v}" for k, v in outputs.items()))
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mineral analysis: SAM + SMA + quality maps with requirement band selection"
    )
    parser.add_argument("--input", help="hyperspectral GeoTIFF (default: config.INPUT_RASTER)")
    parser.add_argument("--library", help="USGS library folder (default: config.SPECTRAL_LIBRARY_DIR)")
    parser.add_argument("--output", help="output folder (default: config.OUTPUT_DIR)")
    parser.add_argument(
        "--preset",
        choices=list(config.WAVELENGTH_PRESETS.keys()),
        help="Requirement bands preset: 'swir' (2000-2450nm), 'vnir' (450-1100nm), 'clean' (water masked), 'alteration', 'iron_oxide', 'carbonate', 'full'"
    )
    parser.add_argument(
        "--feature",
        choices=list(config.DIAGNOSTIC_FEATURES.keys()),
        help="Target diagnostic absorption feature: 'Al-OH' (2100-2300nm), 'Mg-OH' (2250-2400nm), 'CO3' (2300-2400nm), 'Fe3+' (750-1050nm), 'Fe2+', 'Fe-OH', 'Sulfate'"
    )
    parser.add_argument(
        "--bands",
        nargs="+",
        type=float,
        help="Custom requirement bands as min max pairs (e.g. --bands 2000 2450 or --bands 450 1340 2000 2450)"
    )
    parser.add_argument(
        "--minerals",
        nargs="+",
        help="Filter specific minerals to map from the library (e.g. --minerals kaolinite alunite calcite)"
    )
    parser.add_argument(
        "--good-bands-only",
        action="store_true",
        help="Filter out atmospheric water vapor and noisy bands flagged bad in metadata"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Starting Mineral Analysis workflow...")

    custom_bands = None
    if args.bands:
        if len(args.bands) % 2 != 0:
            parser.error("--bands requires an even number of values (pairs of min and max wavelengths)")
        custom_bands = [(args.bands[i], args.bands[i + 1]) for i in range(0, len(args.bands), 2)]

    try:
        run(
            input_raster=args.input,
            library_dir=args.library,
            output_dir=args.output,
            wavelength_ranges=custom_bands,
            preset=args.preset,
            feature=args.feature,
            minerals=args.minerals,
            good_bands_only=args.good_bands_only,
        )
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        return 1
    except Exception:
        log.exception("processing failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

