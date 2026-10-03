# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Landsat entry points: band files on disk in, clean arrays out.

    thermal_celsius()      ST_B10 -> surface temperature in Celsius
    surface_reflectance()  SR_B5, SR_B6, ... -> reflectance 0..1

Both apply the scene's own MTL calibration and the QA_PIXEL cloud mask, and
read only the bbox window. No network, no Kafka: the caller fetches the
files and decides where the result goes (write_geotiff, or its own store).

QA and MTL are found beside the band by default ("auto"). Pass None to go
without one; the returned tags say what was and was not applied.
"""

from . import calibration, cog, config, convert, mtl, qa, reflectance

AUTO = "auto"


def _companions(band_path, qa_path, mtl_path):
    qa_path = qa.find_qa(band_path) if qa_path == AUTO else qa_path
    mtl_path = mtl.find_beside(band_path) if mtl_path == AUTO else mtl_path
    return qa_path, mtl_path


def thermal_celsius(thermal_path, bbox=None, qa_path=AUTO, mtl_path=AUTO):
    """
    ST_B10 -> Celsius, cloud and implausible values set to FILL_VALUE.

    Returns:
        (celsius, transform, crs, tags) -- stats in tags are from valid
        pixels only.
    """
    qa_path, mtl_path = _companions(thermal_path, qa_path, mtl_path)
    scale, offset, from_mtl = calibration.thermal_scaling(
        mtl.parse(mtl_path), config.ST_SCALE, config.ST_OFFSET)

    dn, transform, crs = cog.read_window(thermal_path, bbox)
    cloud, qa_stats = qa.read_quality(qa_path, bbox)
    celsius, stats = convert.prepare(dn, scale, offset, invalid=cloud)

    tags = {**stats, **qa_stats, "units": "celsius", "scale": scale,
            "offset": offset, "scaling_from_mtl": from_mtl,
            "cloud_masked": cloud is not None,
            "bbox": list(bbox) if bbox else None}
    return celsius, transform, crs, tags


def surface_reflectance(band_path, bbox=None, qa_path=AUTO, mtl_path=AUTO):
    """
    SR_B<n> -> reflectance 0..1, cloud and implausible values set to fill.

    Returns:
        (reflectance, transform, crs, tags)
    """
    band = calibration.band_number(band_path)
    if band is None:
        raise ValueError(f"Not a numbered SR band: {band_path}")

    qa_path, mtl_path = _companions(band_path, qa_path, mtl_path)
    scale, offset, from_mtl = calibration.reflectance_scaling(
        mtl.parse(mtl_path), band)

    dn, transform, crs = cog.read_window(band_path, bbox)
    cloud, qa_stats = qa.read_quality(qa_path, bbox)
    values, stats = reflectance.prepare(dn, scale, offset, invalid=cloud,
                                        fill=config.FILL_VALUE)

    tags = {**stats, **qa_stats, "band": f"SR_B{band}",
            "units": "surface_reflectance", "scale": scale, "offset": offset,
            "scaling_from_mtl": from_mtl, "cloud_masked": cloud is not None,
            "bbox": list(bbox) if bbox else None}
    return values, transform, crs, tags


def write_geotiff(path, data, transform, crs, tags):
    """Tiled, compressed GeoTIFF with FILL_VALUE declared as nodata."""
    cog.write(path, data, transform, crs, tags)
    return path
