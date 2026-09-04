# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Mineral Spectral Library Management
"""
import numpy as np
import json
import os
import logging

logger = logging.getLogger(__name__)

class MineralSpectralLibrary:
    def __init__(self, library_path=None):
        self.wavelengths = None
        self.minerals = {}

        if library_path and os.path.exists(library_path):
            self.load_library(library_path)
        else:
            self._initialize_default_library()

    def _initialize_default_library(self):
        self.wavelengths = np.arange(400, 2501, 10, dtype=float)

        self.minerals["kaolinite"] = self._build_mineral_spectrum(
            base_reflectance=0.35,
            absorptions=[
                {"center": 2165, "width": 20, "depth": 0.15},
                {"center": 2205, "width": 25, "depth": 0.30},
                {"center": 1400, "width": 40, "depth": 0.05},
                {"center": 1910, "width": 30, "depth": 0.03},
            ],
            slope=-0.00003
        )
        self.minerals["illite"] = self._build_mineral_spectrum(
            base_reflectance=0.30,
            absorptions=[
                {"center": 2200, "width": 40, "depth": 0.25},
                {"center": 2350, "width": 30, "depth": 0.08},
                {"center": 1420, "width": 30, "depth": 0.04},
                {"center": 1920, "width": 25, "depth": 0.03},
            ],
            slope=-0.00004
        )
        self.minerals["muscovite"] = self._build_mineral_spectrum(
            base_reflectance=0.32,
            absorptions=[
                {"center": 2200, "width": 30, "depth": 0.28},
                {"center": 2350, "width": 25, "depth": 0.10},
                {"center": 1410, "width": 30, "depth": 0.04},
            ],
            slope=-0.00003
        )
        self.minerals["alunite"] = self._build_mineral_spectrum(
            base_reflectance=0.45,
            absorptions=[
                {"center": 2165, "width": 20, "depth": 0.20},
                {"center": 2320, "width": 25, "depth": 0.25},
                {"center": 1480, "width": 30, "depth": 0.06},
                {"center": 1770, "width": 20, "depth": 0.04},
            ],
            slope=-0.00002
        )
        self.minerals["chlorite"] = self._build_mineral_spectrum(
            base_reflectance=0.25,
            absorptions=[
                {"center": 2250, "width": 30, "depth": 0.22},
                {"center": 2320, "width": 25, "depth": 0.18},
                {"center": 1420, "width": 30, "depth": 0.05},
                {"center": 1920, "width": 25, "depth": 0.04},
            ],
            slope=-0.00005
        )
        self.minerals["calcite"] = self._build_mineral_spectrum(
            base_reflectance=0.50,
            absorptions=[
                {"center": 2340, "width": 30, "depth": 0.35},
                {"center": 2520, "width": 20, "depth": 0.15},
                {"center": 1420, "width": 30, "depth": 0.03},
            ],
            slope=0.00001
        )
        self.minerals["dolomite"] = self._build_mineral_spectrum(
            base_reflectance=0.45,
            absorptions=[
                {"center": 2320, "width": 30, "depth": 0.30},
                {"center": 2520, "width": 20, "depth": 0.12},
                {"center": 1420, "width": 30, "depth": 0.03},
            ],
            slope=0.00001
        )
        self.minerals["hematite"] = self._build_mineral_spectrum(
            base_reflectance=0.20,
            absorptions=[
                {"center": 860, "width": 60, "depth": 0.30},
                {"center": 650, "width": 40, "depth": 0.10},
                {"center": 1920, "width": 80, "depth": 0.05},
            ],
            slope=0.00003
        )
        self.minerals["goethite"] = self._build_mineral_spectrum(
            base_reflectance=0.22,
            absorptions=[
                {"center": 480, "width": 40, "depth": 0.15},
                {"center": 900, "width": 80, "depth": 0.28},
                {"center": 1920, "width": 60, "depth": 0.05},
            ],
            slope=0.00002
        )
        self.minerals["gypsum"] = self._build_mineral_spectrum(
            base_reflectance=0.55,
            absorptions=[
                {"center": 1450, "width": 30, "depth": 0.12},
                {"center": 1750, "width": 20, "depth": 0.08},
                {"center": 1940, "width": 40, "depth": 0.20},
                {"center": 2265, "width": 25, "depth": 0.05},
            ],
            slope=0.00000
        )
        self.minerals["montmorillonite"] = self._build_mineral_spectrum(
            base_reflectance=0.30,
            absorptions=[
                {"center": 2210, "width": 40, "depth": 0.20},
                {"center": 1420, "width": 50, "depth": 0.10},
                {"center": 1920, "width": 50, "depth": 0.15},
                {"center": 2350, "width": 30, "depth": 0.05},
            ],
            slope=-0.00004
        )
        self.minerals["quartz"] = self._build_mineral_spectrum(
            base_reflectance=0.55,
            absorptions=[
                {"center": 1400, "width": 30, "depth": 0.02},
                {"center": 1900, "width": 30, "depth": 0.02},
                {"center": 2250, "width": 50, "depth": 0.03},
            ],
            slope=0.00000
        )
        logger.info(f"Initialized library: {len(self.minerals)} minerals, {len(self.wavelengths)} bands")

    def _build_mineral_spectrum(self, base_reflectance, absorptions, slope=0.0):
        wvl = self.wavelengths
        spectrum = np.ones_like(wvl) * base_reflectance
        spectrum += slope * (wvl - wvl[0])

        for abs_feat in absorptions:
            center = abs_feat["center"]
            width = abs_feat["width"]
            depth = abs_feat["depth"]
            absorption = depth * np.exp(-((wvl - center) ** 2) / (2 * width ** 2))
            spectrum -= absorption

        np.random.seed(hash(str(base_reflectance + slope)) % 2**31)
        noise = np.random.normal(0, 0.005, len(wvl))
        spectrum += noise
        spectrum = np.clip(spectrum, 0.01, 0.95)
        return spectrum

    def get_mineral_names(self):
        return list(self.minerals.keys())

    def get_spectrum(self, mineral_name):
        return self.minerals.get(mineral_name, None)

    def get_all_spectra_matrix(self):
        names = sorted(self.minerals.keys())
        matrix = np.array([self.minerals[n] for n in names])
        return names, matrix

    def load_library(self, path):
        with open(path, "r") as f:
            data = json.load(f)
        self.wavelengths = np.array(data["wavelengths"])
        for name, spectrum in data["minerals"].items():
            self.minerals[name] = np.array(spectrum)
        logger.info(f"Loaded library from {path}: {len(self.minerals)} minerals")

    def save_library(self, path):
        data = {
            "wavelengths": self.wavelengths.tolist(),
            "minerals": {name: spec.tolist() for name, spec in self.minerals.items()}
        }
        with open(path, "w") as f:
            json.dump(data, f)
        logger.info(f"Saved library to {path}")
