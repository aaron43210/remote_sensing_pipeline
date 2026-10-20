#!/bin/bash
# One-shot platform init (idempotent): storage, admin account, first model.
set -euo pipefail
python scripts/init_storage.py
# the gateway applies DB migrations; wait until the schema exists
for i in $(seq 1 60); do
  python - <<'PY' && break
import os, psycopg, sys
with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    sys.exit(0 if c.execute("select to_regclass('public.model_versions')").fetchone()[0] else 1)
PY
  echo "waiting for database migrations"; sleep 2
done
python scripts/bootstrap_admin.py
if [ -f /artifacts/models/${MODEL_VERSION:-v1}/hydra.onnx ]; then
  python scripts/promote_model.py --if-missing \
    --model-dir /artifacts/models/${MODEL_VERSION:-v1} --lut /artifacts/data/agri_lut.npz
else
  echo "no trained model at /artifacts/models/${MODEL_VERSION:-v1}; train it (training/README.md) then rerun tools"
fi
