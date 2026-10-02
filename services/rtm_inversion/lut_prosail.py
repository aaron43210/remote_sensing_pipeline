"""
PROSAIL Forward Model and LUT Utilities
=======================================

RTM Inversion Service — Offline LUT component.

Final retrieved parameters:
    Cab, Car, Cw, Cm, LAI, ALA

Fixed PROSAIL parameters:
    N = 1.5
    psoil = 0.5
    cbrown = 0.0
    hspot = 0.1
    tts = 30.0
    tto = 0.0
    psi = 0.0
    typelidf = 2
    rsoil = 1.0
"""

import time
import numpy as np
import pandas as pd
import prosail


# ============================================================
# Final RTM configuration
# ============================================================

TARGET_PARAMETERS = [
    "Cab",
    "Car",
    "Cw",
    "Cm",
    "LAI",
    "ALA"
]

N_FIXED = 1.5
PSOIL_FIXED = 0.5

CBROWN = 0.0
HSPOT = 0.1

TTS = 30.0
TTO = 0.0
PSI = 0.0

RSOIL = 1.0
TYPE_LIDF = 2

PROSAIL_WAVELENGTHS = np.arange(400, 2501)


# ============================================================
# Single-spectrum PROSAIL forward simulation
# ============================================================

def run_prosail_one(
    cab,
    car,
    cw,
    cm,
    lai,
    ala
):
    """
    Run PROSAIL for one set of six retrieved parameters.

    Returns
    -------
    numpy.ndarray
        Simulated reflectance spectrum from 400 to 2500 nm.
    """

    spectrum = prosail.run_prosail(
        n=N_FIXED,
        cab=float(cab),
        car=float(car),
        cbrown=CBROWN,
        cw=float(cw),
        cm=float(cm),
        lai=float(lai),
        lidfa=float(ala),
        hspot=HSPOT,
        tts=TTS,
        tto=TTO,
        psi=PSI,
        typelidf=TYPE_LIDF,
        rsoil=RSOIL,
        psoil=PSOIL_FIXED
    )

    return np.asarray(spectrum, dtype=np.float32)


# ============================================================
# Generate PROSAIL spectral LUT
# ============================================================

def generate_prosail_lut(parameter_samples):
    """
    Generate a PROSAIL spectral LUT from sampled RTM parameters.

    Parameters
    ----------
    parameter_samples : pandas.DataFrame
        DataFrame containing:
        Cab, Car, Cw, Cm, LAI, ALA

    Returns
    -------
    wavelengths : numpy.ndarray
        PROSAIL wavelengths from 400 to 2500 nm.

    spectral_lut : numpy.ndarray
        Simulated reflectance LUT with shape:
        (number_of_samples, number_of_wavelengths)
    """

    missing = [
        parameter
        for parameter in TARGET_PARAMETERS
        if parameter not in parameter_samples.columns
    ]

    if missing:
        raise ValueError(
            f"Missing required RTM parameters: {missing}"
        )

    n_samples = len(parameter_samples)
    n_wavelengths = len(PROSAIL_WAVELENGTHS)

    spectral_lut = np.empty(
        (n_samples, n_wavelengths),
        dtype=np.float32
    )

    start_time = time.time()

    for i, row in parameter_samples.iterrows():

        spectral_lut[i, :] = run_prosail_one(
            cab=row["Cab"],
            car=row["Car"],
            cw=row["Cw"],
            cm=row["Cm"],
            lai=row["LAI"],
            ala=row["ALA"]
        )

        if (i + 1) % 500 == 0:
            elapsed = time.time() - start_time

            print(
                f"Completed {i + 1}/{n_samples} "
                f"({(i + 1) / n_samples * 100:.1f}%) "
                f"| Time: {elapsed / 60:.2f} min"
            )

    elapsed = time.time() - start_time

    print("===================================")
    print("PROSAIL LUT generation completed!")
    print("===================================")
    print("LUT shape:", spectral_lut.shape)
    print(f"Total time: {elapsed / 60:.2f} minutes")

    return PROSAIL_WAVELENGTHS.copy(), spectral_lut
