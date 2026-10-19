"""End-to-end driver for the mineral side of the project.

Steps
  1. Ensure the EMIT reflectance cube exists (extract from zarr if missing).
  2. Run the normal SAM + SMA + quality pipeline (main.py) -> reference maps.
  3. Build the ML-ready training dataset (features + SAM/SMA labels).
  4. Self-validate the dataset with a small neural network and score it against
     the SAM/SMA reference; optionally predict a whole-image NN map.

Run from the service directory:
    python -m rtm_inversion.run --good-bands-only --full-map
"""
import sys
from pathlib import Path

_SERVICE_DIR = str(Path(__file__).resolve().parent.parent)
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import config  # noqa: E402

try:  # works as `python -m rtm_inversion.run` and as `python run.py`
    from . import build_dataset, validate_nn
except ImportError:
    import build_dataset, validate_nn


def run(input_raster=None, good_bands_only=True, skip_normal=False,
        full_map=False, max_samples=build_dataset.DEFAULT_MAX_SAMPLES):
    input_raster = Path(input_raster or config.INPUT_RASTER)
    normal_dir = config.OUTPUT_DIR / "normal_sam"

    # 1. Extract the cube from zarr if nothing usable is on disk.
    if not input_raster.is_file():
        print(f"[1/4] input {input_raster.name} missing; extracting from zarr ...")
        import zarr_to_tif
        zarr_to_tif.main()
    else:
        print(f"[1/4] using existing input: {input_raster.name}")

    # 2. Normal SAM/SMA/quality reference.
    if skip_normal and (normal_dir / "processing_metadata.json").is_file():
        print("[2/4] reusing existing normal SAM/SMA maps")
    else:
        print("[2/4] running normal SAM/SMA/quality pipeline ...")
        import main as mineral_main
        mineral_main.run(input_raster=str(input_raster), output_dir=str(normal_dir),
                         good_bands_only=good_bands_only)

    # 3. Build the dataset.
    print("[3/4] building training dataset ...")
    out_dir, shape, names = build_dataset.build(
        cube_path=str(input_raster), normal_dir=str(normal_dir), max_samples=max_samples)
    print(f"      dataset: X={shape}, {len(names)} classes -> {out_dir}")

    # 4. Validate with a small NN.
    print("[4/4] self-validating with a neural network ...")
    report, val_dir = validate_nn.validate(dataset_dir=out_dir)
    print(f"      class agreement vs SAM: {report['class_accuracy']:.3f}  "
          f"abundance R²: {report['abundance_r2']:.3f}  validated={report['validated']}")
    if full_map:
        nn_map, agree = validate_nn.full_image_map(
            dataset_dir=out_dir, cube_path=str(input_raster), normal_dir=str(normal_dir))
        print(f"      NN image map: {nn_map}  spatial agreement vs SAM: "
              + (f"{agree:.3f}" if agree is not None else "n/a"))

    print("\nDone. Hand-off artefacts:")
    print(f"  dataset   : {out_dir}")
    print(f"  validation: {val_dir}")
    return report


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="End-to-end mineral dataset build + NN self-validation")
    ap.add_argument("--input", help="input raster: GeoTIFF cube or ENVI .bsq (default: config.INPUT_RASTER)")
    ap.add_argument("--all-bands", action="store_true", help="use all bands (default: good bands only)")
    ap.add_argument("--skip-normal", action="store_true", help="reuse existing normal SAM/SMA maps")
    ap.add_argument("--full-map", action="store_true", help="also predict a whole-image NN map")
    ap.add_argument("--max-samples", type=int, default=build_dataset.DEFAULT_MAX_SAMPLES)
    args = ap.parse_args(argv)
    run(input_raster=args.input, good_bands_only=not args.all_bands,
        skip_normal=args.skip_normal, full_map=args.full_map, max_samples=args.max_samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
