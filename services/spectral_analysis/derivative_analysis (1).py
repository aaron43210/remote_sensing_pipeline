
# ============================================================
# SPECTRAL ANALYSIS SERVICE
# derivative_analysis.py
# ============================================================

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds


# ============================================================
# TARGET WAVELENGTHS
# ============================================================

INDEX_BAND_TARGETS = {
    "blue": 482.0,
    "green": 560.0,
    "red": 665.0,
    "nir": 842.0,
    "swir1": 1610.0
}


INDEX_NAMES = [
    "NDVI",
    "EVI",
    "NDWI",
    "NDMI"
]


# ============================================================
# SAFE DIVISION
# ============================================================

def safe_divide(numerator, denominator):
    """
    Perform element-wise division.

    Pixels with invalid or zero denominators are returned
    as NaN.
    """

    result = np.full(
        numerator.shape,
        np.nan,
        dtype=np.float32
    )

    valid = (
        np.isfinite(numerator)
        &
        np.isfinite(denominator)
        &
        (denominator != 0)
    )

    result[valid] = (
        numerator[valid] /
        denominator[valid]
    )

    return result


# ============================================================
# SELECT NEAREST EMIT BANDS
# ============================================================

def select_index_bands(emit_wavelengths):
    """
    Select the nearest EMIT wavelength for each required
    spectral-index band.

    Parameters
    ----------
    emit_wavelengths : numpy.ndarray
        EMIT wavelengths in nanometers.

    Returns
    -------
    dict
        Band-selection information.
    """

    wavelengths = np.asarray(
        emit_wavelengths,
        dtype=np.float64
    )

    if wavelengths.ndim != 1:
        raise ValueError(
            "emit_wavelengths must be a 1-D array"
        )

    selected = {}

    for band_name, target in INDEX_BAND_TARGETS.items():

        index = int(
            np.argmin(
                np.abs(
                    wavelengths - target
                )
            )
        )

        matched = float(
            wavelengths[index]
        )

        selected[band_name] = {
            "index": index,
            "target_wavelength_nm": float(target),
            "emit_wavelength_nm": matched,
            "difference_nm": abs(
                matched - target
            )
        }

    # Make sure every required band is unique.
    selected_indices = [
        info["index"]
        for info in selected.values()
    ]

    if len(selected_indices) != len(
        np.unique(selected_indices)
    ):
        raise ValueError(
            "Multiple spectral-index bands mapped "
            "to the same EMIT band."
        )

    return selected


# ============================================================
# RUN SPECTRAL ANALYSIS
# ============================================================

def run_spectral_analysis(
    emit_cube,
    emit_wavelengths
):
    """
    Generate NDVI, EVI, NDWI and NDMI from an EMIT
    surface-reflectance cube.

    Parameters
    ----------
    emit_cube : numpy.ndarray
        EMIT reflectance cube with shape:
        (rows, columns, bands)

    emit_wavelengths : numpy.ndarray
        EMIT wavelength array in nanometers.

    Returns
    -------
    numpy.ndarray
        Spectral-index stack with shape:
        (rows, columns, 4)

        Band order:
        0 = NDVI
        1 = EVI
        2 = NDWI
        3 = NDMI
    """

    cube_input = np.asarray(
        emit_cube
    )

    wavelengths = np.asarray(
        emit_wavelengths,
        dtype=np.float64
    )

    # --------------------------------------------------------
    # Validate input
    # --------------------------------------------------------

    if cube_input.ndim != 3:
        raise ValueError(
            "emit_cube must have shape "
            "(rows, columns, bands)"
        )

    if cube_input.shape[2] != len(wavelengths):
        raise ValueError(
            "Number of cube bands does not match "
            "wavelength array."
        )

    if len(wavelengths) == 0:
        raise ValueError(
            "Wavelength array is empty."
        )

    # --------------------------------------------------------
    # Create working copy
    # --------------------------------------------------------

    cube = cube_input.astype(
        np.float32,
        copy=True
    )

    # --------------------------------------------------------
    # Remove invalid reflectance
    # --------------------------------------------------------

    invalid = (
        ~np.isfinite(cube)
        |
        (cube < 0)
    )

    cube[invalid] = np.nan

    # --------------------------------------------------------
    # Select nearest EMIT bands
    # --------------------------------------------------------

    selected = select_index_bands(
        wavelengths
    )

    blue = cube[
        :, :,
        selected["blue"]["index"]
    ]

    green = cube[
        :, :,
        selected["green"]["index"]
    ]

    red = cube[
        :, :,
        selected["red"]["index"]
    ]

    nir = cube[
        :, :,
        selected["nir"]["index"]
    ]

    swir1 = cube[
        :, :,
        selected["swir1"]["index"]
    ]

    # --------------------------------------------------------
    # NDVI
    # --------------------------------------------------------

    ndvi = safe_divide(
        nir - red,
        nir + red
    )

    # --------------------------------------------------------
    # EVI
    # --------------------------------------------------------

    evi_denominator = (
        nir
        +
        6.0 * red
        -
        7.5 * blue
        +
        1.0
    )

    evi = safe_divide(
        2.5 * (nir - red),
        evi_denominator
    )

    # --------------------------------------------------------
    # NDWI
    # Green-NIR formulation
    # --------------------------------------------------------

    ndwi = safe_divide(
        green - nir,
        green + nir
    )

    # --------------------------------------------------------
    # NDMI
    # --------------------------------------------------------

    ndmi = safe_divide(
        nir - swir1,
        nir + swir1
    )

    # --------------------------------------------------------
    # Stack outputs
    # --------------------------------------------------------

    spectral_index_stack = np.stack(
        [
            ndvi,
            evi,
            ndwi,
            ndmi
        ],
        axis=2
    ).astype(
        np.float32
    )

    return spectral_index_stack


# ============================================================
# METADATA
# ============================================================

def get_spectral_analysis_metadata(
    emit_wavelengths
):
    """
    Return metadata describing the spectral-analysis
    input, processing and outputs.
    """

    selected = select_index_bands(
        emit_wavelengths
    )

    band_information = {}

    for name, info in selected.items():

        band_information[name] = {
            "target_wavelength_nm":
                info["target_wavelength_nm"],

            "emit_band_index":
                info["index"],

            "emit_wavelength_nm":
                info["emit_wavelength_nm"],

            "difference_nm":
                info["difference_nm"]
        }

    indices = {

        "NDVI": {
            "formula":
                "(NIR - Red) / (NIR + Red)",
            "bands": [
                "nir",
                "red"
            ]
        },

        "EVI": {
            "formula":
                "2.5 * (NIR - Red) / "
                "(NIR + 6*Red - 7.5*Blue + 1)",
            "bands": [
                "nir",
                "red",
                "blue"
            ]
        },

        "NDWI": {
            "formula":
                "(Green - NIR) / (Green + NIR)",
            "definition":
                "Green-NIR NDWI",
            "bands": [
                "green",
                "nir"
            ]
        },

        "NDMI": {
            "formula":
                "(NIR - SWIR1) / (NIR + SWIR1)",
            "bands": [
                "nir",
                "swir1"
            ]
        }
    }

    return {
        "service":
            "Spectral Analysis Service",

        "input": {
            "sensor":
                "NASA EMIT",

            "data_type":
                "surface reflectance",

            "wavelength_unit":
                "nm"
        },

        "output": {
            "type":
                "spectral index raster stack",

            "index_order":
                INDEX_NAMES,

            "dtype":
                "float32"
        },

        "preprocessing": {
            "invalid_reflectance_rule":
                "non-finite or negative reflectance "
                "converted to NaN"
        },

        "required_bands":
            band_information,

        "indices":
            indices
    }


# ============================================================
# WRITE GEOTIFF MAPS
# ============================================================

def write_spectral_index_maps(
    spectral_index_stack,
    bbox,
    output_dir,
    crs="EPSG:4326"
):
    """
    Write NDVI, EVI, NDWI and NDMI as GeoTIFF maps.

    Parameters
    ----------
    spectral_index_stack : numpy.ndarray
        Array with shape (rows, columns, 4).

    bbox : list or tuple
        [min_lon, min_lat, max_lon, max_lat]

    output_dir : str or Path
        Output directory.

    crs : str
        Coordinate reference system.

    Returns
    -------
    dict
        Paths of generated GeoTIFF files.
    """

    stack = np.asarray(
        spectral_index_stack,
        dtype=np.float32
    )

    if stack.ndim != 3:
        raise ValueError(
            "spectral_index_stack must be 3-D."
        )

    if stack.shape[2] != 4:
        raise ValueError(
            "spectral_index_stack must contain "
            "four index bands."
        )

    if bbox is None or len(bbox) != 4:
        raise ValueError(
            "bbox must contain "
            "[min_lon, min_lat, max_lon, max_lat]."
        )

    min_lon, min_lat, max_lon, max_lat = map(
        float,
        bbox
    )

    rows, cols = stack.shape[:2]

    transform = from_bounds(
        min_lon,
        min_lat,
        max_lon,
        max_lat,
        cols,
        rows
    )

    output_path = Path(
        output_dir
    )

    output_path.mkdir(
        parents=True,
        exist_ok=True
    )

    map_paths = {}

    for band_number, index_name in enumerate(
        INDEX_NAMES
    ):

        path = output_path / (
            f"{index_name}.tif"
        )

        index_map = stack[
            :, :,
            band_number
        ]

        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=rows,
            width=cols,
            count=1,
            dtype="float32",
            crs=crs,
            transform=transform,
            nodata=np.nan,
            compress="deflate"
        ) as dst:

            dst.write(
                index_map.astype(
                    np.float32
                ),
                1
            )

            dst.set_band_description(
                1,
                index_name
            )

            dst.update_tags(
                service=
                    "Spectral Analysis Service",

                sensor=
                    "NASA EMIT",

                product=
                    "Surface Reflectance",

                index=
                    index_name
            )

        map_paths[index_name] = str(
            path
        )

    return map_paths


# ============================================================
# COMPLETE SERVICE FUNCTION
# ============================================================

def spectral_analysis_service(
    emit_cube,
    emit_wavelengths,
    bbox=None,
    output_dir=None,
    generate_maps=False
):
    """
    Execute the complete Spectral Analysis Service.

    Parameters
    ----------
    emit_cube : numpy.ndarray
        EMIT surface-reflectance cube.

    emit_wavelengths : numpy.ndarray
        EMIT wavelengths in nanometers.

    bbox : optional
        [min_lon, min_lat, max_lon, max_lat]

    output_dir : optional
        Directory for GeoTIFF maps.

    generate_maps : bool
        Whether to generate GeoTIFF maps.

    Returns
    -------
    dict
        Spectral-analysis service response.
    """

    index_stack = run_spectral_analysis(
        emit_cube,
        emit_wavelengths
    )

    metadata = get_spectral_analysis_metadata(
        emit_wavelengths
    )

    response = {
        "spectral_indices":
            index_stack,

        "index_names":
            INDEX_NAMES.copy(),

        "metadata":
            metadata
    }

    if generate_maps:

        if bbox is None:
            raise ValueError(
                "bbox is required when "
                "generate_maps=True."
            )

        if output_dir is None:
            raise ValueError(
                "output_dir is required when "
                "generate_maps=True."
            )

        map_paths = write_spectral_index_maps(
            spectral_index_stack=index_stack,
            bbox=bbox,
            output_dir=output_dir
        )

        response["map_paths"] = map_paths

    return response
