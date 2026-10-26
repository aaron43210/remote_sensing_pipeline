"""
Publishes HydraNet to Hugging Face: the model repo (ONNX + LUT + card + verification)
and, optionally, the read-only demo Space built by scripts/export_demo.py.

    pip install "huggingface_hub>=1.0" onnx
    HF_TOKEN=hf_... python scripts/publish_hf.py --model-dir training/artifacts/models/v1 \
        [--space-dir docs/hf_space/build] [--dry-run]

The token is read from HF_TOKEN only (a write token from huggingface.co/settings/tokens);
it is never written anywhere. Every file is re-checked first: the ONNX must carry the
card's feature-spec hash, all release gates must have passed, and the upload replaces
the repo's files exactly (stale files such as old models or source maps are deleted).
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_REPO = "aaronr43210/HydraNet-Hyperspectral"
SPACE_REPO = "aaronr43210/HydraNet-Maps"
MODEL_FILES = ["hydra.onnx", "model_card.json", "metrics.json", "map_verification.json", "map_verification.md"]


def check_model(d):
    import onnx
    card = json.loads((d / "model_card.json").read_text())
    meta = {p.key: p.value for p in onnx.load(str(d / "hydra.onnx"), load_external_data=False).metadata_props}
    problems = []
    if meta.get("feature_spec_hash") != card["feature_spec_hash"]:
        problems.append(f"ONNX spec hash {meta.get('feature_spec_hash')} != card {card['feature_spec_hash']}")
    if meta.get("model_version") not in (None, card["version"]):
        problems.append(f"ONNX version {meta.get('model_version')} != card {card['version']}")
    if not card.get("all_gates_passed"):
        problems.append("not every release gate passed")
    if problems:
        sys.exit("refusing to publish: " + "; ".join(problems))
    return card


def model_readme(card, verification_md):
    g = card["gates"]
    pct = lambda k: f"{g[k]['value'] * 100:.2f} %"   # noqa: E731
    return f"""---
library_name: onnx
tags: [remote-sensing, hyperspectral, emit, landsat, agriculture, geology, onnx]
pipeline_tag: tabular-regression
---

# HydraNet {card['version']}: one small network for crop traits, minerals and surface heat

HydraNet reads NASA **EMIT** imaging-spectrometer pixels (and **Landsat 8/9** land surface
temperature) and produces, in one pass, what three classical methods produce separately:

| Head | Output | Reproduces (teacher) |
|---|---|---|
| Agriculture | chlorophyll (Cab), leaf water (Cw), leaf area index (LAI) | PROSAIL look-up-table inversion, k = 20 |
| Minerals | dominant mineral (12 USGS classes + unknown), abundances, fit quality | SAM + SMA/FCLS unmixing |
| Thermal | heat classes, urban heat island zones, local anomalies, hotspots | statistical rules (z-scores, percentiles) |

* **Format:** ONNX opset {card['opset']}, {card['bytes'] / 1e6:.2f} MB, {card['parameters']:,} parameters (MLP)
* **Input contract:** feature-spec hash `{card['feature_spec_hash']}` (530 mineral features: continuum-removed
  and raw reflectance, absorption depths, SAM angles, brightness). The serving code refuses a model whose
  hash differs from its feature code.
* **Agreement with the classical methods:** mineral class {pct('mineral_real_holdout.class_agreement')} of pixels on a
  held-out real EMIT scene (Death Valley); abundance error {g['mineral_real_holdout.abundance_mae']['value']:.4f};
  crop-trait R² ≥ {g['agri_val.r2_min_core']['value']:.3f}; thermal classes ≥ {pct('thermal_val.uhi_agreement')}.

```python
import numpy as np, onnxruntime as ort
sess = ort.InferenceSession("hydra.onnx")
print([(i.name, i.shape) for i in sess.get_inputs()])
```

Building the inputs from an EMIT cube (and the shadow check against the classical methods) is in
`libs/hydra` of the [source repository](https://github.com/aaron43210/remote_sensing_pipeline);
`agri_lut.npz` is the PROSAIL look-up table the agriculture check uses.

**Try it:** [HydraNet Maps demo](https://huggingface.co/spaces/{SPACE_REPO}) (real maps, no sign-in).

## Limitations

Trained for California EMIT scenes and the twelve USGS minerals listed in the card. Pixels outside
that domain (water, salt-lake crusts, snow) are not reliable; the platform masks water and cloud
before the network runs.

## Verification on maps

{verification_md}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(ROOT / "training/artifacts/models/v1"))
    ap.add_argument("--lut", default=str(ROOT / "training/artifacts/data/agri_lut.npz"))
    ap.add_argument("--space-dir", help="output of scripts/export_demo.py")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    d = Path(a.model_dir)
    card = check_model(d)
    readme = model_readme(card, (d / "map_verification.md").read_text())
    print(f"model {card['version']} ({card['feature_spec_hash']}) verified")
    if a.space_dir:
        sd = Path(a.space_dir)
        maps = list(sd.rglob("*.map"))
        if maps or not (sd / "index.html").exists() or not (sd / "demo" / "jobs.json").exists():
            sys.exit(f"{sd} is not a clean demo build (source maps: {len(maps)}); run scripts/export_demo.py")
    if a.dry_run:
        print(readme[:600], "...\n(dry run: nothing uploaded)")
        return
    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("set HF_TOKEN (a write token) in the environment")
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi
    api = HfApi(token=token)
    keep = set(MODEL_FILES) | {"agri_lut.npz", "README.md", ".gitattributes"}
    ops = [CommitOperationAdd(path_in_repo=f, path_or_fileobj=str(d / f)) for f in MODEL_FILES]
    ops += [CommitOperationAdd(path_in_repo="agri_lut.npz", path_or_fileobj=a.lut),
            CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=readme.encode())]
    ops += [CommitOperationDelete(path_in_repo=f) for f in api.list_repo_files(MODEL_REPO) if f not in keep]
    api.create_commit(MODEL_REPO, ops, commit_message=f"HydraNet {card['version']} ({card['feature_spec_hash']})")
    print(f"published https://huggingface.co/{MODEL_REPO}")
    if a.space_dir:
        api.upload_folder(repo_id=SPACE_REPO, repo_type="space", folder_path=a.space_dir,
                          delete_patterns=["*"], commit_message=f"demo for HydraNet {card['version']}")
        print(f"published https://huggingface.co/spaces/{SPACE_REPO}")


if __name__ == "__main__":
    main()
