# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Landsat Collection 2 Level-2 preprocessing (thermal + reflectance).

One module per job, so a failure lands in one small file:

    process.py      entry points: thermal_celsius(), surface_reflectance()
    mtl.py          parse the scene's metadata file
    calibration.py  pick the RIGHT scale/offset out of it (Level-2, not Level-1)
    qa.py           QA_PIXEL bits -> cloud mask
    convert.py      DN -> Kelvin -> Celsius, plausibility mask, statistics
    reflectance.py  DN -> reflectance 0..1, plausibility mask, statistics
    cog.py          lon/lat window read (any UTM zone), tiled GeoTIFF write
    config.py       constants

Not wired to Kafka: the new pipeline has no thermal stage yet.
"""
