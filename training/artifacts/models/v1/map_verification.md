# Map verification: HydraNet v1 vs classical method

| Layer | Metric | HydraNet vs classical | Gate | Result |
|---|---|---|---|---|
| mineral_class | pixel_agreement | 0.99957 | 0.98 | PASS |
| mineral_abundance | mae | 0.0057 | 0.03 | PASS |
| mineral_quality | mae | 0.00745 | 0.03 | PASS |
| agri_Cab | nmae | 0.01624 | 0.05 | PASS |
| agri_Cw | nmae | 0.01444 | 0.05 | PASS |
| agri_LAI | nmae | 0.03447 | 0.05 | PASS |
| thermal_global_class | pixel_agreement | 0.99879 | 0.99 | PASS |
| thermal_uhi_class | pixel_agreement | 0.99855 | 0.99 | PASS |
| thermal_local_anomaly | pixel_agreement | 0.99969 | 0.99 | PASS |
| thermal_hotspots | pixel_agreement | 0.99832 | 0.99 | PASS |

## Real Landsat scenes

| Scene | Landsat ID | Date | Global | UHI | Local | Hotspots |
|---|---|---|---|---|---|---|
| fresno_city | LC08_L2SP_043034_20240930_02_T1 | 2024-09-30 | 0.9974004753416518 | 0.9996180290297937 | 0.9999681690858161 | 0.9996710805534335 |
| fresno_winter | LC08_L2SP_042035_20240228_02_T1 | 2024-02-28 | 0.9986529301463457 | 0.999353406470246 | 0.9997857066323798 | 0.9993464052287582 |
| bakersfield_oil | LC09_L2SP_042035_20240830_02_T1 | 2024-08-30 | 0.999292740950299 | 0.9995606421054888 | 0.9999249876765468 | 0.9996570865213571 |
| sacramento | LC09_L2SP_044033_20240929_02_T1 | 2024-09-29 | 0.9993760068979601 | 0.9992398629484242 | 0.999943690523115 | 0.9998648572554761 |
| tulare_farms | LC09_L2SP_042035_20240729_02_T1 | 2024-07-29 | 0.9997849947861236 | 0.9941303576611733 | 0.998548714806334 | 0.9916577977015942 |
| modesto | LC08_L2SP_043034_20240930_02_T1 | 2024-09-30 | 0.9982560706401766 | 0.9994701986754967 | 0.9999779249448124 | 0.9998013245033113 |
