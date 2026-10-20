"""
The "normal method" for agriculture: PROSAIL look-up-table inversion.

Same approach as services/rtm_inversion/lut_prosail.py (k nearest LUT spectra,
inverse-distance-weighted mean of their parameters, std of the k as
uncertainty), with two corrections:
  * the LUT holds all six parameters of the training CSV, simulated with the
    PROSAIL settings that reproduce that CSV (spec.PROSAIL_FIXED);
  * the KD-tree is queried in the same space it was built in (the original
    built it on raw spectra and queried with unit-normalised ones).
"""

import numpy as np
from scipy.spatial import cKDTree

from .. import spec


def _band_rows():
    return (np.asarray(spec.AGRI_BANDS_NM) - 400).astype(int)   # PROSAIL is 400..2500 nm, 1 nm


def sample_params(n, rng):
    """Uniform parameter draws over spec.AGRI_RANGES -> (n, 6)."""
    lo = np.array([spec.AGRI_RANGES[p][0] for p in spec.AGRI_PARAMS])
    hi = np.array([spec.AGRI_RANGES[p][1] for p in spec.AGRI_PARAMS])
    return lo + rng.random((n, len(lo))) * (hi - lo)


def simulate(params, fixed=None, rows=None):
    """PROSAIL canopy reflectance at spec.AGRI_BANDS_NM for (n, 6) parameter rows."""
    import prosail

    fx = dict(spec.PROSAIL_FIXED, **(fixed or {}))
    rows = _band_rows() if rows is None else rows
    out = np.empty((len(params), len(rows)), dtype=np.float32)
    for i, (cab, car, cw, cm, lai, ala) in enumerate(params):
        out[i] = prosail.run_prosail(
            n=fx["n"], cab=cab, car=car, cbrown=fx["cbrown"], cw=cw, cm=cm, lai=lai,
            lidfa=ala, hspot=fx["hspot"], tts=fx["tts"], tto=fx["tto"], psi=fx["psi"],
            typelidf=fx["typelidf"], rsoil=fx["rsoil"], psoil=fx["psoil"],
            factor=fx["factor"], prospect_version=fx["prospect_version"])[rows]
    return out


class LUTInverter:
    """k-NN inversion over a (spectra, params) table."""

    def __init__(self, spectra, params, k=spec.LUT_K):
        self.spectra = np.asarray(spectra, dtype=np.float64)
        self.params = np.asarray(params, dtype=np.float64)
        self.k = k
        self.tree = cKDTree(self.spectra)

    def invert(self, spectra, exclude_self=False):
        """
        (n, bands) -> dict(params (n, 6), std (n, 6), distance (n,)).
        exclude_self: drop the first neighbour (for labelling the LUT's own rows).
        """
        q = np.atleast_2d(np.asarray(spectra, dtype=np.float64))
        k = self.k + (1 if exclude_self else 0)
        dist, idx = self.tree.query(q, k=k, workers=-1)
        if exclude_self:
            dist, idx = dist[:, 1:], idx[:, 1:]
        w = 1.0 / (dist + 1e-10)
        w /= w.sum(axis=1, keepdims=True)
        p = self.params[idx]                                    # (n, k, 6)
        mean = np.einsum("nk,nkp->np", w, p)
        return {"params": mean, "std": p.std(axis=1), "distance": dist[:, 0]}

    def save(self, path):
        np.savez_compressed(path, spectra=self.spectra.astype(np.float32),
                            params=self.params.astype(np.float32), k=self.k)

    @classmethod
    def load(cls, path):
        with np.load(path) as f:
            return cls(f["spectra"], f["params"], int(f["k"]))
