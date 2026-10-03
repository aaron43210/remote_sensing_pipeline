# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Parse a Landsat MTL metadata file, preserving its GROUP structure.

GROUPS MATTER. The same key appears in several groups with different values,
because the file describes both the Level-1 and Level-2 products:

    LEVEL2_SURFACE_REFLECTANCE_PARAMETERS  REFLECTANCE_MULT_BAND_5 = 2.75e-05
    LEVEL1_RADIOMETRIC_RESCALING           REFLECTANCE_MULT_BAND_5 = 2.0e-05

A flat parser keeps whichever came last and silently applies Level-1 scaling
to Level-2 data. So keys are stored per group and callers name the group.
"""

import logging
import os

logger = logging.getLogger(__name__)


def find_beside(thermal_path):
    """
    Locate the MTL file sitting next to a band.

    Without it we fall back to documented constants -- right for a standard
    scene, wrong for a reprocessed one -- so callers report which was used.
    """
    folder = os.path.dirname(os.path.abspath(thermal_path))
    for name in sorted(os.listdir(folder)):
        if "MTL" in name.upper() and name.lower().endswith(".txt"):
            return os.path.join(folder, name)
    return None


def _coerce(value):
    """Numbers as floats, everything else as a stripped string."""
    value = value.strip().strip('"')
    try:
        return float(value)
    except ValueError:
        return value


def parse(path):
    """
    Read an MTL file into {group_name: {key: value}}.

    Never raises: a missing or malformed MTL degrades to the caller's
    fallback constants rather than failing the whole ingest.
    """
    groups, stack = {}, []

    if not path:
        return groups

    try:
        with open(path) as handle:
            for line in handle:
                line = line.strip()

                if line.startswith("END_GROUP"):
                    if stack:
                        stack.pop()
                elif line.startswith("GROUP"):
                    name = line.split("=", 1)[1].strip()
                    stack.append(name)
                    groups.setdefault(name, {})
                elif "=" in line and stack:
                    key, value = line.split("=", 1)
                    groups[stack[-1]][key.strip()] = _coerce(value)

    except OSError as exc:
        logger.warning("MTL read failed (%s) -- using fallback constants", exc)

    return groups


def get(meta, key, group=None, default=None):
    """
    Look a key up, preferably within a named group.

    Passing `group` is strongly preferred. Without it we search every group
    and warn if the key is ambiguous, because picking arbitrarily is how the
    wrong calibration gets applied.
    """
    if group is not None:
        return meta.get(group, {}).get(key, default)

    hits = [(name, values[key]) for name, values in meta.items()
            if key in values]

    if not hits:
        return default
    if len(hits) > 1:
        logger.warning("MTL key %s appears in %d groups (%s) -- using %s",
                       key, len(hits), ", ".join(n for n, _ in hits),
                       hits[0][0])
    return hits[0][1]
