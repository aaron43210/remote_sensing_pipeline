"""simulate() from services/mineral_analysis/rtm_inversion/synth.py, verbatim."""
import numpy as np

DEFAULT_N_SAMPLES = 60_000
# Probability of mixing k endmembers (index 0 -> k=1 pure, ... index 3 -> k=4).
MIX_K_PROBS = (0.30, 0.35, 0.25, 0.10)


def simulate(endmembers, n_samples, rng, k_probs=MIX_K_PROBS,
             brightness=(0.6, 1.2), noise=(0.003, 0.015), refl_clip=(0.001, 1.5)):
    """Return (spectra (n, n_bands), abundances (n, n_em)) from the mixing model."""
    n_em, n_bands = endmembers.shape
    ks = rng.choice(np.arange(1, len(k_probs) + 1), size=n_samples, p=np.asarray(k_probs))

    abundances = np.zeros((n_samples, n_em), dtype=np.float64)
    for i, k in enumerate(ks):
        members = rng.choice(n_em, size=int(k), replace=False)
        abundances[i, members] = rng.dirichlet(np.ones(int(k)))

    spectra = abundances @ endmembers                      # linear mixing
    spectra *= rng.uniform(*brightness, size=(n_samples, 1))   # albedo nuisance
    sigma = rng.uniform(*noise, size=(n_samples, 1))
    spectra += rng.normal(0.0, 1.0, spectra.shape) * sigma     # additive noise
    np.clip(spectra, *refl_clip, out=spectra)
    return spectra.astype(np.float32), abundances.astype(np.float32)
