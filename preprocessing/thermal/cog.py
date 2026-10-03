# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Read a thermal band within a bounding box, and write the result as a GeoTIFF.

CRS MATTERS HERE. Landsat C2 L2 ships in UTM, but every bbox in this pipeline
is lon/lat because that is what the user draws on a map. Feeding lon/lat
numbers into a UTM transform computes a nonsense window -- it does not raise,
it reads the wrong pixels. So the bbox is reprojected first. EMIT hid this:
its granules are already EPSG:4326, so the two agreed by accident.
"""

import logging

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from . import config

logger = logging.getLogger(__name__)
LONLAT = "EPSG:4326"


def _window_for(src, bbox):
    """Convert a lon/lat bbox into a pixel window in the raster's own CRS."""
    left, bottom, right, top = bbox

    if src.crs and src.crs.to_string() != LONLAT:
        left, bottom, right, top = transform_bounds(
            LONLAT, src.crs, left, bottom, right, top)
        logger.info("Reprojected bbox to %s", src.crs.to_string())

    window = from_bounds(left, bottom, right, top, transform=src.transform)
    return window.intersection(Window(0, 0, src.width, src.height))


def read_window(path, bbox=None):
    """
    Read a thermal band, optionally cropped to a lon/lat bounding box.

    bbox is [lon_min, lat_min, lon_max, lat_max], or None for the whole
    scene. Returns (dn_array, transform, crs). Raises ValueError when the
    bbox misses the scene -- clearer than an empty array failing later.
    """
    with rasterio.open(path) as src:
        if bbox is None:
            logger.info("Reading whole scene: %d x %d", src.height, src.width)
            return src.read(1), src.transform, src.crs

        window = _window_for(src, bbox)
        if window.width < 1 or window.height < 1:
            raise ValueError(
                f"bbox {bbox} falls outside the scene extent {src.bounds} "
                f"({src.crs}). Check the coordinates are lon/lat."
            )

        logger.info("Reading window %dx%d of %dx%d",
                    int(window.height), int(window.width), src.height, src.width)
        return (src.read(1, window=window),
                src.window_transform(window), src.crs)


def write(path, data, transform, crs, metadata=None):
    """
    Write a single-band tiled, compressed GeoTIFF in Celsius.

    config.FILL_VALUE is written for invalid pixels AND declared as nodata,
    so a masked read excludes it automatically. This matters: ~32% of a real
    Landsat raster is empty UTM corner, and a naive mean over the raw array
    comes out ~11 C too cold.

    Tiled so a consumer can read sub-windows without the whole file.
    """
    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=config.FILL_VALUE,
        tiled=True,
        blockxsize=config.BLOCK_SIZE,
        blockysize=config.BLOCK_SIZE,
        compress=config.COMPRESSION,
    ) as dst:
        dst.write(data.astype("float32"), 1)
        dst.update_tags(sensor="Landsat 9 TIRS",
                        band="Surface Temperature (Celsius)",
                        processing_level="L2_ST_C2")
        if metadata:
            dst.update_tags(**{k: str(v) for k, v in metadata.items()
                               if isinstance(v, (str, int, float))})

    logger.info("Wrote thermal GeoTIFF: %s %s", path, data.shape)
    return path
