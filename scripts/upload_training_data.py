"""
Version the training data in the lake's Gold layer.

    python scripts/upload_training_data.py --raw-root .. --generated training/artifacts/data --version v1

    gold/training/raw/<DOMAIN>/<file>          the files as delivered (AGRICULTURE, MINERAL, THERMAL)
    gold/training/real_scenes/<granule>.npz    real EMIT pixels cleaned by production preprocessing
    gold/training/<version>/<name>.npz         the teacher-labelled sets datagen.py produced
    gold/training/<version>/manifest.json      sizes, sha256 of every file, spec hash
"""

import argparse
import hashlib
import json
from pathlib import Path

from hs_common import storage


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", required=True)
    ap.add_argument("--generated", required=True)
    ap.add_argument("--version", required=True)
    a = ap.parse_args(argv)
    files = {}
    for domain in ("AGRICULTURE", "MINERAL", "THERMAL"):
        for p in sorted((Path(a.raw_root) / domain).glob("*")):
            if p.is_file():
                key = f"gold/training/raw/{domain}/{p.name}"
                storage.put_file(key, p)
                files[key] = {"bytes": p.stat().st_size, "sha256": sha256(p)}
    gen = Path(a.generated)
    for p in sorted((gen / "real_scenes").glob("*.npz")):     # harvested EMIT pixels (harvest_emit.py)
        key = f"gold/training/real_scenes/{p.name}"
        storage.put_file(key, p)
        files[key] = {"bytes": p.stat().st_size, "sha256": sha256(p)}
    for p in sorted(gen.glob("*.npz")):
        key = f"gold/training/{a.version}/{p.name}"
        storage.put_file(key, p)
        files[key] = {"bytes": p.stat().st_size, "sha256": sha256(p)}
    manifest = json.loads((gen / "manifest.json").read_text()) if (gen / "manifest.json").exists() else {}
    manifest["files"] = files
    storage.put_json(f"gold/training/{a.version}/manifest.json", manifest)
    print(f"uploaded {len(files)} files; manifest at gold/training/{a.version}/manifest.json")


if __name__ == "__main__":
    main()
