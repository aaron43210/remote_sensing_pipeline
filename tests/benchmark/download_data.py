# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Download AVIRIS Indian Pines dataset for benchmarking.

Dataset: AVIRIS sensor, Northwestern Indiana, June 1992
- 145 x 145 pixels
- 220 spectral bands (400-2500nm)
- 16 land cover classes
- 10,249 labeled pixels

Source: http://www.ehu.eus/ccwintco/index.php/
       Hyperspectral_Remote_Sensing_Scenes

Reference:
- Baumgardner et al. (1985): "Properties of Indiana soils."
- Goetz et al. (1985): AVIRIS initial results.
"""

import os
import logging
import numpy as np
from scipy.io import loadmat
import urllib.request

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
INDIAN_PINES_URLS = {
    'image': 'http://www.ehu.eus/ccwintco/uploads/6/67/Indian_pines_corrected.mat',
    'gt': 'http://www.ehu.eus/ccwintco/uploads/c/c4/Indian_pines_gt.mat'
}

CLASS_NAMES = {
    0: 'Unknown',
    1: 'Alfalfa',
    2: 'Corn-notill',
    3: 'Corn-mintill',
    4: 'Corn',
    5: 'Grass-pasture',
    6: 'Grass-trees',
    7: 'Grass-pasture-mowed',
    8: 'Hay-windrowed',
    9: 'Oats',
    10: 'Soybeans-notill',
    11: 'Soybeans-mintill',
    12: 'Soybeans-clean',
    13: 'Wheat',
    14: 'Woods',
    15: 'Buildings-Grass-Trees-Drives',
    16: 'Stone-Steel-Towers'
}


def download_indian_pines(force=False):
    """Download AVIRIS Indian Pines dataset."""
    os.makedirs(DATA_DIR, exist_ok=True)

    for key, url in INDIAN_PINES_URLS.items():
        filename = url.split('/')[-1]
        filepath = os.path.join(DATA_DIR, filename)

        if os.path.exists(filepath) and not force:
            logger.info(f"Already exists: {filename}")
            continue

        logger.info(f"Downloading {filename}...")
        urllib.request.urlretrieve(url, filepath)
        logger.info(f"Downloaded: {filepath}")

    return load_data()


def load_data():
    """Load Indian Pines data as numpy arrays."""
    image_path = os.path.join(DATA_DIR, 'Indian_pines_corrected.mat')
    gt_path = os.path.join(DATA_DIR, 'Indian_pines_gt.mat')

    if not os.path.exists(image_path):
        return download_indian_pines()

    # Load MATLAB files
    image_mat = loadmat(image_path)
    gt_mat = loadmat(gt_path)

    # Extract arrays (key name varies by source)
    cube = image_mat.get('indian_pines_corrected',
                         image_mat.get('image',
                         list(image_mat.values())[-2]))
    ground_truth = gt_mat.get('indian_pines_gt',
                              gt_mat.get('gt',
                              list(gt_mat.values())[-2]))

    # AVIRIS wavelengths (220 bands, 400-2500nm approx)
    wavelengths = np.linspace(400, 2500, cube.shape[2])

    logger.info(
        f"Loaded Indian Pines: "
        f"cube={cube.shape}, gt={ground_truth.shape}, "
        f"classes={len(np.unique(ground_truth))-1}"
    )

    return {
        'cube': cube.astype(np.float64),
        'ground_truth': ground_truth,
        'wavelengths': wavelengths,
        'class_names': CLASS_NAMES,
        'n_classes': len(CLASS_NAMES) - 1
    }
