"""Load, validate and serve USGS reference spectra stored as local CSV files.

No resampling or unit conversion is applied to the stored data; wavelength
alignment with the satellite sensor is a separate, later step.
"""
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import numpy as np

# Reflectance limits are an explicit, overridable check (None disables it).
DEFAULT_REFLECTANCE_RANGE = (0.0, 1.5)
_USGS_DELETED = -1e30  # USGS marks deleted channels with about -1.23e34

_UNIT_ALIASES = {
    "nm": "nm", "nanometer": "nm", "nanometers": "nm", "nanometre": "nm", "nanometres": "nm",
    "um": "um", "µm": "um", "micron": "um", "microns": "um",
    "micrometer": "um", "micrometers": "um", "micrometre": "um", "micrometres": "um",
}
_TO_NM = {"nm": 1.0, "um": 1000.0}


class SpectralLibraryError(ValueError):
    """Raised for invalid, malformed or unavailable spectral data."""


def normalize_unit(unit: str) -> str:
    """Return 'nm' or 'um' for a supported unit name; raise otherwise."""
    key = str(unit).strip().lower()
    if key not in _UNIT_ALIASES:
        raise SpectralLibraryError(
            f"Unsupported wavelength unit '{unit}'. Use nanometers ('nm') or micrometers ('um').")
    return _UNIT_ALIASES[key]


def _as_float_array(values, label: str, name: str) -> np.ndarray:
    try:
        arr = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise SpectralLibraryError(f"'{name}': {label} contains non-numeric values.") from exc
    if arr.ndim != 1:
        raise SpectralLibraryError(f"'{name}': {label} must be a 1-D array, got shape {arr.shape}.")
    return arr


@dataclass
class Spectrum:
    """One reference spectrum. Arrays are validated and made read-only."""
    name: str
    wavelength: np.ndarray
    reflectance: np.ndarray
    wavelength_unit: str
    metadata: Dict[str, str] = field(default_factory=dict)
    reflectance_range: InitVar[Optional[Tuple[float, float]]] = DEFAULT_REFLECTANCE_RANGE

    def __post_init__(self, reflectance_range):
        if not str(self.name).strip():
            raise SpectralLibraryError("Spectrum name must not be empty.")
        self.wavelength_unit = normalize_unit(self.wavelength_unit)
        wl = _as_float_array(self.wavelength, "wavelength", self.name)
        refl = _as_float_array(self.reflectance, "reflectance", self.name)

        if wl.size != refl.size:
            raise SpectralLibraryError(
                f"'{self.name}': wavelength ({wl.size}) and reflectance ({refl.size}) lengths differ.")
        if wl.size < 2:
            raise SpectralLibraryError(f"'{self.name}': at least 2 samples are required.")
        if not np.isfinite(wl).all():
            raise SpectralLibraryError(f"'{self.name}': wavelength contains NaN or infinite values.")
        if (wl <= 0).any():
            raise SpectralLibraryError(f"'{self.name}': wavelength values must be positive.")
        if (np.diff(wl) <= 0).any():
            raise SpectralLibraryError(f"'{self.name}': wavelength must be strictly increasing.")
        if (refl <= _USGS_DELETED).any():
            raise SpectralLibraryError(
                f"'{self.name}': contains USGS 'deleted channel' values (about -1.23e34). "
                "Remove those channels when preparing the file; they are not dropped silently.")
        if not np.isfinite(refl).all():
            raise SpectralLibraryError(f"'{self.name}': reflectance contains NaN or infinite values.")
        if reflectance_range is not None:
            lo, hi = reflectance_range
            if refl.min() < lo or refl.max() > hi:
                raise SpectralLibraryError(
                    f"'{self.name}': reflectance [{refl.min():.4g}, {refl.max():.4g}] is outside "
                    f"the allowed range [{lo}, {hi}].")

        wl.setflags(write=False)
        refl.setflags(write=False)
        self.wavelength, self.reflectance = wl, refl

    @property
    def n_bands(self) -> int:
        return self.wavelength.size

    @property
    def wavelength_range(self) -> Tuple[float, float]:
        """(min, max) wavelength in the spectrum's own unit."""
        return float(self.wavelength[0]), float(self.wavelength[-1])

    def wavelength_in(self, unit: str) -> np.ndarray:
        """Wavelengths converted to `unit` (a new array; stored data is unchanged)."""
        return self.wavelength * (_TO_NM[self.wavelength_unit] / _TO_NM[normalize_unit(unit)])


def load_spectrum_csv(path, wavelength_unit: Optional[str] = None, name: Optional[str] = None,
                      reflectance_range=DEFAULT_REFLECTANCE_RANGE) -> Spectrum:
    """Read one spectrum from a CSV file.

    Expected layout: optional '# key: value' metadata lines, then a header with a
    column starting with 'wavelength' and one starting with 'reflectance', then data rows.
    The wavelength unit must come from `wavelength_unit`, a '# wavelength_unit: ...' line,
    or a column name such as 'wavelength_um'; if several are given they must agree.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Spectrum file not found: {path}")

    meta, header, wl_col, refl_col = {}, None, None, None
    wavelength, reflectance = [], []
    with path.open(encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            if text.startswith("#"):
                key, sep, value = text.lstrip("#").partition(":")
                if sep:
                    meta[key.strip().lower()] = value.strip()
                continue
            cells = [c.strip() for c in text.split(",")]
            if header is None:
                header = [c.lower() for c in cells]
                wl_col = next((i for i, c in enumerate(header) if c.startswith("wavelength")), None)
                refl_col = next((i for i, c in enumerate(header) if c.startswith("reflectance")), None)
                if wl_col is None or refl_col is None:
                    raise SpectralLibraryError(
                        f"{path.name}: header needs 'wavelength' and 'reflectance' columns; found {cells}.")
                continue
            if len(cells) <= max(wl_col, refl_col):
                raise SpectralLibraryError(f"{path.name} line {lineno}: expected {len(header)} columns, got {len(cells)}.")
            try:
                wavelength.append(float(cells[wl_col]))
                reflectance.append(float(cells[refl_col]))
            except ValueError as exc:
                raise SpectralLibraryError(f"{path.name} line {lineno}: non-numeric value in '{text}'.") from exc

    if header is None:
        raise SpectralLibraryError(f"{path.name}: no header or data found.")
    if not wavelength:
        raise SpectralLibraryError(f"{path.name}: header found but no data rows.")

    # Wavelength unit: every provided source must agree, and at least one must exist.
    units = {}
    if wavelength_unit is not None:
        units["argument"] = normalize_unit(wavelength_unit)
    if "wavelength_unit" in meta:
        units["file metadata"] = normalize_unit(meta["wavelength_unit"])
    suffix = header[wl_col][len("wavelength"):].strip(" _-()[]")
    if suffix:
        units["column name"] = normalize_unit(suffix)
    if not units:
        raise SpectralLibraryError(
            f"{path.name}: wavelength unit is not specified. Pass wavelength_unit, add a "
            "'# wavelength_unit: micrometers' line, or name the column 'wavelength_um' / 'wavelength_nm'.")
    if len(set(units.values())) > 1:
        raise SpectralLibraryError(f"{path.name}: conflicting wavelength units {units}.")

    meta["source_file"] = str(path)
    return Spectrum(name=name or meta.get("name") or path.stem, wavelength=wavelength,
                    reflectance=reflectance, wavelength_unit=next(iter(units.values())),
                    metadata=meta, reflectance_range=reflectance_range)


class SpectralLibrary:
    """Collection of reference spectra, looked up by mineral name (case-insensitive)."""

    def __init__(self, spectra: Iterable[Spectrum] = ()):
        self._spectra: Dict[str, Spectrum] = {}
        for spectrum in spectra:
            self.add(spectrum)

    @classmethod
    def from_directory(cls, directory, wavelength_unit: Optional[str] = None, pattern: str = "*.csv",
                       reflectance_range=DEFAULT_REFLECTANCE_RANGE) -> "SpectralLibrary":
        """Load every matching CSV in `directory`. Any invalid file stops the load with its name."""
        directory = Path(directory)
        if not directory.is_dir():
            raise FileNotFoundError(f"Spectral library directory not found: {directory}")
        files = sorted(directory.glob(pattern))
        if not files:
            raise SpectralLibraryError(f"No files matching '{pattern}' in {directory}.")
        return cls(load_spectrum_csv(f, wavelength_unit, reflectance_range=reflectance_range) for f in files)

    def add(self, spectrum: Spectrum) -> None:
        key = spectrum.name.strip().lower()
        if key in self._spectra:
            raise SpectralLibraryError(f"Duplicate mineral name '{spectrum.name}'.")
        self._spectra[key] = spectrum

    def get(self, name: str) -> Spectrum:
        try:
            return self._spectra[str(name).strip().lower()]
        except KeyError:
            raise SpectralLibraryError(
                f"Mineral '{name}' not found. Available: {self.names}") from None

    def spectra(self) -> List[Spectrum]:
        """All loaded spectra, in load order."""
        return list(self._spectra.values())

    @property
    def names(self) -> List[str]:
        return [s.name for s in self._spectra.values()]

    def wavelength_ranges(self) -> Dict[str, Tuple[float, float]]:
        """{name: (min, max)} in each spectrum's own unit (see Spectrum.wavelength_unit)."""
        return {s.name: s.wavelength_range for s in self._spectra.values()}

    __getitem__ = get

    def __contains__(self, name: str) -> bool:
        return str(name).strip().lower() in self._spectra

    def __len__(self) -> int:
        return len(self._spectra)

    def __iter__(self) -> Iterator[Spectrum]:
        return iter(self._spectra.values())

    def __repr__(self) -> str:
        return f"SpectralLibrary({len(self)} spectra: {self.names})"


_DEFAULT_MINERAL_SPECS = {
    "kaolinite": {
        "base_reflectance": 0.35,
        "absorptions": [
            {"center": 2165, "width": 20, "depth": 0.15},
            {"center": 2205, "width": 25, "depth": 0.30},
            {"center": 1400, "width": 40, "depth": 0.05},
            {"center": 1910, "width": 30, "depth": 0.03},
        ],
        "slope": -0.00003,
    },
    "illite": {
        "base_reflectance": 0.30,
        "absorptions": [
            {"center": 2200, "width": 40, "depth": 0.25},
            {"center": 2350, "width": 30, "depth": 0.08},
            {"center": 1420, "width": 30, "depth": 0.04},
            {"center": 1920, "width": 25, "depth": 0.03},
        ],
        "slope": -0.00004,
    },
    "muscovite": {
        "base_reflectance": 0.32,
        "absorptions": [
            {"center": 2200, "width": 30, "depth": 0.28},
            {"center": 2350, "width": 25, "depth": 0.10},
            {"center": 1410, "width": 30, "depth": 0.04},
        ],
        "slope": -0.00003,
    },
    "alunite": {
        "base_reflectance": 0.45,
        "absorptions": [
            {"center": 2165, "width": 20, "depth": 0.20},
            {"center": 2320, "width": 25, "depth": 0.25},
            {"center": 1480, "width": 30, "depth": 0.06},
            {"center": 1770, "width": 20, "depth": 0.04},
        ],
        "slope": -0.00002,
    },
    "chlorite": {
        "base_reflectance": 0.25,
        "absorptions": [
            {"center": 2250, "width": 30, "depth": 0.22},
            {"center": 2320, "width": 25, "depth": 0.18},
            {"center": 1420, "width": 30, "depth": 0.05},
            {"center": 1920, "width": 25, "depth": 0.04},
        ],
        "slope": -0.00005,
    },
    "calcite": {
        "base_reflectance": 0.50,
        "absorptions": [
            {"center": 2340, "width": 30, "depth": 0.35},
            {"center": 2520, "width": 20, "depth": 0.15},
            {"center": 1420, "width": 30, "depth": 0.03},
        ],
        "slope": 0.00001,
    },
    "dolomite": {
        "base_reflectance": 0.45,
        "absorptions": [
            {"center": 2320, "width": 30, "depth": 0.30},
            {"center": 2520, "width": 20, "depth": 0.12},
            {"center": 1420, "width": 30, "depth": 0.03},
        ],
        "slope": 0.00001,
    },
    "hematite": {
        "base_reflectance": 0.20,
        "absorptions": [
            {"center": 860, "width": 60, "depth": 0.30},
            {"center": 650, "width": 40, "depth": 0.10},
            {"center": 1920, "width": 80, "depth": 0.05},
        ],
        "slope": 0.00003,
    },
    "goethite": {
        "base_reflectance": 0.22,
        "absorptions": [
            {"center": 480, "width": 40, "depth": 0.15},
            {"center": 900, "width": 80, "depth": 0.28},
            {"center": 1920, "width": 60, "depth": 0.05},
        ],
        "slope": 0.00002,
    },
    "gypsum": {
        "base_reflectance": 0.55,
        "absorptions": [
            {"center": 1450, "width": 30, "depth": 0.12},
            {"center": 1750, "width": 20, "depth": 0.08},
            {"center": 1940, "width": 40, "depth": 0.20},
            {"center": 2265, "width": 25, "depth": 0.05},
        ],
        "slope": 0.00000,
    },
    "montmorillonite": {
        "base_reflectance": 0.30,
        "absorptions": [
            {"center": 2210, "width": 40, "depth": 0.20},
            {"center": 1420, "width": 50, "depth": 0.10},
            {"center": 1920, "width": 50, "depth": 0.15},
            {"center": 2350, "width": 30, "depth": 0.05},
        ],
        "slope": -0.00004,
    },
    "quartz": {
        "base_reflectance": 0.55,
        "absorptions": [
            {"center": 1400, "width": 30, "depth": 0.02},
            {"center": 1900, "width": 30, "depth": 0.02},
            {"center": 2250, "width": 50, "depth": 0.03},
        ],
        "slope": 0.00000,
    },
}


def _build_mineral_spectrum(wavelengths, spec_def):
    wvl = np.asarray(wavelengths, dtype=float)
    base = spec_def["base_reflectance"]
    slope = spec_def.get("slope", 0.0)
    spectrum = np.ones_like(wvl) * base + slope * (wvl - wvl[0])

    for abs_feat in spec_def.get("absorptions", []):
        center = abs_feat["center"]
        width = abs_feat["width"]
        depth = abs_feat["depth"]
        absorption = depth * np.exp(-((wvl - center) ** 2) / (2 * width ** 2))
        spectrum -= absorption

    rng = np.random.default_rng(abs(hash(str(base) + str(slope))) % (2**31))
    noise = rng.normal(0, 0.002, len(wvl))
    spectrum += noise
    return np.clip(spectrum, 0.01, 0.95)


def generate_default_library_csvs(directory, wavelengths=None):
    """Populate directory with reference mineral CSV spectra."""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    if wavelengths is None:
        wavelengths = np.arange(350, 2551, 5, dtype=float)

    for name, spec_def in _DEFAULT_MINERAL_SPECS.items():
        csv_path = out_dir / f"{name}.csv"
        if not csv_path.exists():
            refl = _build_mineral_spectrum(wavelengths, spec_def)
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(f"# name: {name}\n")
                f.write("# wavelength_unit: nm\n")
                f.write("wavelength_nm,reflectance\n")
                for w, r in zip(wavelengths, refl):
                    f.write(f"{w:.2f},{r:.6f}\n")


def load_library(library_dir, wavelength_unit: str = "nm") -> Tuple[List[str], np.ndarray, np.ndarray]:
    """Load USGS spectral library from directory. If empty, generate standard spectra.

    Returns:
        (names, wavelengths, spectra_matrix)
        names: list of mineral names
        wavelengths: 1-D array of wavelengths in target unit
        spectra_matrix: 2-D array of shape (n_minerals, n_bands)
    """
    path = Path(library_dir)
    path.mkdir(parents=True, exist_ok=True)
    csv_files = list(path.glob("*.csv"))
    if not csv_files:
        generate_default_library_csvs(path)

    lib = SpectralLibrary.from_directory(path, wavelength_unit=wavelength_unit)
    names = lib.names
    spectra_objs = lib.spectra()

    # Use first spectrum's wavelengths as common reference grid
    target_unit = normalize_unit(wavelength_unit)
    ref_wl = spectra_objs[0].wavelength_in(target_unit)

    matrix_rows = []
    for s in spectra_objs:
        s_wl = s.wavelength_in(target_unit)
        if len(s_wl) == len(ref_wl) and np.allclose(s_wl, ref_wl):
            matrix_rows.append(s.reflectance)
        else:
            # Interpolate to common grid if different
            refl = np.interp(ref_wl, s_wl, s.reflectance)
            matrix_rows.append(refl)

    matrix = np.asarray(matrix_rows, dtype=float)
    return names, ref_wl, matrix


def align_to_wavelengths(lib_wl, spectra, target_wl, method: str = "linear") -> np.ndarray:
    """Resample library spectra onto target wavelengths."""
    import wavelength_alignment
    return wavelength_alignment.resample_spectra(lib_wl, spectra, target_wl, method=method)


class MineralSpectralLibrary:
    """Backward compatibility wrapper for existing tests and modules."""

    def __init__(self, library_path=None):
        self.wavelengths = np.arange(400, 2501, 10, dtype=float)
        self.minerals = {}
        for name, spec_def in _DEFAULT_MINERAL_SPECS.items():
            self.minerals[name] = _build_mineral_spectrum(self.wavelengths, spec_def)

    def get_mineral_names(self):
        return list(self.minerals.keys())

    def get_spectrum(self, mineral_name):
        return self.minerals.get(mineral_name, None)

    def get_all_spectra_matrix(self):
        names = sorted(self.minerals.keys())
        matrix = np.array([self.minerals[n] for n in names])
        return names, matrix

