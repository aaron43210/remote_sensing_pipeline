# Map verification: HydraNet v1 vs classical method

| Layer | Metric | HydraNet vs classical | Gate | Result |
|---|---|---|---|---|
| mineral_class | pixel_agreement | 1.0 | 0.98 | PASS |
| mineral_abundance | mae | 0.00194 | 0.03 | PASS |
| mineral_quality | mae | 0.00404 | 0.03 | PASS |
| agri_Cab | nmae | 0.0171 | 0.05 | PASS |
| agri_Cw | nmae | 0.01492 | 0.05 | PASS |
| agri_LAI | nmae | 0.03425 | 0.05 | PASS |
| thermal_global_class | pixel_agreement | 0.99965 | 0.99 | PASS |
| thermal_uhi_class | pixel_agreement | 0.9984 | 0.99 | PASS |
| thermal_local_anomaly | pixel_agreement | 0.99978 | 0.99 | PASS |
| thermal_hotspots | pixel_agreement | 0.99775 | 0.99 | PASS |
| thermal_real_scene_min | pixel_agreement | 0.98808 | 0.98 | PASS |

## Real Landsat scenes

| Scene | Landsat ID | Date | Global | UHI | Local | Hotspots |
|---|---|---|---|---|---|---|
| fresno_city | LC08_L2SP_043034_20240930_02_T1 | 2024-09-30 | 0.9992678889737713 | 0.9996923011628894 | 0.9999575587810882 | 0.9997665732959851 |
| fresno_winter | LC08_L2SP_042035_20240228_02_T1 | 2024-02-28 | 0.9995258314115136 | 0.9989977800288812 | 0.9998071359691417 | 0.9994535519125683 |
| bakersfield_oil | LC09_L2SP_042035_20240830_02_T1 | 2024-08-30 | 0.9997428148910178 | 0.9995177779206584 | 0.9999142716303393 | 0.9996999507061874 |
| sacramento | LC09_L2SP_044033_20240929_02_T1 | 2024-09-29 | 0.999841165392208 | 0.9992171722901682 | 0.999943690523115 | 0.9998423334647221 |
| tulare_farms | LC09_L2SP_042035_20240729_02_T1 | 2024-07-29 | 0.9998709968716741 | 0.9935820943657884 | 0.9991184786231067 | 0.9880779608905516 |
| modesto | LC08_L2SP_043034_20240930_02_T1 | 2024-09-30 | 0.9996357615894039 | 0.9994701986754967 | 0.9999779249448124 | 0.9997902869757175 |
