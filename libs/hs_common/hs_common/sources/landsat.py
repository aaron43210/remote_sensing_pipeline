"""
Landsat 8/9 Collection 2 Level-2 surface temperature via a public STAC API.

Default: Microsoft Planetary Computer (free, anonymous SAS tokens). The
assets are Cloud-Optimized GeoTIFFs, so only the bbox window is read, never
the whole ~100 MB band. Works the same from a laptop or from AWS.

    item = search(bbox, "2024-06-01", "2024-09-30")
    st, qa, meta = read_window(item, bbox)    # ST DN, QA_PIXEL, transform/crs
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

log = logging.getLogger(__name__)

STAC_URL = os.getenv("LANDSAT_STAC_URL", "https://planetarycomputer.microsoft.com/api/stac/v1")
COLLECTION = "landsat-c2-l2"
SAS_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/" + COLLECTION
MAX_CLOUD = float(os.getenv("LANDSAT_MAX_CLOUD", "30"))
_token = {"value": None, "expires": 0.0}


def _sas():
    if "planetarycomputer" not in STAC_URL:
        return None
    if time.time() > _token["expires"] - 120:
        r = requests.get(SAS_URL, timeout=30)
        r.raise_for_status()
        d = r.json()
        _token["value"] = d["token"]
        exp = d.get("msft:expiry")
        _token["expires"] = (datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()
                             if exp else time.time() + 1800)
    return _token["value"]


def sign(href):
    tok = _sas()
    return f"{href}?{tok}" if tok else href


def search(bbox, date_from=None, date_to=None, max_cloud=MAX_CLOUD, limit=20):
    """Most recent clearest Landsat 8/9 L2 item covering the bbox, or None."""
    date_to = date_to or datetime.now(timezone.utc).date().isoformat()
    date_from = date_from or (datetime.fromisoformat(date_to) - timedelta(days=90)).date().isoformat()
    body = {"collections": [COLLECTION], "bbox": list(bbox), "limit": limit,
            "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": max_cloud},
                      "platform": {"in": ["landsat-8", "landsat-9"]}},
            "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
    r = requests.post(f"{STAC_URL}/search", json=body, timeout=60)
    r.raise_for_status()
    items = [it for it in r.json().get("features", []) if _covers(it, bbox)]
    if not items:
        return None
    # clearest first (in 10 % bands), then most recent
    return min(items, key=lambda it: (round(it["properties"].get("eo:cloud_cover", 100) / 10),
                                      -datetime.fromisoformat(it["properties"]["datetime"]
                                                              .replace("Z", "+00:00")).timestamp()))


def point_in_polygon(x, y, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def covers(geometry, bbox):
    """True when all four bbox corners fall inside the (multi)polygon footprint."""
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    corners = [(bbox[0], bbox[1]), (bbox[0], bbox[3]), (bbox[2], bbox[1]), (bbox[2], bbox[3])]
    return all(any(point_in_polygon(x, y, [tuple(p[:2]) for p in poly[0]]) for poly in polys)
               for x, y in corners)


def _covers(item, bbox):
    try:
        return covers(item["geometry"], bbox)
    except (KeyError, TypeError, ZeroDivisionError):
        return False


def _asset(item, *names):
    for n in names:
        if n in item["assets"]:
            return item["assets"][n]
    raise KeyError(f"none of {names} in {list(item['assets'])}")


def read_window(item, bbox):
    """
    Read ST_B10 and QA_PIXEL for the bbox only. Returns
    (st_dn uint16 (H, W), qa uint16 (H, W), meta) where meta has transform, crs,
    scale/offset (from STAC raster:bands), scene_id, acquired_at, cloud_cover.
    """
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    st_asset = _asset(item, "lwir11", "ST_B10")
    qa_asset = _asset(item, "qa_pixel", "QA_PIXEL")
    env = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MULTIRANGE": "YES",
           "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".TIF,.tif"}
    with rasterio.Env(**env):
        with rasterio.open(sign(st_asset["href"])) as src:
            b = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
            win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
            win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
            st = src.read(1, window=win)
            transform, crs, nodata = src.window_transform(win), src.crs.to_string(), src.nodata
        with rasterio.open(sign(qa_asset["href"])) as src:
            qa = src.read(1, window=win)
    rb = (st_asset.get("raster:bands") or [{}])[0]
    meta = {"scene_id": item["id"], "acquired_at": item["properties"]["datetime"],
            "cloud_cover": item["properties"].get("eo:cloud_cover"),
            "platform": item["properties"].get("platform"),
            "transform": list(transform)[:6], "crs": crs, "nodata": nodata,
            "scale": float(rb.get("scale", 0.00341802)), "offset": float(rb.get("offset", 149.0))}
    return st, qa, meta


def mtl_text(item):
    """The scene's MTL metadata as text (teammate's calibration reads it), or None."""
    for key in ("mtl.txt", "MTL.txt"):
        if key in item["assets"]:
            r = requests.get(sign(item["assets"][key]["href"]), timeout=60)
            if r.ok:
                return r.text
    return None


def to_celsius(st_dn, qa, scale, offset, nodata=0):
    """Simple conversion used for evaluation; the pipeline uses preprocessing/thermal."""
    st = st_dn.astype(np.float64)
    c = st * scale + offset - 273.15
    cloud = ((qa >> 3) & 1).astype(bool) | ((qa >> 1) & 1).astype(bool) | ((qa >> 4) & 1).astype(bool)
    bad = (st_dn == (nodata or 0)) | cloud | (c < -50) | (c > 70)
    return np.where(bad, np.nan, c)
