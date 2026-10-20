"""
HydraNet batch pipelines (Dagster).

    imagery_catalog        daily   EMIT + Landsat footprints over the region -> PostGIS catalog_granules
    prestaged_granules     daily   download the clearest recent EMIT granules into Bronze (shared cache)
    events_compacted       hourly  merge the event sink's small Parquet files per topic/day
    gold_job_facts         hourly  one row per job (stage timings, outcome) -> gold/tables
    gold_layer_stats       hourly  per-layer statistics of every map -> gold/tables
    model_agreement_report daily   shadow agreement HydraNet vs classical over 24 h -> dq_results (+ alert)
    db_housekeeping        daily   prune processed_events / sent outbox rows
    pg_backup              nightly pg_dump -> ops/pg_backups/

Lineage in the Dagster UI: catalog -> staged granules; events -> compacted -> analytics.
"""

from dagster import (AssetSelection, Definitions, ScheduleDefinition, define_asset_job,
                     load_assets_from_modules)

from . import assets

all_assets = load_assets_from_modules([assets])

daily = define_asset_job("daily", AssetSelection.assets(
    assets.imagery_catalog, assets.prestaged_granules, assets.model_agreement_report, assets.db_housekeeping))
hourly = define_asset_job("hourly", AssetSelection.assets(
    assets.events_compacted, assets.gold_job_facts, assets.gold_layer_stats))
nightly = define_asset_job("nightly", AssetSelection.assets(assets.pg_backup))

defs = Definitions(
    assets=all_assets,
    jobs=[daily, hourly, nightly],
    schedules=[
        ScheduleDefinition(job=daily, cron_schedule="15 3 * * *"),
        ScheduleDefinition(job=hourly, cron_schedule="7 * * * *"),
        ScheduleDefinition(job=nightly, cron_schedule="45 2 * * *"),
    ],
)
