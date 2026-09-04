# =============================================================
# OWNER: AARON
# =============================================================
"""
Dataset loaders for training the FusionLightweightNet.

Supports two modes:
    1. Benchmark mode:  Load Indian Pines dataset from datasets/ folder
    2. Production mode: Load fused .npz files from MinIO (real pipeline data)

Usage:
    from dataset import IndianPinesDataset, FusedFeatureDataset

    # Benchmark training
    ds = IndianPinesDataset(data_dir='datasets/')
    train_loader = DataLoader(ds, batch_size=64, shuffle=True)

    # Production training
    ds = FusedFeatureDataset(npz_dir='/path/to/fused/')
    train_loader = DataLoader(ds, batch_size=128, shuffle=True)
"""

import os
import logging
import numpy as np
import torch
from torch.utils.data import Dataset

logger = logging.getLogger(__name__)


class IndianPinesDataset(Dataset):
    """
    AVIRIS Indian Pines benchmark dataset for initial training.

    145×145 pixels, 200 bands (after bad band removal), 16 classes.
    Uses physics-based feature extraction to generate the 13-dim
    fused feature vector that mimics what the pipeline would produce.

    This lets you train the model before the full pipeline is running.
    """

    def __init__(self, data_dir='datasets/', transform=None):
        self.transform = transform

        # Load Indian Pines .mat file
        mat_path = os.path.join(data_dir, 'indian_pines.mat')
        gt_path  = os.path.join(data_dir, 'indian_pines_gt.mat')

        if not os.path.exists(mat_path) or not os.path.exists(gt_path):
            raise FileNotFoundError(
                f"Indian Pines dataset not found at {data_dir}. "
                f"Run: python datasets/download_aviris.py"
            )

        from scipy.io import loadmat
        img_data = loadmat(mat_path)
        gt_data  = loadmat(gt_path)

        # Find the data arrays (key names vary)
        img = None
        for key in img_data:
            if not key.startswith('__'):
                arr = img_data[key]
                if hasattr(arr, 'shape') and len(arr.shape) == 3:
                    img = arr
                    break

        gt = None
        for key in gt_data:
            if not key.startswith('__'):
                arr = gt_data[key]
                if hasattr(arr, 'shape') and len(arr.shape) == 2:
                    gt = arr
                    break

        if img is None or gt is None:
            raise ValueError("Could not find image/ground truth arrays in .mat files")

        rows, cols, bands = img.shape
        img = img.astype(np.float32)

        # Normalize reflectance to [0, 1]
        img_max = img.max()
        if img_max > 1:
            img = img / img_max

        # Extract physics-like features per pixel (simulates pipeline output)
        # This generates 13 features to match TOTAL_FEATURES_HSI
        features = self._extract_features(img, rows, cols, bands)

        # Flatten ground truth, keep only labeled pixels (class > 0)
        gt_flat = gt.flatten()
        mask    = gt_flat > 0

        self.features = features[mask]
        self.labels   = gt_flat[mask] - 1  # 0-indexed classes

        # Convert to tensors
        self.features = torch.FloatTensor(self.features)
        self.labels   = torch.LongTensor(self.labels)

        n_classes = int(self.labels.max()) + 1
        logger.info(
            "Indian Pines loaded: %d labeled pixels, %d classes, %d features",
            len(self.labels), n_classes, self.features.shape[1],
        )

    def _extract_features(self, img, rows, cols, bands):
        """
        Extract 13 physics-like features per pixel to simulate
        what the domain services would produce.

        Features (13):
            Spectral-like (6): NDVI, red_edge_approx, chl_depth, water_depth, brightness, NIR_mean
            RTM-like (4):      chlorophyll_approx, lai_approx, water_approx, structure_approx
            Mineral-like (3):  spectral_variability, max_reflectance, spectral_angle_to_mean
        """
        pixels = img.reshape(-1, bands)
        n_pixels = pixels.shape[0]
        features = np.zeros((n_pixels, 13), dtype=np.float32)

        # Band indices (approximate for AVIRIS 400-2500nm, 200 bands)
        idx_red  = int(bands * 0.2)   # ~680nm
        idx_nir  = int(bands * 0.3)   # ~800nm
        idx_swir = int(bands * 0.75)  # ~1500nm

        R_red  = pixels[:, idx_red]
        R_nir  = pixels[:, idx_nir]
        R_swir = pixels[:, idx_swir]

        # Spectral features (6)
        features[:, 0] = (R_nir - R_red) / (R_nir + R_red + 1e-8)     # NDVI
        features[:, 1] = R_nir - R_red                                   # Red edge proxy
        features[:, 2] = 1 - R_red / (np.mean(pixels[:, idx_red-5:idx_red+5], axis=1) + 1e-8)  # Chl depth
        features[:, 3] = 1 - R_swir / (np.mean(pixels[:, idx_swir-5:idx_swir+5], axis=1) + 1e-8)  # Water depth
        features[:, 4] = np.mean(pixels, axis=1)                          # Brightness
        features[:, 5] = np.mean(pixels[:, idx_nir:idx_nir+20], axis=1)   # NIR mean

        # RTM-like features (4) — approximations
        features[:, 6]  = features[:, 0] * 50 + 25        # Chlorophyll proxy (from NDVI)
        features[:, 7]  = features[:, 0] * 4 + 1          # LAI proxy (from NDVI)
        features[:, 8]  = features[:, 3]                   # Water proxy
        features[:, 9]  = np.std(pixels[:, idx_nir:], axis=1)  # Structure proxy

        # Mineral-like features (3)
        features[:, 10] = np.std(pixels, axis=1)           # Spectral variability
        features[:, 11] = np.max(pixels, axis=1)           # Max reflectance
        # Spectral angle to scene mean
        mean_spec = np.mean(pixels, axis=0)
        dot = np.dot(pixels, mean_spec)
        norms = np.linalg.norm(pixels, axis=1) * np.linalg.norm(mean_spec)
        features[:, 12] = np.arccos(np.clip(dot / (norms + 1e-8), -1, 1))

        return features

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        x = self.features[idx]
        y = self.labels[idx]
        if self.transform:
            x = self.transform(x)
        return x, y


class FusedFeatureDataset(Dataset):
    """
    Load fused feature vectors from .npz files (production data).

    Each .npz file should contain:
        'features': (n_pixels, n_features) float32
        'labels':   (n_pixels,) int64

    Usage:
        ds = FusedFeatureDataset('/path/to/fused_data/')
    """

    def __init__(self, npz_dir: str):
        all_features = []
        all_labels   = []

        for fname in sorted(os.listdir(npz_dir)):
            if not fname.endswith('.npz'):
                continue
            path = os.path.join(npz_dir, fname)
            data = np.load(path)

            if 'features' in data and 'labels' in data:
                all_features.append(data['features'])
                all_labels.append(data['labels'])
            else:
                logger.warning("Skipping %s — missing 'features' or 'labels' key", fname)

        if not all_features:
            raise FileNotFoundError(
                f"No valid .npz files found in {npz_dir}. "
                f"Each file must contain 'features' and 'labels' arrays."
            )

        self.features = torch.FloatTensor(np.vstack(all_features))
        self.labels   = torch.LongTensor(np.concatenate(all_labels))

        logger.info(
            "FusedFeatureDataset: %d pixels, %d features from %d files",
            len(self.labels), self.features.shape[1], len(all_features),
        )

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.features[idx], self.labels[idx]
