"""
Promote a trained HydraNet to production.

    python scripts/promote_model.py --model-dir training/artifacts/models/v1 \
                                    --lut training/artifacts/data/agri_lut.npz

Refuses unless BOTH the training gates (model_card.json) and the map-level
verification against the classical methods (map_verification.json) passed,
because the promise is "the network draws the same map as the normal method".
--force overrides, and the override is recorded in the registry.

Steps: upload hydra.onnx, agri_lut.npz, model_card.json, map_verification.*
to models/hydra/<version>/ ; register the version in Postgres (the only
active one) ; write models/hydra/active.json, which the inference service
polls and hot-swaps to without a restart.
"""

import argparse
import json
import sys
from pathlib import Path

import psycopg

from hs_common import storage
from hs_common.storage import keys


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--lut", required=True)
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--if-missing", action="store_true", help="do nothing if a model is already active")
    a = ap.parse_args(argv)
    import os
    db_url = a.database_url or os.environ["DATABASE_URL"]

    d = Path(a.model_dir)
    card = json.loads((d / "model_card.json").read_text())
    ver_path = d / "map_verification.json"
    verification = json.loads(ver_path.read_text()) if ver_path.exists() else {}
    version = card["version"]

    with psycopg.connect(db_url, autocommit=False) as conn:
        if a.if_missing:
            row = conn.execute("SELECT version FROM model_versions WHERE active").fetchone()
            if row:
                print(f"model {row[0]} already active; nothing to do")
                return 0

        problems = []
        if not card.get("all_gates_passed"):
            problems.append("training gates failed: " + ", ".join(
                k for k, g in card.get("gates", {}).items() if g.get("passed") is False))
        if not verification:
            problems.append("map_verification.json missing (run training/verify_maps.py)")
        elif not verification.get("all_passed"):
            problems.append("map verification failed: " + ", ".join(
                k for k, g in verification.get("gates", {}).items() if g.get("passed") is False))
        if problems and not a.force:
            print("REFUSING to promote", version, "\n  - " + "\n  - ".join(problems))
            return 2

        files = {"hydra.onnx": d / "hydra.onnx", "agri_lut.npz": Path(a.lut), "model_card.json": d / "model_card.json"}
        for extra in ("map_verification.json", "map_verification.md", "metrics.json"):
            if (d / extra).exists():
                files[extra] = d / extra
        for name, path in files.items():
            storage.put_file(keys.model(version, name), path)
            print("uploaded", keys.model(version, name))

        card["forced_promotion"] = bool(problems)
        conn.execute("UPDATE model_versions SET active = false WHERE active")
        conn.execute("""INSERT INTO model_versions (version, card, map_verification, s3_key, feature_spec_hash, active, promoted_at)
                        VALUES (%s, %s, %s, %s, %s, true, now())
                        ON CONFLICT (version) DO UPDATE SET card = EXCLUDED.card,
                          map_verification = EXCLUDED.map_verification, s3_key = EXCLUDED.s3_key,
                          feature_spec_hash = EXCLUDED.feature_spec_hash, active = true, promoted_at = now()""",
                     (version, json.dumps(card), json.dumps(verification), keys.model(version, "hydra.onnx"),
                      card["feature_spec_hash"]))
        conn.commit()
    storage.put_json("models/hydra/active.json", {"version": version})
    print(f"HydraNet {version} is now active" + (" (FORCED)" if problems else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
