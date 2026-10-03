# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Does this scene still need atmospheric correction?

Dark-object subtraction removes haze from RAW radiance. EMIT L2A, which
ingestion fetches, is ALREADY surface reflectance: there is no haze left,
so subtracting the 1st percentile strips real signal from every pixel.
Measured on a real EMIT granule, that collapsed NDVI from +0.361 to -0.041.

It is also unreliable on a small crop, which is what the product sends:
the darkest 1% of a few hundred pixels is a real surface, not haze.
"""

import config


def needs_dark_subtraction(attrs, satellite=None):
    """
    Args:
        attrs: the input Zarr's attributes
        satellite: the 'satellite' field of the ingestion message

    Returns:
        True only when the input is raw enough to still carry haze.
    """
    mode = config.DARK_SUBTRACTION
    if mode == "always":
        return True
    if mode == "never":
        return False

    # "auto": an explicit flag on the store wins ...
    if "surface_reflectance" in attrs:
        return not bool(attrs["surface_reflectance"])
    # ... otherwise trust what the source is known to deliver.
    return satellite not in config.SURFACE_REFLECTANCE_SOURCES
