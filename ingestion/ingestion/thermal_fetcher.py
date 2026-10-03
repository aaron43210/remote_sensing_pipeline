# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Landsat Thermal Data Fetcher

Owned by: ANANTAHANARAYANAN (Ingestion service)

This module fetches Landsat 9 Collection 2 Level-2 Surface Temperature
products from NASA Earthdata and publishes them to the Kafka topic
'thermal-raw' for the thermal processing service (Bainty Kaur) to consume.

Flow:
    NASA Earthdata (Landsat 9 TIRS L2)
            ↓
    Download ST_B10 (Surface Temperature band)
            ↓
    Convert: DN × 0.00341802 + 149.0 = Kelvin → -273.15 = Celsius
            ↓
    Save COG to MinIO bucket 'thermal-data'
            ↓
    Publish scene metadata to Kafka 'thermal-raw'

Landsat 9 Specifications:
    - TIRS Band 10: centre wavelength 10.9μm (10.6–11.2μm)
    - Spatial resolution: 30m (resampled from 100m native)
    - Revisit cycle: 16 days
    - Calibration: Scale = 0.00341802, Offset = 149.0 K

References:
    - Malakar et al. (2018): Landsat TIRS instrument emulator. Remote Sensing.
    - Cook et al. (2014): Landsat TIRS Surface Temperature algorithm. JSTAR.
    - USGS Landsat Collection 2 Level-2 Science Product Guide (2021).

Environment Variables Required (set in ingestion service, NOT thermal):
    EARTHDATA_USERNAME  — NASA Earthdata username
    EARTHDATA_PASSWORD  — NASA Earthdata password
    MINIO_ENDPOINT      — MinIO endpoint
    MINIO_ACCESS_KEY    — MinIO access key
    MINIO_SECRET_KEY    — MinIO secret key
    KAFKA_BOOTSTRAP_SERVERS

NOTE TO ANANTAHANARAYANAN:
    - Add calls to fetch_and_publish() inside services/ingestion/main.py
    - The thermal processing service (Bainty) will automatically pick up
      what you publish to 'thermal-raw'
    - You can trigger thermal fetching alongside hyperspectral ingestion,
      or separately based on API requests
"""

import os
import json
import logging
import shutil
import tempfile
import numpy as np
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class LandsatThermalFetcher:
    """
    Fetch Landsat 9 thermal (Surface Temperature) data from NASA Earthdata.

    Authentication: Uses EARTHDATA_USERNAME + EARTHDATA_PASSWORD from env.
    Download: Landsat C2 L2 Surface Temperature product (ST_B10 band).

    Usage (add to services/ingestion/main.py):

        from thermal_fetcher import LandsatThermalFetcher

        fetcher = LandsatThermalFetcher(
            minio_client=minio_client,
            kafka_producer=producer
        )
        fetcher.fetch_and_publish(bbox=[-117.5, 33.0, -117.0, 33.5])
    """

    # Landsat Collection 2 Level-2 short name
    COLLECTION_SHORT_NAME = "LANDSAT_9_C2_L2"

    # Kafka topic that thermal processing service reads
    KAFKA_TOPIC = "thermal-raw"

    # MinIO bucket for storing thermal COGs
    MINIO_BUCKET = "thermal-data"

    # DN to Kelvin conversion constants (Landsat C2 L2 ST)
    ST_SCALE  = 0.00341802   # Kelvin per DN
    ST_OFFSET = 149.0        # Add offset (Kelvin)

    def __init__(self, minio_client=None, kafka_producer=None):
        self.minio    = minio_client
        self.producer = kafka_producer
        self._authenticated = False

    def _authenticate(self):
        """Authenticate with NASA Earthdata."""
        import earthaccess
        if self._authenticated:
            return
        try:
            earthaccess.login(strategy="environment")  # reads EARTHDATA_* env vars
            logger.info("NASA Earthdata authenticated via environment variables")
        except Exception:
            earthaccess.login(strategy="netrc")        # fallback to ~/.netrc
            logger.info("NASA Earthdata authenticated via .netrc")
        self._authenticated = True

    def fetch_and_publish(self, bbox: list,
                          max_days_back: int = 60,
                          max_cloud_cover: int = 30) -> dict:
        """
        Full pipeline: search → download → convert → save to MinIO → publish to Kafka.

        Args:
            bbox: [lon_min, lat_min, lon_max, lat_max]
            max_days_back: search window in days (Landsat revisit = 16 days)
            max_cloud_cover: maximum cloud cover percentage

        Returns:
            dict with scene_id and metadata
        """
        import earthaccess

        self._authenticate()

        local_dir = tempfile.mkdtemp(prefix="landsat_thermal_")
        try:
            # ── Search ───────────────────────────────────────────────────
            end_date   = datetime.utcnow()
            start_date = end_date - timedelta(days=max_days_back)

            logger.info("Searching Landsat 9 thermal: bbox=%s, days_back=%d", bbox, max_days_back)

            results = earthaccess.search_data(
                short_name=self.COLLECTION_SHORT_NAME,
                bounding_box=tuple(bbox),
                temporal=(
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d"),
                ),
                count=5,
            )

            if not results:
                raise ValueError(
                    f"No Landsat thermal scenes found for bbox={bbox} "
                    f"in last {max_days_back} days. "
                    f"Landsat has a 16-day revisit cycle — try increasing max_days_back."
                )

            scene = results[0]
            logger.info("Selected: %s", str(scene))

            # ── Download ─────────────────────────────────────────────────
            downloaded = earthaccess.download([scene], local_path=local_dir)

            if not downloaded:
                raise ValueError("Earthdata download returned empty list")

            # ── Find thermal (ST) band ────────────────────────────────────
            thermal_file = None
            qa_file      = None
            mtl_file     = None

            for path in downloaded:
                name = os.path.basename(path).lower()
                if "st_b10" in name and path.endswith(".tif"):
                    thermal_file = path
                elif "qa_pixel" in name:
                    qa_file = path
                elif "mtl" in name:
                    mtl_file = path

            if thermal_file is None:
                # Fallback: any .tif with 'st' in name
                for path in downloaded:
                    if path.endswith(".tif") and "st" in os.path.basename(path).lower():
                        thermal_file = path
                        break

            if thermal_file is None:
                files = [os.path.basename(f) for f in downloaded]
                raise ValueError(f"ST_B10 band not found. Files: {files}")

            # ── Convert to Celsius COG ────────────────────────────────────
            lst_celsius, transform, crs, metadata = self._read_and_convert(
                thermal_file, bbox, mtl_file
            )

            scene_id = (
                f"LANDSAT9_THERMAL_"
                f"{os.path.basename(thermal_file).split('.')[0]}"
            )

            # Save as COG
            local_cog = os.path.join(local_dir, "lst.tif")
            self._save_cog(lst_celsius, transform, crs, local_cog, metadata)

            # ── Upload to MinIO ───────────────────────────────────────────
            if self.minio:
                if not self.minio.bucket_exists(self.MINIO_BUCKET):
                    self.minio.make_bucket(self.MINIO_BUCKET)
                thermal_minio_path = f"{scene_id}/lst.tif"
                self.minio.fput_object(
                    self.MINIO_BUCKET, thermal_minio_path, local_cog
                )

                # Save metadata JSON
                meta_path = os.path.join(local_dir, "metadata.json")
                with open(meta_path, "w") as f:
                    json.dump(metadata, f, indent=2)
                self.minio.fput_object(
                    self.MINIO_BUCKET, f"{scene_id}/metadata.json", meta_path
                )

            # ── Publish to Kafka (for thermal processing service) ─────────
            if self.producer:
                import msgpack
                msg = {
                    "scene_id":     scene_id,
                    "thermal_path": f"{scene_id}/lst.tif",
                    "shape":        list(lst_celsius.shape),
                    "bbox":         bbox,
                    "sensor":       "Landsat 9 TIRS",
                    "band":         "B10 (10.9μm)",
                    "resolution_m": 30,
                    "metadata":     metadata,
                    "timestamp":    datetime.now().isoformat(),
                }
                self.producer.send(
                    self.KAFKA_TOPIC,
                    msgpack.packb(msg, use_bin_type=True),
                )
                self.producer.flush()
                logger.info(
                    "Published to '%s': scene_id=%s, T=[%.1f, %.1f]°C",
                    self.KAFKA_TOPIC, scene_id,
                    metadata.get("temp_min_c", 0),
                    metadata.get("temp_max_c", 0),
                )

            return {"scene_id": scene_id, "metadata": metadata}

        finally:
            shutil.rmtree(local_dir, ignore_errors=True)

    def _read_and_convert(self, thermal_file, bbox, mtl_file=None):
        """Read thermal band and convert DN → Kelvin → Celsius."""
        import rasterio
        from rasterio.windows import from_bounds

        metadata = self._parse_mtl(mtl_file) if mtl_file else {}

        with rasterio.open(thermal_file) as src:
            window = from_bounds(
                bbox[0], bbox[1], bbox[2], bbox[3], src.transform
            )
            window = window.intersection(
                rasterio.windows.Window(0, 0, src.width, src.height)
            )

            if window.width <= 0 or window.height <= 0:
                raise ValueError("bbox falls outside thermal scene extent")

            dn        = src.read(1, window=window).astype(np.float32)
            transform = src.window_transform(window)
            crs       = src.crs

        # Read scale/offset from MTL if available
        scale  = float(metadata.get("SCALE_FACTOR", self.ST_SCALE))
        offset = float(metadata.get("ADD_OFFSET",   self.ST_OFFSET))

        kelvin  = dn * scale + offset
        celsius = kelvin - 273.15

        # Mask unrealistic values
        celsius[celsius < -50] = np.nan
        celsius[celsius > 70]  = np.nan

        valid = celsius[~np.isnan(celsius)]
        if len(valid) > 0:
            metadata.update({
                "temp_min_c":  float(np.min(valid)),
                "temp_max_c":  float(np.max(valid)),
                "temp_mean_c": float(np.mean(valid)),
                "temp_std_c":  float(np.std(valid)),
            })
        else:
            metadata.update({"temp_min_c": 0, "temp_max_c": 0,
                             "temp_mean_c": 0, "temp_std_c": 0})

        metadata.update({"scale": scale, "offset": offset, "bbox": bbox})

        # Replace NaN with 0 for storage
        celsius = np.nan_to_num(celsius, nan=0.0)

        logger.info(
            "Converted thermal: shape=%s, T=[%.1f, %.1f]°C",
            celsius.shape,
            metadata["temp_min_c"],
            metadata["temp_max_c"],
        )

        return celsius, transform, crs, metadata

    def _parse_mtl(self, mtl_file: str) -> dict:
        """Parse Landsat MTL metadata file into a flat dict."""
        meta = {}
        try:
            with open(mtl_file) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"')
                        try:
                            meta[k] = float(v)
                        except ValueError:
                            meta[k] = v
        except Exception as e:
            logger.warning("MTL parse failed: %s", e)
        return meta

    def _save_cog(self, data, transform, crs, path, metadata=None):
        """Save numpy array as a tiled, LZW-compressed GeoTIFF."""
        import rasterio

        with rasterio.open(
            path, "w",
            driver="GTiff",
            height=data.shape[0],
            width=data.shape[1],
            count=1,
            dtype="float32",
            crs=crs,
            transform=transform,
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="lzw",
        ) as dst:
            dst.write(data, 1)
            dst.update_tags(
                sensor="Landsat 9 TIRS",
                band="Surface Temperature (°C)",
                processing_level="L2_ST_C2",
            )
            if metadata:
                dst.update_tags(**{
                    k: str(v) for k, v in metadata.items()
                    if isinstance(v, (str, int, float))
                })
