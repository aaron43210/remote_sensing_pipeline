# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
import urllib.request
import os
from spectral import open_image
import numpy as np

class DatasetManager:
    """
    Manage hyperspectral datasets for testing and validation
    """
    
    def __init__(self, data_dir='./data'):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
    
    def download_indian_pines(self):
        """
        Download AVIRIS Indian Pines benchmark dataset
        145×145 pixels, 220 bands
        """
        base_url = "http://www.ehu.eus/ccwintco/uploads"
        
        files = {
            'image': ('6/67/Indian_pines_corrected.mat', 'indian_pines.mat'),
            'ground_truth': ('c/c4/Indian_pines_gt.mat', 'indian_pines_gt.mat')
        }
        
        for key, (url_path, filename) in files.items():
            local_path = os.path.join(self.data_dir, filename)
            
            if not os.path.exists(local_path):
                print(f"Downloading {filename}...")
                urllib.request.urlretrieve(
                    f"{base_url}/{url_path}",
                    local_path
                )
                print(f"✓ Downloaded {filename}")
        
        return self._load_indian_pines()
    
    def _load_indian_pines(self):
        """Load and convert MATLAB format to numpy"""
        from scipy.io import loadmat
        
        data_path = os.path.join(self.data_dir, 'indian_pines.mat')
        gt_path = os.path.join(self.data_dir, 'indian_pines_gt.mat')
        
        # Load data
        data = loadmat(data_path)['indian_pines_corrected']
        ground_truth = loadmat(gt_path)['indian_pines_gt']
        
        # Wavelengths for AVIRIS (400-2500nm, 220 bands)
        wavelengths = np.linspace(400, 2500, 220)
        
        return {
            'cube': data,
            'ground_truth': ground_truth,
            'wavelengths': wavelengths,
            'metadata': {
                'sensor': 'AVIRIS',
                'shape': data.shape,
                'location': 'Indiana, USA',
                'date': '1992-06-12'
            }
        }
    
    def download_sentinel2_sample(self):
        """
        Download Sentinel-2 L2A sample scene
        Uses sentinelsat API (requires account)
        """
        try:
            from sentinelsat import SentinelAPI, read_geojson, geojson_to_wkt
            
            # Credentials (free registration at scihub.copernicus.eu)
            username = os.getenv('COPERNICUS_USERNAME')
            password = os.getenv('COPERNICUS_PASSWORD')
            if not username or not password:
                raise ValueError("COPERNICUS_USERNAME and COPERNICUS_PASSWORD environment variables must be set.")
            api = SentinelAPI(username, password, 'https://scihub.copernicus.eu/dhus')
            
            # Define AOI (example: San Francisco Bay Area)
            footprint = geojson_to_wkt(read_geojson('aoi.geojson'))
            
            # Search for scenes
            products = api.query(
                footprint,
                date=('20231201', '20231210'),
                platformname='Sentinel-2',
                processinglevel='Level-2A',
                cloudcoverpercentage=(0, 10)
            )
            
            # Download first result
            api.download_all(products)
            
        except ImportError:
            print("Install: pip install sentinelsat")
            print("Or download manually from: https://scihub.copernicus.eu/")
    
    def create_synthetic_scene(self, shape=(512, 512, 200)):
        """
        Generate synthetic hyperspectral scene for testing
        Simulates vegetation, soil, water classes
        """
        rows, cols, bands = shape
        wavelengths = np.linspace(400, 2500, bands)
        
        cube = np.zeros((rows, cols, bands))
        
        # Simulate different land cover types
        for i in range(rows):
            for j in range(cols):
                # Create spatial patterns
                if i < rows // 3:
                    # Vegetation (high NIR)
                    cube[i, j, :] = self._vegetation_spectrum(wavelengths)
                elif i < 2 * rows // 3:
                    # Soil (red-brown)
                    cube[i, j, :] = self._soil_spectrum(wavelengths)
                else:
                    # Water (low reflectance, absorption)
                    cube[i, j, :] = self._water_spectrum(wavelengths)
                
                # Add noise
                cube[i, j, :] += np.random.normal(0, 0.02, bands)
        
        return {
            'cube': np.clip(cube, 0, 1),
            'wavelengths': wavelengths,
            'metadata': {
                'sensor': 'Synthetic',
                'shape': shape,
                'classes': ['vegetation', 'soil', 'water']
            }
        }
    
    def _vegetation_spectrum(self, wavelengths):
        """Typical vegetation reflectance"""
        spectrum = np.zeros_like(wavelengths)
        
        # Chlorophyll absorption (visible)
        spectrum[wavelengths < 700] = 0.05 + 0.02 * np.random.rand()
        
        # High NIR reflectance
        spectrum[(wavelengths >= 700) & (wavelengths < 1300)] = 0.45 + 0.1 * np.random.rand()
        
        # Water absorption (SWIR)
        spectrum[wavelengths >= 1300] = 0.3 + 0.1 * np.random.rand()
        spectrum[np.abs(wavelengths - 1450) < 50] *= 0.7  # Water absorption
        spectrum[np.abs(wavelengths - 1940) < 50] *= 0.5  # Water absorption
        
        return spectrum
    
    def _soil_spectrum(self, wavelengths):
        """Typical soil reflectance"""
        # Linear increase with wavelength
        spectrum = 0.1 + 0.3 * (wavelengths - 400) / (2500 - 400)
        spectrum += np.random.normal(0, 0.02, len(wavelengths))
        return np.clip(spectrum, 0, 1)
    
    def _water_spectrum(self, wavelengths):
        """Typical water reflectance"""
        spectrum = np.ones_like(wavelengths) * 0.05
        # Strong absorption beyond 700nm
        spectrum[wavelengths > 700] *= 0.01
        return spectrum


# Usage
if __name__ == "__main__":
    dm = DatasetManager()
    
    # Download benchmark dataset
    indian_pines = dm.download_indian_pines()
    print(f"Indian Pines shape: {indian_pines['cube'].shape}")
    
    # Create synthetic scene for testing
    synthetic = dm.create_synthetic_scene()
    print(f"Synthetic scene shape: {synthetic['cube'].shape}")
