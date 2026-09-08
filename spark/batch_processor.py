# =============================================================
# OWNER: AARON
# =============================================================
"""
Spark Batch Processor

Stitches distributed processing results into
Cloud Optimized GeoTIFFs (COG) for delivery.

Responsibilities:
1. Read processed Zarr tiles from MinIO
2. Stitch tiles into complete scene
3. Convert to COG format
4. Upload final products
5. Generate overview statistics

Run modes:
- Spark cluster mode (production)
- Local fallback (development/testing)
"""

import os
import logging
import tempfile
import json
import numpy as np
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


class COGStitcher:
    """
    Stitches processed results into Cloud Optimized GeoTIFF.
    """

    def __init__(self):
        from shared.config import Settings
        self.minio = Settings.get_minio_client()

    def stitch_and_export(self, scene_id, source_bucket, result_prefix):
        """
        Stitch results and export as COG.

        Args:
            scene_id: Scene identifier
            source_bucket: MinIO bucket with results
            result_prefix: Object prefix
        """
        import rasterio
        from rasterio.transform import from_bounds
        from rio_cogeo.cogeo import cog_translate
        from rio_cogeo.profiles import cog_profiles

        local_dir = tempfile.mkdtemp()

        # Download all result files
        files = {}
        objects = self.minio.list_objects(
            source_bucket, prefix=result_prefix, recursive=True
        )

        for obj in objects:
            fname = os.path.basename(obj.object_name)
            local_path = os.path.join(local_dir, fname)
            self.minio.fget_object(
                source_bucket, obj.object_name, local_path
            )
            files[fname] = local_path

        # Load main result (classification or parameters)
        result_data = None
        for key in ['classification.npy', 'mineral_class.npy']:
            if key in files:
                result_data = np.load(files[key])
                break

        if result_data is None:
            for key in ['parameters.npz', 'abundances.npz']:
                if key in files:
                    data = np.load(files[key])
                    result_data = data[list(data.keys())[0]]
                    break

        if result_data is None:
            logger.error(f"No result data found for {scene_id}")
            return None

        # Load additional layers
        layers = {'classification': result_data}

        for fname, fpath in files.items():
            if fname.endswith('.npy') and fname not in ['classification.npy', 'mineral_class.npy']:
                name = fname.replace('.npy', '')
                layers[name] = np.load(fpath)
            elif fname.endswith('.npz'):
                data = np.load(fpath)
                for key in data.keys():
                    layers[key] = data[key]

        # Create COG
        output_path = os.path.join(local_dir, f'{scene_id}_final.tif')
        rows, cols = result_data.shape

        # Default transform (can be overridden with actual georeferencing)
        transform = from_bounds(
            -180, -90, 180, 90, cols, rows
        )

        n_bands = len(layers)
        band_names = list(layers.keys())

        profile = {
            'driver': 'GTiff',
            'height': rows,
            'width': cols,
            'count': n_bands,
            'dtype': 'float32',
            'crs': 'EPSG:4326',
            'transform': transform,
            'tiled': True,
            'blockxsize': 256,
            'blockysize': 256,
        }

        # Write temporary GeoTIFF
        temp_tif = os.path.join(local_dir, 'temp.tif')
        with rasterio.open(temp_tif, 'w', **profile) as dst:
            for i, (name, data) in enumerate(layers.items()):
                dst.write(data.astype(np.float32), i + 1)
                dst.set_band_description(i + 1, name)

            # Add metadata
            dst.update_tags(
                scene_id=scene_id,
                created=datetime.now().isoformat(),
                bands=','.join(band_names)
            )

        # Convert to COG
        cog_profile = cog_profiles.get('lzw')
        cog_translate(
            temp_tif,
            output_path,
            cog_profile,
            in_memory=False
        )

        # Upload COG
        cog_bucket = 'cog-products'
        if not self.minio.bucket_exists(cog_bucket):
            self.minio.make_bucket(cog_bucket)

        self.minio.fput_object(
            cog_bucket,
            f'{scene_id}/{scene_id}_final.tif',
            output_path
        )

        # Generate statistics
        stats = self._compute_statistics(layers)

        stats_path = os.path.join(local_dir, 'stats.json')
        stats['scene_id'] = scene_id
        stats['cog_path'] = f'{cog_bucket}/{scene_id}/{scene_id}_final.tif'
        stats['band_names'] = band_names
        stats['shape'] = [rows, cols]
        stats['n_bands'] = n_bands

        with open(stats_path, 'w') as f:
            json.dump(stats, f, indent=2)

        self.minio.fput_object(
            cog_bucket,
            f'{scene_id}/statistics.json',
            stats_path
        )

        logger.info(
            f"COG exported: {scene_id}, "
            f"shape={rows}x{cols}, bands={n_bands}"
        )

        # Cleanup temp
        temp_tif_check = os.path.join(local_dir, 'temp.tif')
        if os.path.exists(temp_tif_check):
            os.remove(temp_tif_check)

        return stats

    def _compute_statistics(self, layers):
        """Compute per-band statistics."""
        stats = {}
        for name, data in layers.items():
            valid = data[~np.isnan(data)]
            if len(valid) > 0:
                stats[name] = {
                    'min': float(np.min(valid)),
                    'max': float(np.max(valid)),
                    'mean': float(np.mean(valid)),
                    'std': float(np.std(valid)),
                    'median': float(np.median(valid)),
                    'valid_fraction': float(len(valid) / data.size)
                }
        return stats


def run_spark_job():
    """Run distributed stitching with Spark."""
    try:
        from pyspark.sql import SparkSession

        spark = SparkSession.builder \
            .appName("HyperspectralBatch") \
            .getOrCreate()

        stitcher = COGStitcher()

        # Get list of scenes to process
        from shared.config import Settings
        minio_client = Settings.get_minio_client()

        # Collect scene IDs from consolidated ML results (Hydra MTL)
        scenes = []
        try:
            objects = minio_client.list_objects(
                'ml-results', prefix='', recursive=False
            )
            for obj in objects:
                if obj.is_dir:
                    scene_id = obj.object_name.rstrip('/')
                    scenes.append(scene_id)
        except Exception:
            pass

        if not scenes:
            logger.warning("No scenes found to process")
            spark.stop()
            return

        logger.info(f"Processing {len(scenes)} scenes")

        # Distributed processing
        rdd = spark.sparkContext.parallelize(scenes, numSlices=min(len(scenes), 8))

        def process_scene(scene_id):
            results = {}

            # Stitch consolidated results
            try:
                stats = stitcher.stitch_and_export(
                    scene_id, 'ml-results', f'{scene_id}/'
                )
                if stats:
                    results['hydra_results'] = stats
            except Exception as e:
                results['hydra_error'] = str(e)

            return (scene_id, results)

        # Avoid .collect() which can OOM the driver if there are many scenes
        def process_and_log(scene_id):
            scene, result = process_scene(scene_id)
            logger.info(f"Stitched {scene}: {list(result.keys())}")
            
        rdd.foreach(process_and_log)

        spark.stop()

    except ImportError:
        logger.info("PySpark not available, running locally")
        run_local()


def run_local():
    """Local processing without Spark."""
    from shared.config import Settings
    minio_client = Settings.get_minio_client()

    stitcher = COGStitcher()

    for bucket, prefix in [('ml-results', '')]:
        try:
            objects = minio_client.list_objects(
                bucket, prefix=prefix, recursive=False
            )
            for obj in objects:
                if obj.is_dir:
                    scene_id = obj.object_name.rstrip('/')
                    logger.info(f"Stitching {scene_id} from {bucket}")
                    stitcher.stitch_and_export(
                        scene_id, bucket, f'{scene_id}/'
                    )
        except Exception as e:
            logger.error(f"Error processing {bucket}: {e}")


if __name__ == "__main__":
    mode = os.getenv('SPARK_MODE', 'local')
    if mode == 'spark':
        run_spark_job()
    else:
        run_local()
