"""
NASA EMIT L2A surface reflectance (and its cloud mask) for a bbox.

    granules = search(bbox, date_from, date_to)        # CMR, public, no login
    path = download(granule, workdir)                  # Earthdata login needed
    cube, wl, good, georef = ortho_crop(path, bbox)    # GLT orthorectification

EMIT L2A files are in swath geometry. Each file carries a geometry lookup
table (location/glt_x, glt_y): for every pixel of a north-up EPSG:4326 grid it
says which swath pixel lands there (0 = none). We crop that grid to the bbox
and read only the swath rows it needs, so the output is a georeferenced cube
of the user's box, exactly like NASA's own emit_tools ortho output.

Authentication: EARTHDATA_TOKEN (bearer) or EARTHDATA_USERNAME/PASSWORD.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from .landsat import covers

log = logging.getLogger(__name__)

CMR = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
RFL = "EMITL2ARFL"
MASK = "EMITL2AMASK"
MAX_CLOUD = float(os.getenv("EMIT_MAX_CLOUD", "40"))
FILL = -9999.0


def _footprint(umm):
    polys = []
    for g in umm["SpatialExtent"]["HorizontalSpatialDomain"]["Geometry"].get("GPolygons", []):
        pts = [[p["Longitude"], p["Latitude"]] for p in g["Boundary"]["Points"]]
        polys.append([pts])
    return {"type": "MultiPolygon", "coordinates": polys}


def _data_url(umm, suffix=".nc"):
    for u in umm.get("RelatedUrls", []):
        url = u.get("URL", "")
        if u.get("Type") == "GET DATA" and url.startswith("https") and url.endswith(suffix) \
                and "_UNCERT_" not in url:
            return url
    return None


def search(bbox, date_from=None, date_to=None, max_cloud=MAX_CLOUD, short_name=RFL, page_size=100):
    """Granules fully covering the bbox, clearest first then newest. Public CMR."""
    date_to = date_to or datetime.now(timezone.utc).date().isoformat()
    date_from = date_from or (datetime.fromisoformat(date_to) - timedelta(days=365)).date().isoformat()
    params = {"short_name": short_name, "bounding_box": ",".join(map(str, bbox)),
              "temporal": f"{date_from}T00:00:00Z,{date_to}T23:59:59Z",
              "page_size": page_size, "sort_key": "-start_date"}
    if max_cloud is not None and short_name == RFL:
        params["cloud_cover"] = f"0,{max_cloud}"
    r = requests.get(CMR, params=params, timeout=60)
    r.raise_for_status()
    out = []
    for item in r.json().get("items", []):
        umm, meta = item["umm"], item["meta"]
        fp = _footprint(umm)
        if not fp["coordinates"] or not covers(fp, bbox):
            continue
        out.append({"granule_id": umm["GranuleUR"], "concept_id": meta["concept-id"],
                    "acquired_at": umm["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"],
                    "cloud_cover": umm.get("CloudCover"), "url": _data_url(umm), "footprint": fp})
    # one entry per acquisition: keep the newest processing version
    latest = {}
    for g in out:
        stamp, version = _stamp(g["granule_id"])
        if stamp not in latest or version > _stamp(latest[stamp]["granule_id"])[1]:
            latest[stamp] = g
    out = list(latest.values())
    # clearest first (in 10 % bands), then newest
    out.sort(key=lambda g: (round((g["cloud_cover"] or 0) / 10), -_ts(g["acquired_at"])))
    return out


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _stamp(granule_id):
    """'EMIT_L2A_RFL_002_20230822T192555[_orbit_scene]' -> ('20230822T192555', 2)."""
    parts = granule_id.split("_")
    return parts[4], int(parts[3])


def mask_granule_for(granule):
    """URL of the L2A MASK granule acquired at the same instant (versions differ: RFL 002 <-> MASK 003)."""
    t = datetime.strptime(_stamp(granule["granule_id"])[0], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    window = f"{(t - timedelta(seconds=2)).isoformat()},{(t + timedelta(seconds=2)).isoformat()}"
    r = requests.get(CMR, params={"short_name": MASK, "temporal": window, "page_size": 10}, timeout=60)
    r.raise_for_status()
    best = None
    for item in r.json().get("items", []):
        gid = item["umm"]["GranuleUR"]
        stamp, version = _stamp(gid)
        if stamp == _stamp(granule["granule_id"])[0] and (best is None or version > best[0]):
            best = (version, _data_url(item["umm"]))
    return best[1] if best else None


def _session():
    s = requests.Session()
    token = os.getenv("EARTHDATA_TOKEN") or os.getenv("NASA_EARTHDATA_TOKEN")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    elif os.getenv("EARTHDATA_USERNAME"):
        s.auth = (os.environ["EARTHDATA_USERNAME"], os.environ["EARTHDATA_PASSWORD"])
    else:
        raise PermissionError("EMIT download needs EARTHDATA_TOKEN or EARTHDATA_USERNAME/PASSWORD")
    return s


def download(url, dest):
    """Stream a granule to dest (resumable by re-running; partial file is replaced)."""
    dest = Path(dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _session().get(url, stream=True, timeout=(30, 600), allow_redirects=True) as r:
        if r.status_code in (401, 403):
            raise PermissionError(f"Earthdata refused the download ({r.status_code}); check the token")
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
    tmp.replace(dest)
    return dest


def _ortho_window(geotransform, glt_shape, bbox):
    """Row/col window of the ortho grid covering bbox, plus the window's geotransform."""
    x0, dx, _, y0, _, dy = geotransform                    # GDAL order, dy < 0
    c0 = int(np.floor((bbox[0] - x0) / dx))
    c1 = int(np.ceil((bbox[2] - x0) / dx))
    r0 = int(np.floor((bbox[3] - y0) / dy))
    r1 = int(np.ceil((bbox[1] - y0) / dy))
    r0, c0 = max(r0, 0), max(c0, 0)
    r1, c1 = min(r1, glt_shape[0]), min(c1, glt_shape[1])
    if r1 <= r0 or c1 <= c0:
        raise ValueError("bbox does not intersect the granule")
    transform = [dx, 0.0, x0 + c0 * dx, 0.0, dy, y0 + r0 * dy]   # rasterio/affine order
    return (r0, r1, c0, c1), transform


def ortho_crop(nc_path, bbox, var="reflectance"):
    """
    Returns (cube float32 (H, W, B) NaN outside the swath, wavelengths (B,),
    good_mask (B,) bool, georef dict(crs, transform, width, height)).
    For the MASK product var="mask" returns its 7 flag bands.
    """
    import netCDF4

    with netCDF4.Dataset(nc_path) as ds:
        ds.set_auto_mask(False)
        gt = [float(v) for v in ds.getncattr("geotransform")]
        glt_x = ds["location"]["glt_x"]
        (r0, r1, c0, c1), transform = _ortho_window(gt, glt_x.shape, bbox)
        gx = np.asarray(glt_x[r0:r1, c0:c1]).astype(np.int64)
        gy = np.asarray(ds["location"]["glt_y"][r0:r1, c0:c1]).astype(np.int64)
        valid = (gx > 0) & (gy > 0)
        if not valid.any():
            raise ValueError("bbox falls in the granule's no-data area")
        rows = gy[valid] - 1
        cols = gx[valid] - 1
        lo, hi = rows.min(), rows.max() + 1              # read only the swath rows needed
        data = np.asarray(ds[var][lo:hi, :, :], dtype=np.float32)
        h, w = gx.shape
        cube = np.full((h, w, data.shape[2]), np.nan, np.float32)
        cube[valid] = data[rows - lo, cols]
        if var == "reflectance":
            cube[cube <= FILL + 1] = np.nan
            sbp = ds["sensor_band_parameters"]
            wl = np.asarray(sbp["wavelengths"][:], dtype=float)
            good = np.asarray(sbp["good_wavelengths"][:]).astype(bool)
        else:
            wl = np.arange(data.shape[2], dtype=float)
            good = np.ones(data.shape[2], bool)
    georef = {"crs": "EPSG:4326", "transform": transform, "width": w, "height": h}
    return cube, wl, good, georef


def cloud_from_mask(mask_cube):
    """EMIT L2A MASK bands: 0 cloud, 1 cirrus, 2 water, 3 spacecraft, 4 dilated cloud."""
    m = np.nan_to_num(mask_cube[..., [0, 1, 3, 4]], nan=0.0)
    return (m > 0).any(axis=-1)
