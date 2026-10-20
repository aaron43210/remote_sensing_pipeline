"""
Landsat branch: find the clearest recent scene, read ONLY the bbox window of
ST_B10 and QA_PIXEL (cloud-optimized GeoTIFFs), stage them with the MTL file
under silver/{job}/landsat/ using Landsat file names, so the preprocessing
thermal module finds QA and MTL beside the band exactly as on disk.
"""

import hashlib
import io

import numpy as np

from hs_common import storage
from hs_common.kafka import NoData
from hs_common.sources import landsat
from hs_common.storage import keys

ST_SCALE, ST_OFFSET = 0.00341802, 149.0
CLEAR_QA = 21824                       # Landsat 8/9 QA_PIXEL: clear, low confidence clouds


def _tif_bytes(arr, transform, crs, nodata):
    import rasterio
    from rasterio.transform import Affine
    buf = io.BytesIO()
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1, dtype=arr.dtype.name,
                     crs=crs, transform=Affine(*transform), nodata=nodata, tiled=True, blockxsize=256,
                     blockysize=256, compress="deflate") as dst:
            dst.write(arr, 1)
        buf.write(mf.read())
    return buf.getvalue()


def _fixture_mtl(scene_id):
    return (f'GROUP = LANDSAT_METADATA_FILE\n  GROUP = PRODUCT_CONTENTS\n    LANDSAT_PRODUCT_ID = "{scene_id}"\n'
            f"  END_GROUP = PRODUCT_CONTENTS\n  GROUP = LEVEL2_SURFACE_TEMPERATURE_PARAMETERS\n"
            f"    TEMPERATURE_MULT_BAND_ST_B10 = {ST_SCALE}\n    TEMPERATURE_ADD_BAND_ST_B10 = {ST_OFFSET}\n"
            f"  END_GROUP = LEVEL2_SURFACE_TEMPERATURE_PARAMETERS\nEND_GROUP = LANDSAT_METADATA_FILE\nEND\n")


def fetch_fixture(req):
    from hydra import synthetic
    seed = int(hashlib.sha256(",".join(f"{v:.4f}" for v in req["bbox"]).encode()).hexdigest()[:8], 16) % 10_000
    lst, georef = synthetic.lst_scene(seed, (128, 128), tuple(req["bbox"]))
    dn = np.where(np.isfinite(lst), np.round((lst + 273.15 - ST_OFFSET) / ST_SCALE), 0).astype(np.uint16)
    qa = np.where(np.isfinite(lst), CLEAR_QA, 1).astype(np.uint16)          # bit 0 = fill
    scene_id = f"FIXTURE_LC09_{seed:04d}"
    meta = {"scene_id": scene_id, "acquired_at": "2024-07-01T18:30:00Z", "transform": georef["transform"],
            "crs": "EPSG:4326", "nodata": 0}
    return dn, qa, meta, _fixture_mtl(scene_id)


def run(req):
    """Returns the raw.thermal message body (dict)."""
    if req.get("source") == "fixture":
        st, qa, meta, mtl = fetch_fixture(req)
    else:
        item = landsat.search(req["bbox"], req.get("date_from"), req.get("date_to"))
        if item is None:
            raise NoData("no clear Landsat 8/9 scene covers this area in the date range")
        st, qa, meta = landsat.read_window(item, req["bbox"])
        mtl = landsat.mtl_text(item)
        if (st == 0).mean() > 0.95:
            raise NoData("the Landsat scene has no valid pixels in this area")
    job, sid = req["job_id"], meta["scene_id"]
    base = keys.silver(job, f"landsat/{sid}")
    st_key = storage.put_bytes(f"{base}_ST_B10.TIF", _tif_bytes(st, meta["transform"], meta["crs"], 0), "image/tiff")
    qa_key = storage.put_bytes(f"{base}_QA_PIXEL.TIF", _tif_bytes(qa, meta["transform"], meta["crs"], 1), "image/tiff")
    mtl_key = storage.put_bytes(f"{base}_MTL.txt", mtl.encode(), "text/plain") if mtl else None
    return {"job_id": job, "scene_id": sid, "acquired_at": meta["acquired_at"], "st_key": st_key,
            "qa_key": qa_key, "mtl_key": mtl_key, "bbox": req["bbox"], "source": req.get("source", "nasa")}
