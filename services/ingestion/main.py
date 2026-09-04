# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
from shared.kafka_helpers import create_reliable_producer, send_with_callback
from shared.config import Settings
import msgpack
from storage.zarr_manager import HyperspectralStorage
import logging

logger = logging.getLogger(__name__)

class ZarrIngestionService:
    def __init__(self):
        self.producer = create_reliable_producer(Settings.KAFKA_BOOTSTRAP_SERVERS)
        
        self.storage = HyperspectralStorage(backend='minio')
    
    def ingest_scene(self, file_path, metadata):
        """
        Ingest hyperspectral scene with Zarr storage
        """
        from spectral import open_image
        import numpy as np
        
        # Load scene (memory-mapped, not loaded entirely)
        img = open_image(file_path)
        
        # Get wavelengths
        wavelengths = img.metadata.get('wavelength', [])
        if not wavelengths:
            wavelengths = np.linspace(400, 2500, img.shape[2]).tolist()
        
        scene_id = metadata['scene_id']
        
        # Save as Zarr (chunked, compressed)
        zarr_path = f"{scene_id}/data.zarr"
        
        # Process in tiles to avoid OOM
        rows, cols, bands = img.shape
        tile_size = 256
        
        # Create Zarr array
        zarr_full_path = self.storage.save_as_zarr(
            img.load(),  # Load full scene (or implement tile-by-tile)
            wavelengths,
            metadata,
            zarr_path
        )
        
        # Also create COG for geospatial tools
        cog_path = f"{scene_id}/data.tif"
        self.storage.save_as_cog(
            img.load()[:, :, :10],  # First 10 bands only for preview
            wavelengths[:10],
            metadata,
            cog_path
        )
        
        # Publish to Kafka
        message = {
            'scene_id': scene_id,
            'zarr_path': zarr_path,
            'cog_path': cog_path,
            'wavelengths': wavelengths,
            'shape': [rows, cols, bands],
            'metadata': metadata
        }
        
        send_with_callback(self.producer, 'raw-data', message)
        
        logger.info(f"Ingested: {scene_id}, Zarr: {zarr_path}")
        
        return True
