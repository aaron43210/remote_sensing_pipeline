# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
import numpy as np
from scipy.signal import savgol_filter

def first_derivative(spectrum, wavelengths, window=7, poly=2):
    delta = np.mean(np.diff(wavelengths))
    return savgol_filter(spectrum, window_length=window, polyorder=poly,
                         deriv=1, delta=delta, mode='nearest')

def second_derivative(spectrum, wavelengths, window=7, poly=2):
    delta = np.mean(np.diff(wavelengths))
    return savgol_filter(spectrum, window_length=window, polyorder=poly,
                         deriv=2, delta=delta, mode='nearest')

def red_edge_position(spectrum, wavelengths):
    mask = (wavelengths >= 680) & (wavelengths <= 750)
    wl = wavelengths[mask]
    spec = spectrum[mask]
    deriv = np.gradient(spec, wl)
    idx = np.argmax(deriv)
    return wl[idx], deriv[idx]

def continuum_removal(spectrum, wavelengths):
    # Simple convex hull upper-bound
    from scipy.spatial import ConvexHull
    points = np.column_stack([wavelengths, spectrum])
    hull = ConvexHull(points)
    upper = hull.vertices[hull.vertices[:,0].argsort()]
    # Interpolate continuum
    continuum = np.interp(wavelengths, wavelengths[upper], spectrum[upper])
    cr = spectrum / (continuum + 1e-10)
    return cr

def absorption_depth(spectrum, wavelengths, center=680, shoulder_left=650, shoulder_right=710):
    idx_c = np.argmin(np.abs(wavelengths - center))
    idx_l = np.argmin(np.abs(wavelengths - shoulder_left))
    idx_r = np.argmin(np.abs(wavelengths - shoulder_right))
    # Linear continuum between shoulders
    cont = np.interp(center, [wavelengths[idx_l], wavelengths[idx_r]],
                     [spectrum[idx_l], spectrum[idx_r]])
    depth = 1 - spectrum[idx_c] / (cont + 1e-10)
    return max(0, depth)
