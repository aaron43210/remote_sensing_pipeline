# =============================================================
# OWNER: AARON
# =============================================================
import pytest
import numpy as np


class TestMineralSpectralLibrary:

    def setup_method(self):
        from services.mineral_analysis.spectral_library import MineralSpectralLibrary
        self.library = MineralSpectralLibrary()

    def test_library_has_minerals(self):
        assert len(self.library.minerals) >= 10

    def test_all_minerals_have_spectra(self):
        for name, spectrum in self.library.minerals.items():
            assert len(spectrum) == len(self.library.wavelengths)
            assert not np.any(np.isnan(spectrum))

    def test_spectra_in_valid_range(self):
        for name, spectrum in self.library.minerals.items():
            assert np.all(spectrum >= 0)
            assert np.all(spectrum <= 1)

    def test_kaolinite_has_2205_absorption(self):
        """Kaolinite should have strong absorption near 2205nm."""
        spec = self.library.minerals["kaolinite"]
        wvl = self.library.wavelengths

        idx = np.argmin(np.abs(wvl - 2205))
        region = spec[max(0, idx-5):idx+5]
        shoulders = np.concatenate([
            spec[max(0, idx-15):max(0, idx-10)],
            spec[idx+10:min(len(spec), idx+15)]
        ])

        assert np.min(region) < np.mean(shoulders)

    def test_quartz_is_featureless(self):
        """Quartz should have minimal absorption features."""
        spec = self.library.minerals["quartz"]
        assert np.std(spec) < 0.1


class TestContinuumRemoval:

    def setup_method(self):
        from services.mineral_analysis.continuum_removal import ContinuumRemover
        self.remover = ContinuumRemover(method="hull")
        self.wavelengths = np.arange(400, 2501, 10, dtype=float)
        self.spectrum = 0.4 + 0.1 * np.sin(
            (self.wavelengths - 700) / 200
        )

    def test_cr_output_shape(self):
        cr, cont = self.remover.remove(self.wavelengths, self.spectrum)
        assert cr.shape == self.spectrum.shape

    def test_cr_range(self):
        cr, _ = self.remover.remove(self.wavelengths, self.spectrum)
        assert np.all(cr >= 0)
        assert np.all(cr <= 1.01)

    def test_continuum_above_spectrum(self):
        _, cont = self.remover.remove(self.wavelengths, self.spectrum)
        assert np.all(cont >= self.spectrum - 0.01)


class TestSpectralAngleMapper:

    def setup_method(self):
        from services.mineral_analysis.sam import SpectralAngleMapper
        self.sam = SpectralAngleMapper()

    def test_identical_spectra_zero_angle(self):
        spec = np.random.rand(200)
        angle = self.sam.compute_angle(spec, spec)
        assert angle < 0.001

    def test_orthogonal_spectra_right_angle(self):
        spec1 = np.zeros(200)
        spec1[0] = 1
        spec2 = np.zeros(200)
        spec2[1] = 1
        angle = self.sam.compute_angle(spec1, spec2)
        assert abs(angle - np.pi/2) < 0.01

    def test_classify_pixel_returns_valid(self):
        from services.mineral_analysis.spectral_library import MineralSpectralLibrary
        lib = MineralSpectralLibrary()
        aligned = {
            name: spec for name, spec in lib.minerals.items()
        }
        test_spectrum = lib.minerals["kaolinite"]

        result = self.sam.classify_pixel(test_spectrum, aligned)
        assert result["mineral"] == "kaolinite"
        assert result["angle"] < 0.05


class TestLinearUnmixing:

    def setup_method(self):
        from services.mineral_analysis.unmixing import LinearSpectralUnmixer
        self.unmixer = LinearSpectralUnmixer(method="nnls")

    def test_pure_pixel_recovery(self):
        """Pure pixel should give abundance ~1 for that mineral."""
        E = np.array([
            [0.1, 0.5, 0.8],
            [0.2, 0.6, 0.7],
            [0.3, 0.7, 0.6],
        ]).T  # (3 bands, 3 minerals)

        pure_pixel = E[:, 0]  # Pure mineral 0

        abundances, recon, error = self.unmixer.unmix_pixel(pure_pixel, E)
        assert abundances[0] > 0.95
        assert error < 0.01

    def test_mixed_pixel_recovery(self):
        """Known mixture should be recovered."""
        E = np.array([
            [0.1, 0.5, 0.8],
            [0.2, 0.6, 0.7],
            [0.3, 0.7, 0.6],
        ]).T

        true_abundances = np.array([0.3, 0.5, 0.2])
        mixed_pixel = E @ true_abundances

        abundances, _, error = self.unmixer.unmix_pixel(mixed_pixel, E)
        np.testing.assert_allclose(abundances, true_abundances, atol=0.05)

    def test_abundances_non_negative(self):
        E = np.random.rand(100, 5)
        pixel = np.random.rand(100)

        abundances, _, _ = self.unmixer.unmix_pixel(pixel, E)
        assert np.all(abundances >= 0)
