-- Runs once, on an empty data directory. The application schema itself is
-- applied by the gateway's migrations; Dagster gets its own database because
-- its tables (e.g. "jobs") would collide with ours.
CREATE DATABASE dagster;
