# =============================================================
# OWNER: AARON
# =============================================================
"""
Spatial Subsetting Router

Mounted into main.py as an APIRouter.
NOT a standalone FastAPI app.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subset", tags=["spatial"])


class BBox(BaseModel):
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float


class SubsetRequest(BaseModel):
    scene_id: str
    bbox: BBox


def get_minio():
    """Lazy MinIO client from environment."""
    from minio import Minio
    return Minio(
        os.getenv('MINIO_ENDPOINT', 'minio:9000'),
        access_key=os.getenv('MINIO_ACCESS_KEY'),
        secret_key=os.getenv('MINIO_SECRET_KEY'),
        secure=False,
    )


@router.post("/extract")
async def extract_subset(request: SubsetRequest):
    """
    Extract spatial subset from a scene using bounding box.

    Uses windowed reading to avoid loading full scene.
    """
    import rasterio
    from rasterio.windows import from_bounds

    minio_client = get_minio()
    scene_id = request.scene_id
    bbox = request.bbox

    local_path = None
    try:
        fd, local_path = tempfile.mkstemp(suffix='.tif')
        os.close(fd)

        # Download scene
        minio_client.fget_object(
            'hyperspectral-data',
            f'{scene_id}/data.tif',
            local_path
        )

        with rasterio.open(local_path) as src:
            # Convert bbox to pixel window
            window = from_bounds(
                bbox.lon_min, bbox.lat_min,
                bbox.lon_max, bbox.lat_max,
                src.transform
            )

            # Read only the windowed portion
            subset = src.read(window=window)
            subset_transform = src.window_transform(window)

            return {
                'scene_id': scene_id,
                'bbox': bbox.dict(),
                'subset_shape': list(subset.shape),
                'transform': list(subset_transform)[:6],
                'crs': str(src.crs),
                'status': 'success'
            }

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Scene {scene_id} not found")
    except Exception as e:
        logger.error(f"Subset error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if local_path and os.path.exists(local_path):
            os.unlink(local_path)


@router.get("/tiles/{scene_id}")
async def list_tiles(
    scene_id: str,
    tile_size: int = Query(256, ge=64, le=1024)
):
    """
    List tiles for distributed processing.
    """
    import rasterio

    minio_client = get_minio()
    local_path = None

    try:
        fd, local_path = tempfile.mkstemp(suffix='.tif')
        os.close(fd)

        minio_client.fget_object(
            'hyperspectral-data',
            f'{scene_id}/data.tif',
            local_path
        )

        with rasterio.open(local_path) as src:
            rows, cols = src.height, src.width
            bands = src.count

            tiles = []
            tile_id = 0
            for row_off in range(0, rows, tile_size):
                for col_off in range(0, cols, tile_size):
                    h = min(tile_size, rows - row_off)
                    w = min(tile_size, cols - col_off)
                    tiles.append({
                        'tile_id': tile_id,
                        'row_offset': row_off,
                        'col_offset': col_off,
                        'height': h,
                        'width': w
                    })
                    tile_id += 1

            return {
                'scene_id': scene_id,
                'tile_size': tile_size,
                'scene_shape': [rows, cols, bands],
                'total_tiles': len(tiles),
                'tiles': tiles
            }

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Scene {scene_id} not found")
    finally:
        if local_path and os.path.exists(local_path):
            os.unlink(local_path)
