# Thermal Service — Bainty Kaur

**Owner:** Bainty Kaur

**Scope:** Physics-based thermal analysis of preprocessed Landsat 9 Land Surface Temperature (LST) data.

---

## What This Service Does

The Thermal Service performs thermal analysis on preprocessed Landsat 9 LST data.

The service:

1. **Reads** preprocessed thermal data from Kafka topic `thermal-raw`.

   The thermal raster is already prepared by the upstream ingestion service. The
   Thermal Service does not handle NASA Earthdata authentication or data
   downloading.

2. **Processes** the LST raster using thermal analysis methods:

   - LST validation and thermal statistics
   - Five-class thermal classification
   - Global thermal anomaly detection using Z-score
   - Local thermal anomaly detection using neighbourhood Z-score
   - Thermal hotspot detection using percentile thresholding and connected
     component analysis
   - Surface Urban Heat Island (SUHI) intensity calculation using Dynamic World
     urban/rural information
   - SUHI sensitivity analysis using different built-up probability thresholds

3. **Publishes** the processing result to Kafka topic `thermal-processed`.

4. **Stores** generated raster products in the MinIO bucket
   `thermal-processed`.

5. **Stores** the complete processing summary as JSON in the MinIO bucket
   `thermal-results`.

---

# Study Area

The current thermal implementation and validation were performed for:

**Bathinda District, Punjab, India**

### Test Dataset

| Parameter | Value |
|-----------|-------|
| Satellite | Landsat 9 |
| Product | Landsat Collection 2 Level-2 |
| Acquisition date | 2026-04-05 |
| Resolution | 30 m |
| CRS | EPSG:32643 |
| Study area | Bathinda, Punjab, India |
| LST unit | °C |

The production service is designed to process LST rasters supplied through the
Kafka interface, while the current validation dataset is the Bathinda study
area.

---

# Folder Structure

```text
services/thermal/

├── config.py
├── Dockerfile
├── requirements.txt
├── main.py
├── lst_calculator.py
├── anomaly_detector.py
├── suhi.py
├── test_thermal.py
└── .dockerignore