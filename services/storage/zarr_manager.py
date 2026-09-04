# =============================================================
# OWNER: AARON
# =============================================================
import zarr
import numpy as np
from pathlib import Path
import rasterio
from rasterio.io import MemoryFile
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles
import logging
import os

logger = logging.getLogger(__name__)

class HyperspectralStorage:
    """
    Production-grade storage for hyperspectral cubes
    
    Strategy:
    - Zarr for chunked array storage (efficient I/O)
    - COG for geospatial compatibility
    - Chunking: 256×256×all_bands (balance between access patterns)
    """
    
    def __init__(self, storage_backend='minio'):
        self.backend = storage_backend
        
        if storage_backend == 'minio':
            import s3fs
            self.fs = s3fs.S3FileSystem(
                key=os.getenv('MINIO_ACCESS_KEY', 'minioadmin'),
                secret=os.getenv('MINIO_SECRET_KEY', 'minioadmin'),
                client_kwargs={'endpoint_url': os.getenv('MINIO_ENDPOINT_URL', 'http://minio:9000')}
            )
        else:
            self.fs = None  # Local filesystem
    
    def save_as_zarr(self, cube, wavelengths, metadata, output_path):
        """
        Save hyperspectral cube as chunked Zarr array
        """
        rows, cols, bands = cube.shape
        
        chunk_size = (256, 256, bands)
        
        if self.backend == 'minio':
            import s3fs
            store = s3fs.S3Map(f's3://hyperspectral-data/{output_path}', s3=self.fs)
        else:
            store = output_path
        
        z = zarr.open(
            store,
            mode='w',
            shape=(rows, cols, bands),
            chunks=chunk_size,
            dtype='float32',
            compressor=zarr.Blosc(cname='zstd', clevel=3, shuffle=2)
        )
        
        chunk_rows, chunk_cols, _ = chunk_size
        
        for i in range(0, rows, chunk_rows):
            for j in range(0, cols, chunk_cols):
                row_end = min(i + chunk_rows, rows)
                col_end = min(j + chunk_cols, cols)
                
                z[i:row_end, j:col_end, :] = cube[i:row_end, j:col_end, :]
        
        z.attrs['wavelengths'] = wavelengths
        z.attrs['metadata'] = metadata
        z.attrs['chunks'] = chunk_size
        
        logger.info(f"Saved Zarr: {output_path}, Shape: {cube.shape}, Chunks: {chunk_size}")
        
        return output_path
    
    def load_zarr(self, zarr_path, bbox=None):
        """
        Load hyperspectral cube from Zarr
        """
        if self.backend == 'minio':
            import s3fs
            store = s3fs.S3Map(f's3://hyperspectral-data/{zarr_path}', s3=self.fs)
        else:
            store = zarr_path
        
        z = zarr.open(store, mode='r')
        
        if bbox:
            row_min, row_max, col_min, col_max = bbox
            subset = z[row_min:row_max, col_min:col_max, :]
            return subset
        else:
            import dask.array as da
            return da.from_zarr(z)
    
    def save_as_cog(self, cube, wavelengths, metadata, output_path, crs='EPSG:4326', transform=None):
        """
        Save as Cloud Optimized GeoTIFF (COG)
        """
        rows, cols, bands = cube.shape
        
        memfile = MemoryFile()
        
        with memfile.open(
            driver='GTiff',
            height=rows,
            width=cols,
            count=bands,
            dtype=cube.dtype,
            crs=crs,
            transform=transform if transform else rasterio.transform.from_bounds(
                -180, -90, 180, 90, cols, rows
            )
        ) as dataset:
            for b in range(bands):
                dataset.write(cube[:, :, b], b + 1)
            
            dataset.update_tags(wavelengths=str(wavelengths))
            dataset.update_tags(**metadata)
        
        memfile.seek(0)
        
        cog_profile = cog_profiles.get('lzw')
        cog_profile.update({
            'BLOCKXSIZE': 256,
            'BLOCKYSIZE': 256,
            'TILED': True,
            'COMPRESS': 'LZW',
            'OVERVIEWS': 'AUTO'
        })
        
        with MemoryFile() as cog_memfile:
            cog_translate(
                memfile,
                cog_memfile.name,
                cog_profile,
                in_memory=True
            )
            
            cog_memfile.seek(0)
            cog_data = cog_memfile.read()
            
            if self.backend == 'minio':
                from minio import Minio
                client = Minio(
                    os.getenv('MINIO_ENDPOINT', 'minio:9000'),
                    access_key=os.getenv('MINIO_ACCESS_KEY', 'minioadmin'),
                    secret_key=os.getenv('MINIO_SECRET_KEY', 'minioadmin'),
                    secure=False
                )
                
                from io import BytesIO
                client.put_object(
                    'hyperspectral-data',
                    output_path,
                    BytesIO(cog_data),
                    length=len(cog_data),
                    content_type='image/tiff'
                )
            else:
                with open(output_path, 'wb') as f:
                    f.write(cog_data)
        
        logger.info(f"Saved COG: {output_path}, Shape: {cube.shape}")
        
        return output_path
    
    def load_cog_window(self, cog_path, window):
        if self.backend == 'minio':
            url = f'http://minio:9000/hyperspectral-data/{cog_path}'
        else:
            url = cog_path
        
        with rasterio.open(url) as src:
            data = src.read(window=window)
        
        return data
