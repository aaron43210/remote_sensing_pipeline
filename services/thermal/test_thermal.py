import os
import rasterio
import numpy as np

from lst_calculator import LSTCalculator
from anomaly_detector import ThermalAnomalyDetector
from suhi import SUHIAnalyzer


# ============================================================
# TEST DATA DIRECTORY
# ============================================================

TEST_DATA_DIR = "test_data"

lst_path = os.path.join(
    TEST_DATA_DIR,
    "bathinda_lst.tif"
)


# ============================================================
# 1. READ BATHINDA LST
# ============================================================

with rasterio.open(lst_path) as src:

    lst_raw = src.read(1).astype(np.float32)

    print("\n========== INPUT RASTER ==========")
    print("Shape:", lst_raw.shape)
    print("CRS:", src.crs)
    print("Resolution:", src.res)
    print("NoData:", src.nodata)


# ============================================================
# 2. LST VALIDATION
# ============================================================

lst_calc = LSTCalculator()

lst = lst_calc.lst_from_celsius(lst_raw)

print("\n========== LST ==========")

valid = np.isfinite(lst)

print("Valid pixels:", np.sum(valid))
print("Min:", np.nanmin(lst))
print("Max:", np.nanmax(lst))
print("Mean:", np.nanmean(lst))
print("Std:", np.nanstd(lst))


# ============================================================
# 3. THERMAL CLASSIFICATION
# ============================================================

indices = lst_calc.compute_thermal_indices(lst)

print("\n========== CLASSIFICATION ==========")

print("Mean:", indices["mean"])
print("Std:", indices["std"])
print("Min:", indices["min"])
print("Max:", indices["max"])

print("Classification thresholds:")
print(indices["classification_thresholds"])

print("Classification labels:")
print(indices["classification_labels"])

classification = indices["classification"]

print("Classification shape:")
print(classification.shape)

print("Class pixel counts:")

for class_id in range(1, 6):

    count = int(
        np.sum(classification == class_id)
    )

    print(
        f"Class {class_id}: {count} pixels"
    )


# ============================================================
# 4. GLOBAL ANOMALIES
# ============================================================

detector = ThermalAnomalyDetector()

(
    global_z,
    hot_mask,
    cold_mask,
    global_stats,
) = detector.detect_global_anomalies(lst)

print("\n========== GLOBAL ANOMALIES ==========")

print(
    "Hot anomaly pixels:",
    np.sum(hot_mask)
)

print(
    "Cold anomaly pixels:",
    np.sum(cold_mask)
)

print("Global statistics:")
print(global_stats)


# ============================================================
# 5. LOCAL ANOMALIES
# ============================================================

(
    local_z,
    local_anomalies,
    local_stats,
) = detector.detect_local_anomalies(lst)

print("\n========== LOCAL ANOMALIES ==========")

print(
    "Local anomaly pixels:",
    np.sum(local_anomalies)
)

print("Local statistics:")
print(local_stats)


# ============================================================
# 6. HOTSPOTS
# ============================================================

(
    hotspot_map,
    hotspot_info,
    hotspot_stats,
) = detector.detect_hotspots(lst)

print("\n========== HOTSPOTS ==========")

print(
    "Hotspot pixels:",
    np.sum(hotspot_map > 0)
)

print(
    "Number of hotspot components:",
    len(hotspot_info)
)

print("Hotspot statistics:")
print(hotspot_stats)


# ============================================================
# 7. SUHI TEST
# ============================================================

print("\n========== SUHI TEST ==========")


# ------------------------------------------------------------
# Dynamic World input files
# These are the ALIGNED 30 m rasters
# ------------------------------------------------------------

dw_label_path = os.path.join(
    TEST_DATA_DIR,
    "dynamicworld_label_30m.tif"
)

dw_probability_path = os.path.join(
    TEST_DATA_DIR,
    "dynamicworld_built_probability_30m.tif"
)


# ------------------------------------------------------------
# Check that the files exist
# ------------------------------------------------------------

if not os.path.exists(dw_label_path):

    raise FileNotFoundError(
        f"Dynamic World 30 m label raster not found:\n"
        f"{dw_label_path}"
    )


if not os.path.exists(dw_probability_path):

    raise FileNotFoundError(
        f"Dynamic World 30 m built probability raster "
        f"not found:\n"
        f"{dw_probability_path}"
    )


# ------------------------------------------------------------
# Read Dynamic World label raster
# ------------------------------------------------------------

with rasterio.open(dw_label_path) as src:

    dw_label = src.read(1)

    print(
        "Dynamic World label shape:",
        dw_label.shape
    )

    print(
        "Dynamic World label CRS:",
        src.crs
    )

    print(
        "Dynamic World label resolution:",
        src.res
    )


# ------------------------------------------------------------
# Read Dynamic World built probability raster
# ------------------------------------------------------------

with rasterio.open(dw_probability_path) as src:

    dw_built_probability = src.read(1)

    print(
        "Dynamic World built probability shape:",
        dw_built_probability.shape
    )

    print(
        "Dynamic World probability CRS:",
        src.crs
    )

    print(
        "Dynamic World probability resolution:",
        src.res
    )


# ============================================================
# 8. CHECK RASTER ALIGNMENT
# ============================================================

print("\n========== SUHI ALIGNMENT CHECK ==========")

print(
    "LST shape:",
    lst.shape
)

print(
    "DW label shape:",
    dw_label.shape
)

print(
    "DW probability shape:",
    dw_built_probability.shape
)


if dw_label.shape != lst.shape:

    raise ValueError(
        "\nDynamic World label raster does not match "
        "the LST raster shape.\n"
        f"LST shape: {lst.shape}\n"
        f"DW label shape: {dw_label.shape}\n\n"
        "The Dynamic World raster must be aligned "
        "to the 30 m LST grid before running SUHI."
    )


if dw_built_probability.shape != lst.shape:

    raise ValueError(
        "\nDynamic World built-probability raster does "
        "not match the LST raster shape.\n"
        f"LST shape: {lst.shape}\n"
        f"DW probability shape: "
        f"{dw_built_probability.shape}\n\n"
        "The Dynamic World raster must be aligned "
        "to the 30 m LST grid before running SUHI."
    )


print("Shape alignment: PASSED")


# ============================================================
# 9. CREATE SUHI ANALYZER
# ============================================================

suhi_analyzer = SUHIAnalyzer(
    pixel_size_m=30.0
)


# ============================================================
# 10. CREATE URBAN AND RURAL MASKS
# ============================================================

print("\n========== URBAN / RURAL MASKS ==========")

(
    urban_mask,
    rural_mask
) = suhi_analyzer.create_masks_from_dynamic_world(
    dynamic_world_label=dw_label,
    dynamic_world_built_probability=dw_built_probability
)


urban_pixels = int(
    np.sum(urban_mask)
)

rural_pixels = int(
    np.sum(rural_mask)
)

print(
    "Urban pixels:",
    urban_pixels
)

print(
    "Rural pixels:",
    rural_pixels
)

print(
    "Urban + Rural pixels:",
    urban_pixels + rural_pixels
)

print(
    "Valid LST pixels:",
    int(np.sum(valid))
)


# ============================================================
# 11. CALCULATE SUHI
# ============================================================

suhi_intensity, suhi_details = (
    suhi_analyzer.calculate(
        lst_celsius=lst,
        urban_mask=urban_mask,
        rural_mask=rural_mask
    )
)


# ============================================================
# 12. DISPLAY SUHI RESULTS
# ============================================================

print("\n========== SUHI RESULTS ==========")

print(
    "Urban mean LST:",
    suhi_details["urban_mean_lst_c"],
    "°C"
)

print(
    "Urban median LST:",
    suhi_details["urban_median_lst_c"],
    "°C"
)

print(
    "Urban std LST:",
    suhi_details["urban_std_lst_c"],
    "°C"
)

print(
    "Urban pixels:",
    suhi_details["urban_pixels"]
)

print(
    "Urban area:",
    suhi_details["urban_area_km2"],
    "km²"
)


print(
    "\nRural mean LST:",
    suhi_details["rural_mean_lst_c"],
    "°C"
)

print(
    "Rural median LST:",
    suhi_details["rural_median_lst_c"],
    "°C"
)

print(
    "Rural std LST:",
    suhi_details["rural_std_lst_c"],
    "°C"
)

print(
    "Rural pixels:",
    suhi_details["rural_pixels"]
)

print(
    "Rural area:",
    suhi_details["rural_area_km2"],
    "km²"
)


print(
    "\nSUHI intensity:",
    suhi_intensity,
    "°C"
)


# ============================================================
# 13. SUHI SENSITIVITY ANALYSIS
# ============================================================

print("\n========== SUHI SENSITIVITY ==========")

sensitivity = suhi_analyzer.sensitivity_analysis(
    lst_celsius=lst,
    dynamic_world_label=dw_label,
    dynamic_world_built_probability=dw_built_probability
)


print(
    "Built probability thresholds:",
    sensitivity["built_probability_thresholds"]
)


print("SUHI values:")

for threshold, value in zip(
    sensitivity["built_probability_thresholds"],
    sensitivity["suhi_c"]
):

    print(
        f"Built probability >= {threshold:.2f}: "
        f"{value:.3f} °C"
    )


# ============================================================
# 14. FINAL VALIDATION
# ============================================================

print("\n========== FINAL VALIDATION ==========")

expected_suhi = 1.395

actual_suhi = suhi_intensity

print(
    f"Expected SUHI: approximately "
    f"{expected_suhi:.3f} °C"
)

print(
    f"Actual SUHI: "
    f"{actual_suhi:.3f} °C"
)

difference = abs(
    actual_suhi - expected_suhi
)

print(
    f"Difference: "
    f"{difference:.6f} °C"
)


# ============================================================
# 15. TEST COMPLETED
# ============================================================

print("\n========================================")
print("THERMAL ALGORITHM TEST COMPLETED")
print("========================================")