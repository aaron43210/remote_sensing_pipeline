import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio

from thermal_feature_pipeline import build_thermal_features, validate_manifest


class ThermalFeaturePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)
        self.raster_path = self.directory / "lst.tif"
        self.manifest_path = self.directory / "manifest.json"

        transform = rasterio.transform.from_origin(74.0, 31.1, 0.001, 0.001)
        values = np.array(
            [
                [300.0, 302.0, 304.0],
                [306.0, 308.0, 310.0],
                [312.0, 314.0, 316.0],
            ],
            dtype=np.float32,
        )
        with rasterio.open(
            self.raster_path,
            "w",
            driver="GTiff",
            height=3,
            width=3,
            count=1,
            dtype=values.dtype,
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999,
        ) as dst:
            dst.write(values, 1)

        manifest = {
            "scene_id": "TEST_SCENE_ST_B10",
            "thermal_path": self.raster_path.name,
            "sensor": "Landsat TIRS Band 10",
            "source_file": "TEST_SCENE_ST_B10.TIF",
            "bbox": [74.0, 31.097, 74.003, 31.1],
            "crs": "EPSG:4326",
            "units": "celsius",
            "scale": 1.0,
            "offset": 0.0,
            "fill_value": -9999,
            "shape": [3, 3],
        }
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_manifest_and_feature_building(self) -> None:
        validated = validate_manifest(self.manifest_path, self.raster_path)
        self.assertEqual(validated["scene_id"], "TEST_SCENE_ST_B10")
        self.assertEqual(validated["shape"], [3, 3])

        output_csv = self.directory / "features.csv"
        dataframe = build_thermal_features(
            raster_path=self.raster_path,
            manifest_path=self.manifest_path,
            output_csv=output_csv,
            max_samples=9,
        )

        self.assertTrue(output_csv.exists())
        self.assertEqual(len(dataframe), 9)
        self.assertTrue(
            {"lst", "local_mean_3x3", "local_std_3x3", "thermal_class"}
            <= set(dataframe.columns)
        )
        self.assertTrue(dataframe["scene_id"].eq("TEST_SCENE_ST_B10").all())
        self.assertTrue(set(dataframe["thermal_class"].unique()).issubset({0, 1, 2, 3}))

    def test_invalid_roi_is_rejected(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["bbox"] = [74.0, 31.1, 74.001, 31.101]
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "does not match raster"):
            validate_manifest(self.manifest_path, self.raster_path)


if __name__ == "__main__":
    unittest.main()
