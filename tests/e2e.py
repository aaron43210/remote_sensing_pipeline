"""
End-to-end test against a running stack, through the same public API the UI uses.

    python tests/e2e.py --base http://localhost:3000 [--jobs 3] [--real-thermal]

Checks: register/login, validation errors, job submission with an idempotency
key, live status to a final state, every layer's PNG and COG downloadable via
its presigned URL, shadow verification present and within the gates, result
reuse for an identical request, cancel, auth isolation between users, and
(optionally) a thermal job on REAL Landsat imagery. Exits non-zero on failure.
"""

import argparse
import concurrent.futures as cf
import json
import secrets
import sys
import time
import urllib.error
import urllib.request

FIXTURE_BOXES = [[-119.85, 36.70, -119.80, 36.75], [-119.40, 36.15, -119.35, 36.20],
                 [-121.05, 37.60, -121.00, 37.65], [-119.10, 35.35, -119.05, 35.40],
                 [-121.55, 38.52, -121.50, 38.57], [-120.50, 36.90, -120.45, 36.95]]
failures = []


def call(base, method, path, token=None, body=None, headers=None, expect=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, method=method, headers=h,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code, data = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, data = e.code, e.read()
    try:
        data = json.loads(data) if data else None
    except ValueError:
        pass
    if expect is not None and code != expect:
        raise AssertionError(f"{method} {path}: expected {expect}, got {code}: {data}")
    return code, data


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def user(base):
    email = f"e2e-{secrets.token_hex(4)}@example.com"
    _, d = call(base, "POST", "/api/auth/register", body={"email": email, "password": "correct horse 42", "name": "E2E"},
                expect=201)
    return d["token"]


def wait(base, token, job_id, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, j = call(base, "GET", f"/api/jobs/{job_id}", token, expect=200)
        if j["final"]:
            return j, time.time() - t0
        time.sleep(2)
    raise TimeoutError(f"{job_id} not finished after {timeout}s")


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.status, r.read(16)


def verify_job(base, token, job, label):
    _, data = call(base, "GET", f"/api/jobs/{job['job_id']}/layers", token, expect=200)
    layers = data["layers"]
    check(len(layers) > 0, f"{label}: {len(layers)} layers")
    tasks = {l["task"] for l in layers}
    check(tasks == set(job["tasks"]), f"{label}: layers for every task {sorted(tasks)}")
    sample = [l for l in layers if l["name"] in ("mineral_class", "agri_LAI", "thermal_lst", "thermal_uhi_class")] or layers[:3]
    for l in sample:
        code, head = fetch(l["png_url"])
        check(code == 200 and head.startswith(b"\x89PNG"), f"{label}: PNG {l['name']} downloadable")
        code, head = fetch(l["tif_url"])
        check(code == 200 and head[:2] in (b"II", b"MM"), f"{label}: COG {l['name']} downloadable")
    for task, res in data["results"].items():
        ver = res.get("verification") or {}
        check(bool(ver), f"{label}: {task} shadow verification present")
        for layer, m in ver.items():
            if layer in ("mineral_class", "thermal_global_class", "thermal_uhi_class"):
                check(m["pixel_agreement"] >= 0.95, f"{label}: {task}/{layer} agrees with classical ({m['pixel_agreement']:.4f})")
            elif layer in ("agri_Cab", "agri_Cw", "agri_LAI", "mineral_abundance"):
                # the product's rule: error <= 5 % of the value's range (R² is reported, not judged:
                # it collapses on areas whose values barely vary)
                check(m["nmae"] <= 0.05, f"{label}: {task}/{layer} agrees with classical "
                                         f"(error {m['nmae'] * 100:.2f} % of range, R² {m.get('r2')})")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--real-thermal", action="store_true")
    a = ap.parse_args()
    base = a.base.rstrip("/")

    print("== health, config, auth")
    _, cfg = call(base, "GET", "/api/config", expect=200)
    check(cfg["max_bbox_deg"] > 0, "config served")
    _, model = call(base, "GET", "/api/model", expect=200)
    check(model.get("active"), f"active model {model.get('version')}")
    tok = user(base)
    call(base, "GET", "/api/me", tok, expect=200)
    code, _ = call(base, "GET", "/api/jobs", None)
    check(code == 401, "API refuses requests without a token")
    code, _ = call(base, "POST", "/api/auth/login", body={"email": "nobody@example.com", "password": "wrongpass1"})
    check(code == 401, "wrong credentials rejected")

    print("== EMIT coverage (map layer)")
    code, _ = call(base, "GET", "/api/coverage", None)
    check(code == 401, "coverage needs a token")
    code, cov = call(base, "GET", "/api/coverage?date_from=2023-06-01&date_to=2023-09-30", tok)
    check(code == 200 and cov.get("type") == "FeatureCollection", f"coverage served: {len(cov.get('features', []))} scenes")
    check(all(f["properties"].get("cloud_pct") is None or f["properties"]["cloud_pct"] <= cov["max_cloud_pct"]
              for f in cov.get("features", [])), "only scenes a job could use (cloud limit)")
    code, _ = call(base, "GET", "/api/coverage?date_from=2023-13-01", tok)
    check(code == 400, "bad coverage dates rejected")

    print("== validation")
    for body, why in [({"bbox": [-119, 36, -118, 37], "tasks": ["mineral"]}, "too large"),
                      ({"bbox": [-119, 36, -119.05, 36.05], "tasks": ["mineral"]}, "inverted"),
                      ({"bbox": [10, 10, 10.05, 10.05], "tasks": ["mineral"]}, "outside region"),
                      ({"bbox": [-119.85, 36.70, -119.80, 36.75], "tasks": ["weather"]}, "unknown task")]:
        code, d = call(base, "POST", "/api/jobs", tok, body=body)
        check(code == 400, f"rejects {why}: {d.get('error') if isinstance(d, dict) else d}")

    print(f"== {a.jobs} concurrent fixture jobs (all three maps)")
    tokens = [user(base) for _ in range(a.jobs)]
    jobs = []
    for i in range(a.jobs):
        body = {"bbox": FIXTURE_BOXES[i % len(FIXTURE_BOXES)], "tasks": ["agriculture", "mineral", "thermal"],
                "source": "fixture"}
        key = secrets.token_hex(8)
        _, j1 = call(base, "POST", "/api/jobs", tokens[i], body=body, headers={"Idempotency-Key": key}, expect=202)
        code, j2 = call(base, "POST", "/api/jobs", tokens[i], body=body, headers={"Idempotency-Key": key})
        check(code == 200 and j2["job_id"] == j1["job_id"], f"job {i}: idempotent resubmit returns the same job")
        jobs.append((tokens[i], j1, body))
    code, _ = call(base, "GET", f"/api/jobs/{jobs[0][1]['job_id']}", tokens[1] if a.jobs > 1 else user(base))
    check(code == 404, "a user cannot read another user's job")

    with cf.ThreadPoolExecutor(len(jobs)) as ex:
        done = list(ex.map(lambda t: wait(base, t[0], t[1]["job_id"], a.timeout), jobs))
    for i, ((token, j, body), (final, secs)) in enumerate(zip(jobs, done)):
        check(final["status"] == "COMPLETED", f"job {i}: {final['status']} in {secs:.0f}s "
                                             f"{[(t['task'], t['state'], t['message']) for t in final['task_states']]}")
        if final["status"] == "COMPLETED":
            verify_job(base, token, final, f"job {i}")

    print("== reuse of an identical request")
    token, _, body = jobs[0]
    _, again = call(base, "POST", "/api/jobs", token, body=body, expect=202)
    check(again["status"] == "COMPLETED" and again["reused_from"], f"identical request served from cache ({again['status']})")

    print("== cancel")
    _, jc = call(base, "POST", "/api/jobs", tok, body={"bbox": FIXTURE_BOXES[5], "tasks": ["mineral"], "source": "fixture",
                                                        "date_from": "2024-01-01"}, expect=202)
    code, jcc = call(base, "DELETE", f"/api/jobs/{jc['job_id']}", tok)
    check(code in (200, 409), f"cancel answered {code} ({(jcc or {}).get('status')})")

    if a.real_thermal:
        print("== REAL Landsat thermal job (Planetary Computer, no credentials needed)")
        _, jr = call(base, "POST", "/api/jobs", tok, body={"bbox": [-119.85, 36.70, -119.75, 36.78], "tasks": ["thermal"],
                                                            "date_from": "2024-06-01", "date_to": "2024-09-30"}, expect=202)
        final, secs = wait(base, tok, jr["job_id"], a.timeout)
        check(final["status"] == "COMPLETED", f"real thermal: {final['status']} in {secs:.0f}s, acquired {final['acquired_landsat']}")
        if final["status"] == "COMPLETED":
            verify_job(base, tok, final, "real thermal")

    print(f"\n{'PASSED' if not failures else 'FAILED'}: {len(failures)} failure(s)")
    for f in failures:
        print("  -", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
