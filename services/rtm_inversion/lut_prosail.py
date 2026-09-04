# =============================================================
# OWNER: LANKAPRIYA
# =============================================================
import numpy as np
import pickle
from pathlib import Path
from scipy.spatial import cKDTree
import logging

logger = logging.getLogger(__name__)

class PROSAILLookupTable:
    """
    Pre-computed PROSAIL lookup table for fast inversion
    
    LUT Dimensions:
    - Chlorophyll (Cab): 20 values [10, 20, ..., 100]
    - Water (Cw): 15 values [0.001, 0.005, ..., 0.05]
    - LAI: 20 values [0.5, 1.0, ..., 8.0]
    - Structure (N): 10 values [1.0, 1.1, ..., 2.5]
    
    Total: 60,000 spectra pre-computed
    """
    
    def __init__(self, lut_path='lut_prosail.pkl'):
        self.lut_path = Path(lut_path)
        
        if self.lut_path.exists():
            logger.info("Loading existing LUT...")
            self.load_lut()
        else:
            logger.info("LUT not found. Generating...")
            self.generate_lut()
            self.save_lut()
    
    def generate_lut(self):
        """
        Generate lookup table by running PROSAIL across parameter grid
        One-time computation: ~5-10 minutes
        """
        try:
            import prosail
            prosail_available = True
        except ImportError:
            prosail_available = False
            logger.warning("PROSAIL not available, using simplified model")
        
        # Parameter grid
        cab_range = np.linspace(10, 100, 20)     # Chlorophyll
        cw_range = np.linspace(0.001, 0.05, 15)  # Water
        lai_range = np.linspace(0.5, 8.0, 20)    # LAI
        n_range = np.linspace(1.0, 2.5, 10)      # Structure
        
        # Create meshgrid
        cab_grid, cw_grid, lai_grid, n_grid = np.meshgrid(
            cab_range, cw_range, lai_range, n_range, indexing='ij'
        )
        
        # Flatten
        cab_flat = cab_grid.flatten()
        cw_flat = cw_grid.flatten()
        lai_flat = lai_grid.flatten()
        n_flat = n_grid.flatten()
        
        n_combinations = len(cab_flat)
        logger.info(f"Generating {n_combinations} LUT entries...")
        
        # Storage for spectra
        wavelengths = np.arange(400, 2501)  # PROSAIL default
        spectra = np.zeros((n_combinations, len(wavelengths)))
        
        # Generate spectra
        for i in range(n_combinations):
            if i % 1000 == 0:
                logger.info(f"Progress: {i}/{n_combinations}")
            
            if prosail_available:
                try:
                    spectrum = prosail.run_prosail(
                        n=n_flat[i],
                        cab=cab_flat[i],
                        car=8,  # Fixed carotenoid
                        cbrown=0,
                        cw=cw_flat[i],
                        cm=0.009,  # Fixed dry matter
                        lai=lai_flat[i],
                        lidfa=57,  # Fixed leaf angle
                        hspot=0.01,
                        tts=30,  # Sun zenith
                        tto=0,   # View zenith
                        psi=0,   # Relative azimuth
                        typelidf=2
                    )
                    spectra[i, :] = spectrum
                except:
                    # Fallback to simplified model
                    spectra[i, :] = self._simplified_forward(
                        cab_flat[i], cw_flat[i], lai_flat[i], n_flat[i], wavelengths
                    )
            else:
                spectra[i, :] = self._simplified_forward(
                    cab_flat[i], cw_flat[i], lai_flat[i], n_flat[i], wavelengths
                )
        
        # Store LUT
        self.lut = {
            'wavelengths': wavelengths,
            'parameters': np.column_stack([cab_flat, cw_flat, lai_flat, n_flat]),
            'spectra': spectra,
            'param_names': ['Cab', 'Cw', 'LAI', 'N']
        }
        
        # Build KD-Tree for fast nearest neighbor search
        self.tree = cKDTree(spectra)
        
        logger.info("LUT generation complete!")
    
    def _simplified_forward(self, cab, cw, lai, n, wavelengths):
        """Simplified vegetation model when PROSAIL unavailable"""
        spectrum = np.zeros_like(wavelengths, dtype=float)
        
        # Visible (chlorophyll absorption)
        vis_mask = wavelengths < 700
        spectrum[vis_mask] = 0.05 + 0.02 * np.exp(-cab / 30)
        
        # NIR (structure scattering)
        nir_mask = (wavelengths >= 700) & (wavelengths < 1300)
        spectrum[nir_mask] = 0.35 + 0.15 * (1 - np.exp(-lai / 3)) * n / 2
        
        # SWIR (water absorption)
        swir_mask = wavelengths >= 1300
        base_swir = 0.25 - 0.15 * cw * 20
        spectrum[swir_mask] = base_swir
        
        # Water absorption features
        for center in [1450, 1940]:
            absorption = np.exp(-((wavelengths - center) ** 2) / (2 * 50 ** 2))
            spectrum -= absorption * cw * 5
        
        return np.clip(spectrum, 0, 1)
    
    def save_lut(self):
        """Save LUT to disk"""
        with open(self.lut_path, 'wb') as f:
            pickle.dump({
                'lut': self.lut,
                'tree_data': self.tree.data,
                'tree_indices': self.tree.indices,
                'tree_indptr': self.tree.indptr
            }, f, protocol=4)
        
        logger.info(f"LUT saved to {self.lut_path}")
    
    def load_lut(self):
        """Load LUT from disk"""
        with open(self.lut_path, 'rb') as f:
            data = pickle.load(f)
        
        self.lut = data['lut']
        
        # Rebuild KD-Tree
        self.tree = cKDTree(self.lut['spectra'])
        
        logger.info(f"LUT loaded: {len(self.lut['spectra'])} entries")
    
    def invert_fast(self, measured_spectrum, wavelengths, k=5):
        """
        Fast inversion using LUT nearest neighbor search
        """
        # Interpolate measured spectrum to LUT wavelengths
        measured_interp = np.interp(
            self.lut['wavelengths'],
            wavelengths,
            measured_spectrum
        )
        
        # Normalize for spectral angle search
        measured_norm = measured_interp / (np.linalg.norm(measured_interp) + 1e-10)
        
        # Find k nearest neighbors
        distances, indices = self.tree.query(measured_norm, k=k)
        
        # Average parameters from k neighbors (weighted by inverse distance)
        weights = 1 / (distances + 1e-10)
        weights /= weights.sum()
        
        params_nn = self.lut['parameters'][indices]
        params_avg = np.average(params_nn, axis=0, weights=weights)
        
        # Uncertainty estimate (std of k neighbors)
        params_std = np.std(params_nn, axis=0)
        
        result = {
            'Chlorophyll_ug_cm2': params_avg[0],
            'Water_content': params_avg[1],
            'LAI': params_avg[2],
            'Leaf_structure': params_avg[3],
            'uncertainty': {
                'Chlorophyll': params_std[0],
                'Water': params_std[1],
                'LAI': params_std[2],
                'Structure': params_std[3]
            },
            'spectral_distance': distances[0],
            'confidence': 'HIGH' if distances[0] < 0.05 else 'MEDIUM' if distances[0] < 0.1 else 'LOW'
        }
        
        return result
    
    def invert_hybrid(self, measured_spectrum, wavelengths, use_optimization=False):
        """
        Hybrid approach: LUT for initial guess, optimization for refinement
        """
        # Fast LUT retrieval
        lut_result = self.invert_fast(measured_spectrum, wavelengths, k=3)
        
        # If low confidence, use optimization for refinement
        if use_optimization and lut_result['confidence'] == 'LOW':
            from scipy.optimize import minimize
            
            # Use LUT result as initial guess
            x0 = [
                lut_result['Chlorophyll_ug_cm2'],
                lut_result['Water_content'],
                lut_result['LAI'],
                lut_result['Leaf_structure']
            ]
            
            # Cost function
            def cost(params):
                simulated = self._simplified_forward(
                    params[0], params[1], params[2], params[3],
                    self.lut['wavelengths']
                )
                measured_interp = np.interp(
                    self.lut['wavelengths'], wavelengths, measured_spectrum
                )
                return np.linalg.norm(simulated - measured_interp)
            
            # Optimize
            bounds = [(10, 100), (0.001, 0.05), (0.5, 8.0), (1.0, 2.5)]
            result = minimize(cost, x0, bounds=bounds, method='L-BFGS-B')
            
            # Update with optimized values
            lut_result.update({
                'Chlorophyll_ug_cm2': result.x[0],
                'Water_content': result.x[1],
                'LAI': result.x[2],
                'Leaf_structure': result.x[3],
                'optimization_used': True
            })
        
        return lut_result


# Benchmark
if __name__ == "__main__":
    import time
    
    # Initialize LUT
    lut = PROSAILLookupTable()
    
    # Test inversion speed
    n_tests = 1000
    wavelengths = np.linspace(400, 2500, 200)
    
    times = []
    for _ in range(n_tests):
        # Random spectrum
        spectrum = np.random.rand(200) * 0.5 + 0.2
        
        start = time.time()
        result = lut.invert_fast(spectrum, wavelengths)
        times.append(time.time() - start)
    
    print(f"LUT Inversion Performance:")
    print(f"  Mean time: {np.mean(times)*1000:.2f} ms/pixel")
    print(f"  Throughput: {1/np.mean(times):.0f} pixels/sec")
    print(f"\nFor 512×512 scene: {512*512*np.mean(times):.1f} seconds")
