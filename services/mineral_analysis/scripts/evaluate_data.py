"""
Comprehensive evaluation of mineral analysis datasets:
  1. training_sample.csv
  2. dataset_sample.csv
  3. Individual USGS mineral library files
"""
import os
import csv
import sys
import json
from collections import Counter

# --- Paths ---
BASE = r"C:\Users\sanan\OneDrive\Documents\Thermal Remote Sensing\Project\remote_sensing_pipeline\services\mineral_analysis"
TRAINING_CSV = os.path.join(BASE, "output", "training_set", "training_sample.csv")
DATASET_CSV = os.path.join(BASE, "output", "dataset", "dataset_sample.csv")
USGS_DIR = os.path.join(BASE, "data", "usgs_library")

MINERAL_NAMES = [
    "alunite", "calcite", "chlorite", "dolomite", "goethite",
    "gypsum", "hematite", "illite", "kaolinite",
    "montmorillonite", "muscovite", "quartz"
]

issues = []

def report(section, severity, message):
    """severity: ERROR, WARNING, INFO"""
    issues.append({"section": section, "severity": severity, "message": message})
    tag = {"ERROR": "[X]", "WARNING": "[!]", "INFO": "[i]"}.get(severity, "")
    print(f"  {tag} [{severity}] {message}")

# ============================================================================
# 1. Evaluate individual USGS mineral library files
# ============================================================================
print("=" * 80)
print("1. EVALUATING USGS MINERAL LIBRARY FILES")
print("=" * 80)

mineral_data = {}  # name -> {wavelengths: [...], reflectances: [...]}

for mineral in MINERAL_NAMES:
    filepath = os.path.join(USGS_DIR, f"{mineral}.csv")
    section = f"usgs/{mineral}"
    print(f"\n--- {mineral}.csv ---")

    if not os.path.exists(filepath):
        report(section, "ERROR", f"File not found: {filepath}")
        continue

    wavelengths = []
    reflectances = []
    header_name = None
    header_unit = None
    line_errors = 0

    with open(filepath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue  # skip blank lines
            if line.startswith("# name:"):
                header_name = line.split(":", 1)[1].strip()
                if header_name != mineral:
                    report(section, "ERROR", f"Header name '{header_name}' does not match filename '{mineral}'")
                continue
            if line.startswith("# wavelength_unit:"):
                header_unit = line.split(":", 1)[1].strip()
                if header_unit != "nm":
                    report(section, "WARNING", f"Unexpected wavelength unit: '{header_unit}'")
                continue
            if line.startswith("#"):
                continue
            if line.startswith("wavelength"):
                # Header row
                parts = line.split(",")
                if parts != ["wavelength_nm", "reflectance"]:
                    report(section, "WARNING", f"Unexpected column header: {parts}")
                continue

            # Data row
            parts = line.split(",")
            if len(parts) != 2:
                report(section, "ERROR", f"Line {i}: Expected 2 columns, got {len(parts)}")
                line_errors += 1
                continue
            try:
                wl = float(parts[0])
                ref = float(parts[1])
                wavelengths.append(wl)
                reflectances.append(ref)
            except ValueError as e:
                report(section, "ERROR", f"Line {i}: Cannot parse numbers: {e}")
                line_errors += 1

    mineral_data[mineral] = {"wavelengths": wavelengths, "reflectances": reflectances}
    n_points = len(wavelengths)
    report(section, "INFO", f"{n_points} data points, wavelength range: {min(wavelengths):.1f} - {max(wavelengths):.1f} nm")

    if header_name is None:
        report(section, "WARNING", "Missing '# name:' header comment")
    if header_unit is None:
        report(section, "WARNING", "Missing '# wavelength_unit:' header comment")

    # Check reflectance range
    min_ref = min(reflectances)
    max_ref = max(reflectances)
    if min_ref < 0 or max_ref > 1:
        report(section, "WARNING", f"Reflectance values outside [0,1]: min={min_ref:.6f}, max={max_ref:.6f}")
    else:
        report(section, "INFO", f"Reflectance range: {min_ref:.6f} - {max_ref:.6f}")

    # Check wavelength ordering
    for j in range(1, len(wavelengths)):
        if wavelengths[j] <= wavelengths[j - 1]:
            report(section, "ERROR", f"Wavelength not strictly increasing at index {j}: {wavelengths[j-1]} -> {wavelengths[j]}")
            break

    # Check wavelength spacing consistency
    if len(wavelengths) > 1:
        spacings = [wavelengths[j] - wavelengths[j - 1] for j in range(1, len(wavelengths))]
        spacing_set = set(round(s, 2) for s in spacings)
        if len(spacing_set) > 1:
            report(section, "INFO", f"Wavelength spacing varies: {sorted(spacing_set)}")

    if line_errors > 0:
        report(section, "ERROR", f"{line_errors} lines with parsing errors")

# Cross-compare mineral files for duplicates
print("\n--- Cross-comparing mineral library spectra ---")
for i, m1 in enumerate(MINERAL_NAMES):
    for j, m2 in enumerate(MINERAL_NAMES):
        if j <= i:
            continue
        if m1 not in mineral_data or m2 not in mineral_data:
            continue
        r1 = mineral_data[m1]["reflectances"]
        r2 = mineral_data[m2]["reflectances"]
        if len(r1) != len(r2):
            continue
        # Check if data is identical or nearly identical
        if r1 == r2:
            report("usgs/cross-compare", "ERROR", f"'{m1}' and '{m2}' have IDENTICAL reflectance data!")
        else:
            # Check how similar they are
            n_same = sum(1 for a, b in zip(r1, r2) if a == b)
            frac_same = n_same / len(r1) if r1 else 0
            if frac_same > 0.5:
                report("usgs/cross-compare", "ERROR",
                       f"'{m1}' and '{m2}' share {n_same}/{len(r1)} ({frac_same*100:.1f}%) identical reflectance values — likely corrupted data!")

# ============================================================================
# 2. Evaluate training_sample.csv
# ============================================================================
print("\n" + "=" * 80)
print("2. EVALUATING training_sample.csv")
print("=" * 80)

section = "training_sample"

if not os.path.exists(TRAINING_CSV):
    report(section, "ERROR", f"File not found: {TRAINING_CSV}")
else:
    with open(TRAINING_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    n_cols = len(header)
    n_rows = len(rows)
    report(section, "INFO", f"{n_rows} rows x {n_cols} columns")

    # Column analysis
    cr_cols = [c for c in header if c.startswith("cr_")]
    depth_cols = [c for c in header if c.startswith("depth_")]
    sam_cols = [c for c in header if c.startswith("sam_")]
    abund_cols = [c for c in header if c.startswith("abund_")]
    other_cols = [c for c in header if not any(c.startswith(p) for p in ["cr_", "depth_", "sam_", "abund_"])]

    report(section, "INFO", f"Column groups: {len(cr_cols)} spectral (cr_), {len(depth_cols)} depth, {len(sam_cols)} SAM, {len(abund_cols)} abundance, other: {other_cols}")

    # Check for 'dominant' or 'label' column
    has_dominant = "dominant" in header
    has_label_name = "label_name" in header
    has_label_index = "label_index" in header

    if has_dominant:
        dominant_idx = header.index("dominant")
        labels = [row[dominant_idx] for row in rows if len(row) > dominant_idx]
        label_counts = Counter(labels)
        report(section, "INFO", f"Label column 'dominant': {dict(label_counts)}")

        # Check unknown labels
        known_labels = set(MINERAL_NAMES)
        unknown = set(labels) - known_labels
        if unknown:
            report(section, "WARNING", f"Unknown mineral labels: {unknown}")
    else:
        report(section, "WARNING", "No 'dominant' column found")

    # Check for NaN, empty, or non-numeric values in numeric columns
    nan_count = 0
    empty_count = 0
    negative_cr = 0
    above_one_cr = 0
    row_length_errors = 0

    for ri, row in enumerate(rows):
        if len(row) != n_cols:
            row_length_errors += 1
            if row_length_errors <= 3:
                report(section, "ERROR", f"Row {ri+2}: expected {n_cols} columns, got {len(row)}")

        for ci, val in enumerate(row):
            if header[ci] in ["dominant", "label_name"]:
                continue
            val = val.strip()
            if val == "" or val.lower() == "nan":
                nan_count += 1
            elif val.lower() == "inf" or val.lower() == "-inf":
                report(section, "ERROR", f"Row {ri+2}, col '{header[ci]}': infinite value '{val}'")
            else:
                try:
                    fval = float(val)
                    if header[ci].startswith("cr_"):
                        if fval < 0:
                            negative_cr += 1
                        if fval > 1.01:  # Allow very small float errors
                            above_one_cr += 1
                except ValueError:
                    pass  # Non-numeric in non-label column
    
    if row_length_errors > 3:
        report(section, "ERROR", f"... and {row_length_errors - 3} more rows with wrong column count")
    if nan_count > 0:
        report(section, "WARNING", f"{nan_count} empty/NaN values found")
    if negative_cr > 0:
        report(section, "WARNING", f"{negative_cr} negative values in spectral (cr_) columns")
    if above_one_cr > 0:
        report(section, "INFO", f"{above_one_cr} spectral values > 1.0 (these are continuum-removed ratios, values at 1.0 indicate band maxima)")

    # Check abundance sums
    if abund_cols:
        bad_sums = 0
        for ri, row in enumerate(rows):
            try:
                abund_vals = [float(row[header.index(c)]) for c in abund_cols]
                s = sum(abund_vals)
                if abs(s - 1.0) > 0.05 and s != 0.0:
                    bad_sums += 1
                    if bad_sums <= 3:
                        report(section, "WARNING", f"Row {ri+2}: abundance sum = {s:.4f} (expected ~1.0 or 0.0)")
            except (ValueError, IndexError):
                pass
        if bad_sums > 3:
            report(section, "WARNING", f"... and {bad_sums - 3} more rows with unexpected abundance sums")

    # Check for 'quality' column (only expected in dataset, not training)
    if "quality" in header:
        report(section, "INFO", "Has 'quality' column")
    if "label_index" in header:
        report(section, "INFO", "Has 'label_index' column")

# ============================================================================
# 3. Evaluate dataset_sample.csv
# ============================================================================
print("\n" + "=" * 80)
print("3. EVALUATING dataset_sample.csv")
print("=" * 80)

section = "dataset_sample"

if not os.path.exists(DATASET_CSV):
    report(section, "ERROR", f"File not found: {DATASET_CSV}")
else:
    with open(DATASET_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        ds_header = next(reader)
        ds_rows = list(reader)

    n_cols_ds = len(ds_header)
    n_rows_ds = len(ds_rows)
    report(section, "INFO", f"{n_rows_ds} rows x {n_cols_ds} columns")

    ds_cr_cols = [c for c in ds_header if c.startswith("cr_")]
    ds_depth_cols = [c for c in ds_header if c.startswith("depth_")]
    ds_sam_cols = [c for c in ds_header if c.startswith("sam_")]
    ds_abund_cols = [c for c in ds_header if c.startswith("abund_")]
    ds_other_cols = [c for c in ds_header if not any(c.startswith(p) for p in ["cr_", "depth_", "sam_", "abund_"])]

    report(section, "INFO", f"Column groups: {len(ds_cr_cols)} spectral (cr_), {len(ds_depth_cols)} depth, {len(ds_sam_cols)} SAM, {len(ds_abund_cols)} abundance, other: {ds_other_cols}")

    # Check for label columns
    if "label_name" in ds_header:
        label_idx = ds_header.index("label_name")
        ds_labels = [row[label_idx] for row in ds_rows if len(row) > label_idx]
        ds_label_counts = Counter(ds_labels)
        report(section, "INFO", f"Label column 'label_name': {dict(ds_label_counts)}")

        unknown = set(ds_labels) - set(MINERAL_NAMES)
        if unknown:
            report(section, "WARNING", f"Unknown mineral labels: {unknown}")

    if "label_index" in ds_header:
        li_idx = ds_header.index("label_index")
        li_vals = set(row[li_idx] for row in ds_rows if len(row) > li_idx)
        report(section, "INFO", f"label_index values: {sorted(li_vals)}")

    if "quality" in ds_header:
        qi = ds_header.index("quality")
        q_vals = [float(row[qi]) for row in ds_rows if len(row) > qi and row[qi].strip()]
        report(section, "INFO", f"quality range: {min(q_vals):.6f} - {max(q_vals):.6f}")

    # NaN / error checks
    nan_count_ds = 0
    negative_cr_ds = 0
    above_one_cr_ds = 0
    row_length_errors_ds = 0

    for ri, row in enumerate(ds_rows):
        if len(row) != n_cols_ds:
            row_length_errors_ds += 1
            if row_length_errors_ds <= 3:
                report(section, "ERROR", f"Row {ri+2}: expected {n_cols_ds} columns, got {len(row)}")

        for ci, val in enumerate(row):
            if ds_header[ci] in ["dominant", "label_name"]:
                continue
            val = val.strip()
            if val == "" or val.lower() == "nan":
                nan_count_ds += 1
            else:
                try:
                    fval = float(val)
                    if ds_header[ci].startswith("cr_"):
                        if fval < 0:
                            negative_cr_ds += 1
                        if fval > 1.01:
                            above_one_cr_ds += 1
                except ValueError:
                    pass

    if row_length_errors_ds > 3:
        report(section, "ERROR", f"... and {row_length_errors_ds - 3} more rows with wrong column count")
    if nan_count_ds > 0:
        report(section, "WARNING", f"{nan_count_ds} empty/NaN values found")
    if negative_cr_ds > 0:
        report(section, "WARNING", f"{negative_cr_ds} negative values in spectral (cr_) columns")
    if above_one_cr_ds > 0:
        report(section, "INFO", f"{above_one_cr_ds} spectral values > 1.0")

    # Abundance check
    if ds_abund_cols:
        bad_sums_ds = 0
        for ri, row in enumerate(ds_rows):
            try:
                abund_vals = [float(row[ds_header.index(c)]) for c in ds_abund_cols]
                s = sum(abund_vals)
                if abs(s - 1.0) > 0.05 and s != 0.0:
                    bad_sums_ds += 1
                    if bad_sums_ds <= 3:
                        report(section, "WARNING", f"Row {ri+2}: abundance sum = {s:.4f} (expected ~1.0 or 0.0)")
            except (ValueError, IndexError):
                pass
        if bad_sums_ds > 3:
            report(section, "WARNING", f"... and {bad_sums_ds - 3} more rows with unexpected abundance sums")

# ============================================================================
# 4. Compare training_sample.csv vs dataset_sample.csv
# ============================================================================
print("\n" + "=" * 80)
print("4. COMPARING training_sample.csv vs dataset_sample.csv")
print("=" * 80)

section = "comparison"

if 'header' in dir() and 'ds_header' in dir():
    # Compare columns
    train_set = set(header)
    ds_set = set(ds_header)

    only_train = train_set - ds_set
    only_ds = ds_set - train_set

    if only_train:
        report(section, "WARNING", f"Columns ONLY in training_sample: {only_train}")
    if only_ds:
        report(section, "WARNING", f"Columns ONLY in dataset_sample: {only_ds}")

    common_cols = train_set & ds_set
    report(section, "INFO", f"{len(common_cols)} common columns, {len(only_train)} only-training, {len(only_ds)} only-dataset")

    # Compare spectral columns
    train_cr = set(c for c in header if c.startswith("cr_"))
    ds_cr = set(c for c in ds_header if c.startswith("cr_"))
    if train_cr == ds_cr:
        report(section, "INFO", f"Spectral columns match ({len(train_cr)} bands)")
    else:
        missing_in_ds = train_cr - ds_cr
        missing_in_train = ds_cr - train_cr
        if missing_in_ds:
            report(section, "ERROR", f"Spectral columns in training but NOT in dataset: {sorted(missing_in_ds)[:5]}...")
        if missing_in_train:
            report(section, "ERROR", f"Spectral columns in dataset but NOT in training: {sorted(missing_in_train)[:5]}...")

    # Compare label schemes
    if has_dominant and "label_name" in ds_header:
        report(section, "INFO", "Training uses 'dominant' column; Dataset uses 'label_name' + 'label_index'")
    elif has_dominant and "label_name" not in ds_header:
        report(section, "WARNING", "Training has 'dominant' but dataset lacks 'label_name'")

# ============================================================================
# 5. Summary
# ============================================================================
print("\n" + "=" * 80)
print("5. SUMMARY")
print("=" * 80)

errors = [i for i in issues if i["severity"] == "ERROR"]
warnings = [i for i in issues if i["severity"] == "WARNING"]
infos = [i for i in issues if i["severity"] == "INFO"]

print(f"\n  Total issues: {len(issues)}")
print(f"  [X] ERRORS:   {len(errors)}")
print(f"  [!] WARNINGS: {len(warnings)}")
print(f"  [i] INFO:     {len(infos)}")

if errors:
    print("\n  === CRITICAL ERRORS ===")
    for e in errors:
        print(f"    [{e['section']}] {e['message']}")

if warnings:
    print("\n  === WARNINGS ===")
    for w in warnings:
        print(f"    [{w['section']}] {w['message']}")

print("\nDone.")
