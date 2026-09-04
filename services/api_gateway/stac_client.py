# =============================================================
# OWNER: AARON
# =============================================================
import os
import logging
import tempfile
import shutil
import numpy as np
import zarr
import earthaccess
import xarray as xr
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env file
load_dotenv()

class STACFetcher:
    def __init__(self, minio_client=None):
        self.minio_client = minio_client
        
        # Login to NASA Earthdata (Uses EARTHDATA_USERNAME and EARTHDATA_PASSWORD from .env)
        try:
            earthaccess.login(strategy="environment", persist=False)
            logger.info("Successfully authenticated with NASA Earthdata.")
        except Exception as e:
            logger.error(f"NASA Earthdata Authentication Failed: {e}")
            raise ValueError(f"NASA Earthdata Auth Failed: {e}. Check your .env file.")

    def fetch_and_prepare(self, bbox: list, scene_id: str, redis_client=None):
        """
        bbox: [lon_min, lat_min, lon_max, lat_max]
        """
        if redis_client:
            redis_client.setex(f"status:{scene_id}", 3600, "FETCHING_NASA")
            
        logger.info(f"Searching NASA CMR for EMIT hyperspectral data for bbox: {bbox}")
        
        # EMIT L2A Estimated Surface Reflectance 
        short_name = "EMITL2AR"
        
        # 1. Search for granules
        results = earthaccess.search_data(
            short_name=short_name,
            bounding_box=tuple(bbox),
            count=1
        )
        
        if not results:
            raise ValueError(f"No NASA EMIT data found for the given bounding box {bbox}.")
            
        granule = results[0]
        logger.info(f"Found EMIT Granule: {granule}")

        local_dir = tempfile.mkdtemp()
        try:
            # 2. Download the NetCDF file
            logger.info("Downloading EMIT NetCDF file from NASA...")
            earthaccess.download([granule], local_path=local_dir)
            
            # Find the downloaded .nc file
            nc_files = [f for f in os.listdir(local_dir) if f.endswith('.nc') or f.endswith('.nc4')]
            if not nc_files:
                raise ValueError("Downloaded file not found or is not a NetCDF file.")
            
            nc_path = os.path.join(local_dir, nc_files[0])
            
            # 3. Read and process with Xarray
            logger.info(f"Processing hyperspectral data using Xarray: {nc_path}")
            
            # Open the dataset
            ds = xr.open_dataset(nc_path, engine="h5netcdf")
            
            ref = None
            if 'reflectance' in ds.variables:
                ref = ds['reflectance'].values
            else:
                var_names = list(ds.data_vars.keys())
                logger.info(f"Available variables: {var_names}")
                for var in var_names:
                    if len(ds[var].shape) == 3:
                        ref = ds[var].values
                        break
                        
            if ref is None:
                raise ValueError("Could not find a 3D reflectance variable in the NetCDF.")
                        
            # Get Wavelengths
            if 'wavelengths' in ds.variables:
                wavelengths = ds['wavelengths'].values.tolist()
            else:
                logger.warning("Wavelengths not found. Mocking 285 bands.")
                wavelengths = np.linspace(380, 2500, ref.shape[2]).tolist()
                
            # Clean data
            ref = np.nan_to_num(ref, nan=0.0)
            
            # Subsetting to a manageable size for the demo (256x256 center crop)
            h, w, b = ref.shape
            crop_size = min(256, h, w)
            start_h = max(0, (h - crop_size) // 2)
            start_w = max(0, (w - crop_size) // 2)
            
            cube = ref[start_h:start_h+crop_size, start_w:start_w+crop_size, :].astype(np.float32)
            
            logger.info(f"Extracted hyperspectral cube shape: {cube.shape}")
            
            # 4. Save to MinIO as Zarr
            if redis_client:
                redis_client.setex(f"status:{scene_id}", 3600, "SAVING_MINIO")
                
            zarr_path = f"raw-hyperspectral/{scene_id}/data.zarr"
            
            if self.minio_client:
                local_zarr = os.path.join(local_dir, 'data.zarr')
                z = zarr.open(
                    local_zarr, mode='w',
                    shape=cube.shape,
                    chunks=(256, 256, cube.shape[2]),
                    dtype='float32'
                )
                z[:] = cube
                z.attrs['wavelengths'] = wavelengths
                z.attrs['scene_id'] = scene_id
                z.attrs['source'] = "NASA EMIT (ISS)"
                z.attrs['bbox'] = bbox
                
                logger.info(f"Uploading Zarr to MinIO: {zarr_path}")
                self._upload_zarr(self.minio_client, 'raw-hyperspectral', zarr_path, local_zarr)
            
            return zarr_path, wavelengths, list(cube.shape)
            
        except Exception as e:
            logger.error(f"Failed to process EMIT data: {e}", exc_info=True)
            raise ValueError(f"Failed to process EMIT data: {e}")
        finally:
            shutil.rmtree(local_dir, ignore_errors=True)
            
    def _upload_zarr(self, minio_client, bucket, zarr_path, local_dir):
        """Upload Zarr store to MinIO."""
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)

        for root, dirs, files in os.walk(local_dir):
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, local_dir)
                object_name = f"{zarr_path}/{rel_path}"
                minio_client.fput_object(bucket, object_name, full_path)
