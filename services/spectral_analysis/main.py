"""
Spectral Analysis Service
-------------------------
Command-line entry point for the spectral analysis pipeline.

This service:
1. Loads an EMIT spectral cube (.npy)
2. Loads the wavelength array (.npy)
3. Runs NDVI, EVI, NDWI and NDMI analysis
4. Saves the spectral-index stack and metadata
5. Optionally generates GeoTIFF maps
"""

import argparse
import json
from pathlib import Path

import numpy as np

from derivative_analysis import spectral_analysis_service


def parse_bbox(value):
    """
    Parse bounding box supplied as:
    left,bottom,right,top
    """
    if value is None:
        return None

    parts = [float(x.strip()) for x in value.split(",")]

    if len(parts) != 4:
        raise ValueError(
            "bbox must contain four values: "
            "left,bottom,right,top"
        )

    return tuple(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Run the EMIT spectral analysis service."
    )

    parser.add_argument(
        "--cube",
        required=True,
        help="Path to spectral cube .npy file"
    )

    parser.add_argument(
        "--wavelengths",
        required=True,
        help="Path to wavelength .npy file"
    )

    parser.add_argument(
        "--output-dir",
        default="./spectral_analysis_output",
        help="Directory for service outputs"
    )

    parser.add_argument(
        "--bbox",
        default=None,
        help="Optional bounding box: left,bottom,right,top"
    )

    parser.add_argument(
        "--generate-maps",
        action="store_true",
        help="Generate GeoTIFF spectral-index maps"
    )

    args = parser.parse_args()

    cube_path = Path(args.cube)
    wavelength_path = Path(args.wavelengths)
    output_dir = Path(args.output_dir)

    if not cube_path.exists():
        raise FileNotFoundError(
            f"Spectral cube not found: {cube_path}"
        )

    if not wavelength_path.exists():
        raise FileNotFoundError(
            f"Wavelength file not found: {wavelength_path}"
        )

    print("=" * 70)
    print("SPECTRAL ANALYSIS SERVICE")
    print("=" * 70)

    print(f"Loading cube       : {cube_path}")
    print(f"Loading wavelengths: {wavelength_path}")

    emit_cube = np.load(cube_path)
    emit_wavelengths = np.load(wavelength_path)

    print(f"Cube shape         : {emit_cube.shape}")
    print(f"Cube dtype         : {emit_cube.dtype}")
    print(f"Wavelength count   : {len(emit_wavelengths)}")

    bbox = parse_bbox(args.bbox)

    result = spectral_analysis_service(
        emit_cube=emit_cube,
        emit_wavelengths=emit_wavelengths,
        bbox=bbox,
        output_dir=output_dir,
        generate_maps=args.generate_maps,
    )

    # Save the spectral-index stack.
    indices_path = output_dir / "spectral_indices.npy"
    np.save(indices_path, result["spectral_indices"])

    # Save metadata.
    metadata_path = output_dir / "metadata.json"

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(
            result["metadata"],
            f,
            indent=2
        )

    index_stack = result["spectral_indices"]

    print()
    print("=" * 70)
    print("SPECTRAL ANALYSIS COMPLETED")
    print("=" * 70)

    print(f"Index stack shape  : {index_stack.shape}")
    print(f"Index names        : {result['index_names']}")
    print(f"Index stack file   : {indices_path}")
    print(f"Metadata file      : {metadata_path}")

    if args.generate_maps:
        print(f"Map output         : {output_dir / 'maps'}")

    print("=" * 70)


if __name__ == "__main__":
    main()
