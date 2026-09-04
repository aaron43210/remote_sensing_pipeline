# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
import rasterio
from rasterio.windows import Window, from_bounds
import zarr
import s3fs
import numpy as np
import os
import logging

logger = logging.getLogger(__name__)

class HyperspectralDataLoader:
    def __init__(self, minio_endpoint, access_key, secret_key, bucket="raw-hyperspectral"):
        self.minio_endpoint = minio_endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        
        # Setup S3 filesystem for Zarr
        self.s3 = s3fs.S3FileSystem(
            client_kwargs={'endpoint_url': f"http://{self.minio_endpoint}"},
            key=self.access_key,
            secret=self.secret_key,
            use_ssl=False
        )
        
        # Ensure bucket exists
        if not self.s3.exists(self.bucket):
            self.s3.mkdir(self.bucket)

    def ingest_and_tile(self, file_path, scene_id, bbox=None, chunk_size=256):
        """
        Reads a hyperspectral image, extracts ROI if bbox is given,
        tiles into chunk_size x chunk_size, and saves to Zarr in MinIO.
        Returns a list of tile metadata to be published to Kafka.
        """
        tiles_metadata = []
        
        with rasterio.open(file_path) as src:
            # Determine window
            if bbox:
                # bbox = [lon_min, lat_min, lon_max, lat_max]
                lon_min, lat_min, lon_max, lat_max = bbox
                window = from_bounds(lon_min, lat_min, lon_max, lat_max, src.transform)
                # Ensure window is within bounds
                window = window.intersection(Window(0, 0, src.width, src.height))
            else:
                window = Window(0, 0, src.width, src.height)
            
            # Wavelengths mapping (mock for now, but usually in src.tags)
            wavelengths = src.tags().get('wavelengths', None)
            if not wavelengths:
                # Default AVIRIS wavelengths mapping
                wavelengths = np.linspace(400, 2500, src.count).tolist()
                
            # Iterate over the window in chunk_size
            col_off = int(window.col_off)
            row_off = int(window.row_off)
            width = int(window.width)
            height = int(window.height)
            
            for i, row in enumerate(range(row_off, row_off + height, chunk_size)):
                for j, col in enumerate(range(col_off, col_off + width, chunk_size)):
                    # Determine chunk width and height (edge cases)
                    c_width = min(chunk_size, col_off + width - col)
                    c_height = min(chunk_size, row_off + height - row)
                    
                    c_window = Window(col, row, c_width, c_height)
                    
                    # Read chunk data (Bands, Rows, Cols) -> (Rows, Cols, Bands)
                    data = src.read(window=c_window)
                    data = np.transpose(data, (1, 2, 0)) 
                    
                    # Create Zarr store path in MinIO
                    tile_id = f"{i}_{j}"
                    zarr_path = f"{self.bucket}/{scene_id}/{tile_id}.zarr"
                    
                    store = s3fs.S3Map(root=zarr_path, s3=self.s3, check=False)
                    
                    # Save to Zarr array
                    z_arr = zarr.array(
                        data, 
                        store=store, 
                        chunks=(c_height, c_width, src.count), 
                        overwrite=True
                    )
                    
                    # Compile metadata for Kafka
                    metadata = {
                        'scene_id': scene_id,
                        'tile_id': tile_id,
                        'zarr_path': f"{scene_id}/{tile_id}.zarr",
                        'shape': data.shape,
                        'wavelengths': wavelengths,
                        'grid_position': {'row': i, 'col': j},
                        'bbox_subset': bbox is not None
                    }
                    tiles_metadata.append(metadata)
                    
        return tiles_metadata
