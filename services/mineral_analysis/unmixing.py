# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Linear Spectral Unmixing
"""
import numpy as np
from scipy.optimize import nnls, minimize
import logging

logger = logging.getLogger(__name__)

class LinearSpectralUnmixer:
    def __init__(self, method="nnls"):
        self.method = method

    def unmix_pixel(self, spectrum, endmember_matrix):
        y = np.asarray(spectrum, dtype=float)
        E = np.asarray(endmember_matrix, dtype=float)

        if self.method == "fcls":
            abundances = self._fcls_unmix(y, E)
        else:
            abundances = self._nnls_unmix(y, E)

        reconstruction = E @ abundances
        rmse = float(np.sqrt(np.mean((y - reconstruction) ** 2)))
        return abundances, reconstruction, rmse

    def unmix_scene(self, cube, endmember_matrix, endmember_names):
        rows, cols, bands = cube.shape
        abundance_maps = {name: np.zeros((rows, cols)) for name in endmember_names}
        error_map = np.zeros((rows, cols))

        total = rows * cols
        processed = 0

        for i in range(rows):
            for j in range(cols):
                pixel = cube[i, j, :]
                if np.any(np.isnan(pixel)) or np.all(pixel == 0):
                    continue

                abundances, _, error = self.unmix_pixel(pixel, endmember_matrix)
                
                for k, name in enumerate(endmember_names):
                    abundance_maps[name][i, j] = abundances[k]
                error_map[i, j] = error

                processed += 1
                if processed % 10000 == 0:
                    logger.info(f"Unmixing progress: {processed}/{total}")

        return abundance_maps, error_map

    def _nnls_unmix(self, y, E):
        abundances, _ = nnls(E, y)
        return abundances

    def _fcls_unmix(self, y, E):
        n_endmembers = E.shape[1]
        def cost(a):
            res = E @ a - y
            return 0.5 * np.dot(res, res)
        def gradient(a):
            return E.T @ (E @ a - y)
        
        constraints = [{"type": "eq", "fun": lambda a: np.sum(a) - 1.0}]
        bounds = [(0, 1) for _ in range(n_endmembers)]
        a0 = np.ones(n_endmembers) / n_endmembers

        try:
            result = minimize(cost, a0, jac=gradient, bounds=bounds, constraints=constraints, method="SLSQP", options={"maxiter": 100, "ftol": 1e-10})
            if result.success: return result.x
            return self._nnls_unmix(y, E)
        except Exception:
            return self._nnls_unmix(y, E)
