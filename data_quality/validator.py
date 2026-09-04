# =============================================================
# OWNER: AARON
# =============================================================
import numpy as np
import logging
import zarr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class HyperspectralDataValidator:
    """
    Validate hyperspectral data before processing.
    Catches real-world issues: clouds, shadows, sensor artifacts.
    """

    def __init__(self):
        self.rules = {
            "min_reflectance": 0.0,
            "max_reflectance": 1.0,
            "max_nan_fraction": 0.05,       # Max 5% NaN pixels
            "max_saturated_fraction": 0.10,  # Max 10% saturated
            "min_snr": 50,                   # Minimum signal-to-noise ratio
            "min_bands": 50,                 # Minimum usable bands
            "max_cloud_fraction": 0.20       # Max 20% cloud cover
        }

    def validate_scene(self, cube: np.ndarray, wavelengths: list) -> dict:
        """
        Full scene validation.

        Args:
            cube: (rows, cols, bands) hyperspectral cube
            wavelengths: list of wavelengths

        Returns:
            dict with is_valid, issues, summary
        """
        issues = []
        warnings = []

        rows, cols, bands = cube.shape
        n_pixels = rows * cols

        # ── 1. Shape check ────────────────────────
        if bands < self.rules["min_bands"]:
            issues.append(f"Too few bands: {bands} < {self.rules['min_bands']}")

        if len(wavelengths) != bands:
            issues.append(
                f"Wavelength mismatch: {len(wavelengths)} wavelengths vs {bands} bands"
            )

        # ── 2. NaN check ──────────────────────────
        nan_fraction = np.sum(np.isnan(cube)) / (n_pixels * bands)
        if nan_fraction > self.rules["max_nan_fraction"]:
            issues.append(
                f"Too many NaN values: {nan_fraction*100:.1f}% "
                f"(max {self.rules['max_nan_fraction']*100:.1f}%)"
            )
        elif nan_fraction > 0.01:
            warnings.append(f"Some NaN values: {nan_fraction*100:.2f}%")

        # ── 3. Range check ────────────────────────
        valid_mask = ~np.isnan(cube)
        if valid_mask.any():
            min_val = np.min(cube[valid_mask])
            max_val = np.max(cube[valid_mask])

            if min_val < self.rules["min_reflectance"] - 0.1:
                issues.append(f"Negative reflectance detected: min={min_val:.4f}")

            if max_val > self.rules["max_reflectance"] + 0.1:
                issues.append(f"Reflectance > 1 detected: max={max_val:.4f}")

        # ── 4. Saturation check ───────────────────
        saturated_fraction = np.sum(cube >= 0.99) / (n_pixels * bands)
        if saturated_fraction > self.rules["max_saturated_fraction"]:
            issues.append(
                f"Too many saturated pixels: {saturated_fraction*100:.1f}%"
            )
        elif saturated_fraction > 0.05:
            warnings.append(
                f"Some saturation: {saturated_fraction*100:.2f}%"
            )

        # ── 5. SNR estimate ───────────────────────
        estimated_snr = self._estimate_snr(cube)
        if estimated_snr < self.rules["min_snr"]:
            issues.append(
                f"Low SNR: {estimated_snr:.1f} "
                f"(min {self.rules['min_snr']})"
            )

        # ── 6. Cloud detection (simple threshold) ─
        cloud_fraction = self._detect_clouds(cube, wavelengths)
        if cloud_fraction > self.rules["max_cloud_fraction"]:
            issues.append(
                f"Too much cloud cover: {cloud_fraction*100:.1f}%"
            )
        elif cloud_fraction > 0.10:
            warnings.append(
                f"Moderate cloud cover: {cloud_fraction*100:.2f}%"
            )

        # ── 7. Spectral consistency ───────────────
        spectral_ok = self._check_spectral_consistency(cube, wavelengths)
        if not spectral_ok:
            warnings.append("Spectral consistency issues detected")

        is_valid = len(issues) == 0

        result = {
            "is_valid": is_valid,
            "issues": issues,
            "warnings": warnings,
            "summary": {
                "shape": cube.shape,
                "nan_fraction": float(nan_fraction),
                "saturated_fraction": float(saturated_fraction),
                "snr_estimate": float(estimated_snr),
                "cloud_fraction": float(cloud_fraction),
                "n_issues": len(issues),
                "n_warnings": len(warnings)
            }
        }

        if is_valid:
            logger.info(f"Scene validation PASSED: {result['summary']}")
        else:
            logger.error(f"Scene validation FAILED: {issues}")

        return result

    def validate_training_batch(self, batch_path: str) -> dict:
        """Validate a training batch from Zarr."""
        z = zarr.open(batch_path, mode="r")
        cube = z[:]
        wavelengths = z.attrs.get("wavelengths", [])
        return self.validate_scene(cube, wavelengths)

    def _estimate_snr(self, cube: np.ndarray) -> float:
        """Estimate SNR from homogeneous regions."""
        rows, cols, bands = cube.shape
        patch_size = min(10, rows // 5, cols // 5)

        if patch_size < 3:
            return 100.0  # Can't estimate, assume OK

        center_r = rows // 2
        center_c = cols // 2
        patch = cube[
            center_r - patch_size:center_r + patch_size,
            center_c - patch_size:center_c + patch_size,
            :
        ]

        mean_signal = np.nanmean(patch)
        noise = np.nanstd(np.diff(patch, axis=2))

        if noise < 1e-10:
            return 1000.0

        return float(mean_signal / noise)

    def _detect_clouds(self, cube: np.ndarray, wavelengths: list) -> float:
        """
        Simple cloud detection using high reflectance threshold.
        Clouds typically have high reflectance across all bands.
        """
        if len(wavelengths) == 0:
            return 0.0

        # Use visible bands (400-700nm)
        wl = np.array(wavelengths)
        vis_mask = (wl >= 400) & (wl <= 700)

        if not vis_mask.any():
            return 0.0

        vis_bands = cube[:, :, vis_mask]
        mean_vis = np.nanmean(vis_bands, axis=2)

        # Clouds: very high visible reflectance > 0.7
        cloud_mask = mean_vis > 0.7
        return float(np.sum(cloud_mask) / cloud_mask.size)

    def _check_spectral_consistency(self, cube: np.ndarray, wavelengths: list) -> bool:
        """Check if spectra follow expected physical patterns."""
        if len(wavelengths) == 0:
            return True

        wl = np.array(wavelengths)
        n_pixels = cube.shape[0] * cube.shape[1]
        sample_size = min(100, n_pixels)

        # Random sample pixels
        flat = cube.reshape(-1, cube.shape[2])
        indices = np.random.choice(n_pixels, sample_size, replace=False)
        samples = flat[indices]

        # Check: NIR should generally be >= VIS for vegetated scenes
        vis_idx = np.argmin(np.abs(wl - 660)) if 660 in wl else 0
        nir_idx = np.argmin(np.abs(wl - 800)) if 800 in wl else min(60, cube.shape[2]-1)

        nir_gt_vis = samples[:, nir_idx] >= samples[:, vis_idx]
        consistency_rate = np.mean(nir_gt_vis)

        # At least 50% of pixels should have NIR > red (unless all urban)
        return consistency_rate > 0.3
