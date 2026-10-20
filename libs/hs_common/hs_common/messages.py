"""
Kafka message contracts (JSON, schema_version 1).

These pydantic models are the source of truth; `python -m hs_common.messages`
writes contracts/*.schema.json, which the Go services are tested against.
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
Task = Literal["agriculture", "mineral", "thermal"]


def now():
    return datetime.now(timezone.utc).isoformat()


class Msg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    job_id: str
    ts: str = Field(default_factory=now)


class Georef(BaseModel):
    crs: str = "EPSG:4326"
    transform: list[float]               # affine a, b, c, d, e, f (rasterio order)
    width: int
    height: int


class IngestRequest(Msg):
    """ingest.requests: gateway -> ingestion."""
    user_id: Optional[str] = None
    bbox: list[float]                    # lon_min, lat_min, lon_max, lat_max
    tasks: list[Task]
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    source: Literal["nasa", "fixture"] = "nasa"


class RawHyperspectral(Msg):
    """raw.hyperspectral: ingestion -> preprocessing."""
    satellite: str = "EMIT"
    granule_id: str
    acquired_at: Optional[str] = None
    zarr_key: str                        # silver/{job}/raw.zarr (within S3_BUCKET)
    wavelengths: list[float]
    good_bands: Optional[list[bool]] = None
    cloud_key: Optional[str] = None      # silver/{job}/cloud.npy, True = cloud
    georef: Georef
    bbox: list[float]
    tasks: list[Task]
    source: str = "nasa"


class RawThermal(Msg):
    """raw.thermal: ingestion -> preprocessing-thermal."""
    scene_id: str
    acquired_at: Optional[str] = None
    st_key: str
    qa_key: Optional[str] = None
    mtl_key: Optional[str] = None
    bbox: list[float]
    source: str = "nasa"


class PreprocessedHyperspectral(Msg):
    """preprocessed.hyperspectral: preprocessing -> ml_inference."""
    satellite: str = "EMIT"
    granule_id: Optional[str] = None
    acquired_at: Optional[str] = None
    reflectance_key: str                 # full cleaned cube, all bands
    multiband_key: Optional[str] = None  # 16-band quick-look product
    wavelengths: list[float]
    cloud_key: Optional[str] = None
    georef: Georef
    bbox: list[float]
    tasks: list[Task]
    quality: dict = {}
    source: str = "nasa"


class PreprocessedThermal(Msg):
    """preprocessed.thermal: preprocessing-thermal -> ml_inference."""
    scene_id: str
    acquired_at: Optional[str] = None
    lst_key: str                         # COG, Celsius, nodata declared
    georef: Georef
    bbox: list[float]
    stats: dict = {}
    source: str = "nasa"


class Layer(BaseModel):
    name: str
    tif_key: str
    png_key: Optional[str] = None
    bounds: list[float]                  # lon_min, lat_min, lon_max, lat_max
    units: str = ""
    kind: Literal["continuous", "categorical"] = "continuous"
    colormap: str = "viridis"
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    classes: Optional[list[str]] = None
    stats: dict = {}


class MLResult(Msg):
    """ml.results: ml_inference -> orchestrator. One per task."""
    task: Task
    model_version: str
    layers: list[Layer]
    summary: dict = {}
    verification: dict = {}              # shadow agreement with the classical method
    timings: dict = {}
    acquired_at: Optional[str] = None


class JobStatus(Msg):
    """job.status: every service -> orchestrator."""
    stage: Literal["queued", "ingest", "preprocess", "inference", "done"]
    task: Optional[Task] = None
    state: Literal["RUNNING", "DONE", "ERROR", "NO_DATA", "RETRYING"]
    message: str = ""
    service: str = ""
    progress: Optional[float] = None


TOPICS = {
    "ingest.requests": IngestRequest,
    "raw.hyperspectral": RawHyperspectral,
    "raw.thermal": RawThermal,
    "preprocessed.hyperspectral": PreprocessedHyperspectral,
    "preprocessed.thermal": PreprocessedThermal,
    "ml.results": MLResult,
    "job.status": JobStatus,
}


def write_schemas(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for topic, model in TOPICS.items():
        (out / f"{topic}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")


if __name__ == "__main__":
    write_schemas(sys.argv[1] if len(sys.argv) > 1 else "contracts")
