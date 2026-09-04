# =============================================================
# OWNER: AARON
# =============================================================
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from security.auth import auth_handler
from spatial_subsetting import router as subset_router
from shared.config import Settings
from shared.kafka_helpers import create_reliable_producer, send_with_callback
import tempfile
import os
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List
from stac_client import STACFetcher
import uuid
import redis
import json
from kafka import KafkaProducer
import msgpack
import threading
import logging
from datetime import datetime
from fastapi import Query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Hyperspectral API")
app.include_router(subset_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis
redis_client = redis.Redis(host='redis', port=6379, decode_responses=True)

# Kafka producer
producer = create_reliable_producer(Settings.KAFKA_BOOTSTRAP_SERVERS)

stac_fetcher = STACFetcher(minio_client=Settings.get_minio_client())

def safe_fget(minio_client, bucket, object_name, suffix='.json'):
    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        minio_client.fget_object(bucket, object_name, temp_path)
    except Exception:
        os.unlink(temp_path)
        raise
    return temp_path

class BBox(BaseModel):
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float

class ProcessRequest(BaseModel):
    bbox: BBox
    tasks: List[str] = ["agriculture", "mineral"]

# Background worker to listen for results
import threading

def listen_results():
    from kafka import KafkaConsumer
    consumer = KafkaConsumer(
        'results',
        bootstrap_servers='kafka:29092',
        value_deserializer=lambda m: msgpack.unpackb(m, raw=False)
    )
    for msg in consumer:
        data = msg.value
        scene_id = data['scene_id']
        redis_client.setex(f"result:{scene_id}", 3600, json.dumps(data))
        logger.info(f"Cached result for {scene_id}")

threading.Thread(target=listen_results, daemon=True).start()

@app.get("/")
async def root():
    return {"service": "Hyperspectral API", "version": "1.0.0"}

@app.get("/health")
async def health():
    try:
        redis_client.ping()
        return {"status": "healthy"}
    except:
        raise HTTPException(status_code=503, detail="Redis not reachable")

def run_nasa_fetch(bbox_list, scene_id, tasks, bbox_dict):
    try:
        # 1. Fetch real EMIT data from NASA
        zarr_path, wavelengths, shape = stac_fetcher.fetch_and_prepare(bbox_list, scene_id, redis_client=redis_client)
        
        # 2. Publish to Kafka
        msg = {
            'scene_id': scene_id,
            'zarr_path': zarr_path,
            'wavelengths': wavelengths,
            'shape': shape,
            'bbox': bbox_dict,
            'tasks': tasks,
            'timestamp': datetime.now().isoformat()
        }
        send_with_callback(producer, 'raw-data', msg)
        
        redis_client.setex(f"status:{scene_id}", 3600, "PROCESSING")
    except Exception as e:
        logger.error(f"Background fetch error: {e}")
        redis_client.setex(f"status:{scene_id}", 3600, f"ERROR: {str(e)}")

@app.post("/process")
async def process_scene(req: ProcessRequest, background_tasks: BackgroundTasks, user=Depends(auth_handler.verify_token)):
    """Submit a scene for processing via NASA EMIT fetching"""
    scene_id = f"EMIT_{uuid.uuid4().hex[:8]}"
    
    # Validate tasks
    valid_tasks = {"agriculture", "mineral"}
    invalid = set(req.tasks) - valid_tasks
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid tasks. Must be subset of {valid_tasks}")
        
    try:
        bbox_list = [req.bbox.lon_min, req.bbox.lat_min, req.bbox.lon_max, req.bbox.lat_max]
        
        redis_client.setex(f"status:{scene_id}", 3600, "STARTING")
        redis_client.setex(f"scene:{scene_id}:tasks", 600, json.dumps(req.tasks))
        
        background_tasks.add_task(run_nasa_fetch, bbox_list, scene_id, req.tasks, req.bbox.dict())
        
        return {"scene_id": scene_id, "status": "STARTING", "tasks": req.tasks}
    except Exception as e:
        logger.error(f"Process error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{scene_id}")
async def get_status(scene_id: str):
    status = redis_client.get(f"status:{scene_id}")
    if status:
        return {"scene_id": scene_id, "status": status}
    
    cached = redis_client.get(f"result:{scene_id}")
    if cached:
        return {"scene_id": scene_id, "status": "COMPLETED", "result": json.loads(cached)}
        
    return {"scene_id": scene_id, "status": "NOT_FOUND"}

@app.get("/results/{scene_id}")
async def get_results(scene_id: str):
    cached = redis_client.get(f"result:{scene_id}")
    if not cached:
        raise HTTPException(status_code=404, detail="Results not yet available")
    return json.loads(cached)


# ── Mineral Analysis Endpoints ──────────────────────

@app.post("/mineral/analyze")
async def analyze_minerals(
    scene_id: str,
    bbox: Optional[BBox] = None,
    background_tasks: BackgroundTasks = None,
    user=Depends(auth_handler.verify_token)
):
    msg = {
        "scene_id": scene_id,
        "bbox": bbox.dict() if bbox else None,
        "timestamp": datetime.now().isoformat()
    }
    send_with_callback(producer, 'process-requests', msg)
    redis_client.setex(f"mineral:{scene_id}:status", 600, "processing")
    return {"scene_id": scene_id, "status": "queued", "service": "mineral_analysis"}

@app.get("/mineral/results/{scene_id}")
async def get_mineral_results(scene_id: str):
    cached = redis_client.get(f"mineral:{scene_id}:results")
    if cached:
        return json.loads(cached)
    try:
        minio_client = Settings.get_minio_client()
        temp = safe_fget(minio_client, 'mineral-results', f'{scene_id}/summary.json', suffix='.json')
        try:
            with open(temp) as f:
                summary = json.load(f)
        finally:
            os.unlink(temp)
        return summary
    except Exception:
        raise HTTPException(status_code=404, detail=f"Mineral results not found for {scene_id}")

@app.get("/mineral/pixel/{scene_id}")
async def get_mineral_pixel(
    scene_id: str,
    x: int = Query(..., description="Pixel column"),
    y: int = Query(..., description="Pixel row")
):
    import numpy as np
    minio_client = Settings.get_minio_client()
    result = {}

    try:
        temp = safe_fget(minio_client, 'mineral-results', f'{scene_id}/mineral_class.npy', suffix='.npy')
        try:
            class_map = np.load(temp)
            result["mineral"] = str(class_map[y, x])
        finally:
            os.unlink(temp)
    except:
        result["mineral"] = "unknown"

    try:
        temp = safe_fget(minio_client, 'mineral-results', f'{scene_id}/abundances.npz', suffix='.npz')
        try:
            abundances = np.load(temp)
            result["abundances"] = {str(k): float(v[y, x]) for k, v in abundances.items()}
        finally:
            os.unlink(temp)
    except:
        result["abundances"] = {}

    try:
        temp = safe_fget(minio_client, 'mineral-results', f'{scene_id}/confidence.npy', suffix='.npy')
        try:
            conf_map = np.load(temp)
            result["confidence"] = float(conf_map[y, x])
        finally:
            os.unlink(temp)
    except:
        result["confidence"] = 0.0

    result["pixel"] = {"x": x, "y": y}
    return result

@app.get("/mineral/library")
async def get_mineral_library():
    import sys
    sys.path.append("/app/services/mineral_analysis")
    try:
        from spectral_library import MineralSpectralLibrary
        from config import DIAGNOSTIC_FEATURES
        lib = MineralSpectralLibrary()
        return {
            "minerals": lib.get_mineral_names(),
            "n_minerals": len(lib.get_mineral_names()),
            "wavelength_range": [float(lib.wavelengths.min()), float(lib.wavelengths.max())],
            "n_bands": len(lib.wavelengths),
            "diagnostic_features": {
                k: {"center": v["center"], "associated_minerals": v["minerals"]}
                for k, v in DIAGNOSTIC_FEATURES.items()
            }
        }
    except Exception as e:
        logger.error(f"Error loading mineral library: {e}")
        raise HTTPException(status_code=500, detail="Could not load mineral library")

# ── ML Endpoints (no auth for now) ────────────

@app.post("/ml/analyze")
async def analyze_ml(scene_id: str, bbox: Optional[BBox] = None, user=Depends(auth_handler.verify_token)):
    """
    Submit scene for ML inference.
    TODO: Add auth_handler.verify_token dependency later.
    """
    msg = {
        'scene_id': scene_id,
        'type': 'ml',
        'bbox': bbox.dict() if bbox else None,
        'timestamp': datetime.now().isoformat()
    }

    send_with_callback(producer, 'process-requests', msg)
    redis_client.setex(f"ml:{scene_id}:status", 600, "processing")

    return {
        'scene_id': scene_id,
        'status': 'queued',
        'service': 'ml_inference'
    }


@app.get("/ml/results/{scene_id}")
async def get_ml_results(scene_id: str):
    """Get ML inference results."""
    cached = redis_client.get(f"ml:{scene_id}:results")
    if cached:
        return json.loads(cached)

    try:
        minio_client = Settings.get_minio_client()
        temp = safe_fget(minio_client, 'ml-results', f'{scene_id}/summary.json', suffix='.json')
        try:
            with open(temp) as f:
                summary = json.load(f)
        finally:
            os.unlink(temp)

        redis_client.setex(f"ml:{scene_id}:results", 3600, json.dumps(summary))
        return summary
    except:
        raise HTTPException(status_code=404, detail="ML results not found")


@app.get("/ml/parameters/{scene_id}")
async def get_ml_parameters(
    scene_id: str,
    x: int = Query(..., description="Pixel column"),
    y: int = Query(..., description="Pixel row")
):
    """Get ML biophysical parameters for a specific pixel."""
    import numpy as np

    try:
        minio_client = Settings.get_minio_client()

        result = {}

        temp = safe_fget(minio_client, 'ml-results', f'{scene_id}/parameters.npz', suffix='.npz')
        try:
            params = np.load(temp)
            result['parameters'] = {str(k): float(v[y, x]) for k, v in params.items()}
        finally:
            os.unlink(temp)

        temp2 = safe_fget(minio_client, 'ml-results', f'{scene_id}/indices.npz', suffix='.npz')
        try:
            indices = np.load(temp2)
            result['indices'] = {str(k): float(v[y, x]) for k, v in indices.items()}
        finally:
            os.unlink(temp2)

        result['pixel'] = {'x': x, 'y': y, 'scene_id': scene_id}
        return result

    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Pixel data not found: {e}")


@app.get("/ml/compare/{scene_id}")
async def compare_ml_vs_physics(scene_id: str):
    """Compare ML vs Physics-only predictions."""
    import numpy as np

    try:
        minio_client = Settings.get_minio_client()

        comparison = {'scene_id': scene_id, 'models': {}}

        try:
            temp = safe_fget(minio_client, 'ml-results', f'{scene_id}/summary.json', suffix='.json')
            try:
                with open(temp) as f:
                    ml_summary = json.load(f)
            finally:
                os.unlink(temp)
            comparison['models']['ml'] = {
                'mean_chlorophyll': ml_summary.get('mean_chlorophyll', 0),
                'mean_lai': ml_summary.get('mean_lai', 0),
                'mean_ndvi': ml_summary.get('mean_ndvi', 0),
                'processing_time_sec': ml_summary.get('timing', {}).get('total_seconds', 0)
            }
        except:
            comparison['models']['ml'] = {'status': 'not_available'}

        try:
            temp = safe_fget(minio_client, 'biophysical-params', f'{scene_id}/biophysical.npz', suffix='.npz')
            try:
                params = np.load(temp)
            finally:
                os.unlink(temp)
            comparison['models']['physics'] = {
                'mean_chlorophyll': float(np.nanmean(params['chlorophyll'])),
                'mean_lai': float(np.nanmean(params['lai'])),
                'mean_water': float(np.nanmean(params['water']))
            }
        except:
            comparison['models']['physics'] = {'status': 'not_available'}

        return comparison
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Not available: {e}")


@app.get("/ml/model/info")
async def get_ml_model_info():
    """ML model metadata."""
    return {
        'model_name': 'PhysicsGuidedLightweightNet',
        'parameters': '~10,000',
        'model_size_kb': 40,
        'device': 'CPU',
        'inference_ms_per_pixel': 1.0,
        'physics_features': [
            'NDVI', 'Red Edge Position', 'Red Edge Slope',
            'Chlorophyll Absorption', 'Water Absorption',
            'NIR Mean', 'SWIR Mean', 'Brightness'
        ],
        'output': {
            'classification': '16 classes',
            'parameters': ['Chlorophyll', 'Water', 'LAI', 'Structure']
        }
    }
