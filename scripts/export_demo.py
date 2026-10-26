"""
Builds the read-only demo (the Hugging Face Space) from REAL finished jobs.

Reads the jobs through the public API as the admin (signed in with the test IdP:
docker compose --profile test, ADMIN_EMAIL=admin@example.com), downloads every
layer's PNG and COG, rewrites their presigned URLs to static paths, then builds
the frontend with VITE_DEMO=1 around them.

    python scripts/export_demo.py --base http://localhost:3000 --admin admin@example.com \
        --jobs <job_id>,<job_id> --out docs/hf_space/build

Nothing secret is exported: no tokens, no user e-mails (owner fields are dropped).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from oidc_login import login  # noqa: E402

DROP = {"user_id", "email", "owner", "user", "idempotency_key", "reuse_key", "error_detail"}


def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if k not in DROP}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--admin", default="admin@example.com", help="ADMIN_EMAIL of the running stack")
    ap.add_argument("--jobs", required=True, help="comma-separated ids of finished real (source=nasa) jobs")
    ap.add_argument("--out", default=str(ROOT / "docs/hf_space/build"))
    a = ap.parse_args()
    base, out = a.base.rstrip("/"), Path(a.out).resolve()
    s, token, user = login(base, a.admin)
    if user.get("role") != "admin":
        sys.exit(f"{a.admin} is not admin on this stack (set ADMIN_EMAIL)")
    H = {"Authorization": f"Bearer {token}"}

    def get(path):
        r = s.get(f"{base}/api{path}", headers=H, timeout=60)
        r.raise_for_status()
        return r.json()

    if out.exists():
        shutil.rmtree(out)
    demo = out / "demo"
    (demo / "jobs").mkdir(parents=True)
    listing = []
    for jid in [j.strip() for j in a.jobs.split(",") if j.strip()]:
        job = get(f"/jobs/{jid}")
        if job.get("status") not in ("COMPLETED", "PARTIAL") or job.get("source") != "nasa":
            sys.exit(f"{jid}: only finished jobs on real NASA imagery belong in the demo ({job.get('status')}, {job.get('source')})")
        layers = get(f"/jobs/{jid}/layers")
        files = demo / "files" / jid
        files.mkdir(parents=True)
        for l in layers["layers"]:
            for key, ext in (("png_url", "png"), ("tif_url", "tif")):
                if not l.get(key):
                    continue
                name = re.sub(r"[^\w.-]", "_", f"{l['task']}-{l['name']}.{ext}")
                r = requests.get(l[key], timeout=120)
                r.raise_for_status()
                (files / name).write_bytes(r.content)
                l[key] = f"/demo/files/{jid}/{name}"
        (demo / "jobs" / f"{jid}.json").write_text(json.dumps(clean(job)))
        (demo / "jobs" / f"{jid}.layers.json").write_text(json.dumps(clean(layers)))
        listing.append({k: job.get(k) for k in ("job_id", "status", "bbox", "tasks", "source", "created_at",
                                                "completed_at", "final")})
        print(f"  {jid}: {len(layers['layers'])} layers")
    (demo / "jobs.json").write_text(json.dumps({"jobs": listing}))
    cfg = get("/config")
    cfg["allow_fixtures"] = False
    (demo / "config.json").write_text(json.dumps(cfg))
    (demo / "model.json").write_text(json.dumps(get("/model")))
    (demo / "coverage.json").write_text(json.dumps(get("/coverage")))
    leftovers = [p for p in demo.rglob("*.json") if re.search(r"X-Amz-|Signature=|localhost:9000", p.read_text())]
    if leftovers:
        sys.exit(f"presigned URLs left in {leftovers}")

    # the app itself, demo build, around the exported data
    subprocess.run(["npm", "run", "build", "--", "--outDir", str(out), "--emptyOutDir", "false"],
                   cwd=ROOT / "frontend", check=True, env={**os.environ, "VITE_DEMO": "1"})
    shutil.copy(ROOT / "docs/hf_space/README.md", out / "README.md")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    print(f"demo built in {out} ({size:.1f} MB): python scripts/publish_hf.py --space-dir {out}")


if __name__ == "__main__":
    main()
