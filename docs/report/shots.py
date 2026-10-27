"""
Screenshots of the running app for the project document (Playwright, CSP on).

    docker run --rm --network host -v $PWD:/w mcr.microsoft.com/playwright/python:v1.48.0-jammy \
        sh -c "pip install -q playwright==1.48.0 && python /w/docs/report/shots.py \
               http://localhost:3000 /w/<job_fresno.json> /w/<job_deathvalley.json> /w/docs/report/build/img"

Each job JSON holds {"job": {...}, "email": ...} of a finished real job. Sign-in goes
through the test IdP (docker compose --profile test), which signs in "email".
"""
import json
import sys
import time

from playwright.sync_api import expect, sync_playwright

BASE, FRESNO, DV, OUT = sys.argv[1], json.load(open(sys.argv[2])), json.load(open(sys.argv[3])), sys.argv[4]
errors = []


def login(page, info):
    def hint(route):   # Playwright does not route redirect hops: add login_hint to the gateway's redirect itself
        resp = route.fetch(max_redirects=0)
        loc = resp.headers.get("location")
        if not loc:   # e.g. 429 from the sign-in rate limit: pass the response through unchanged
            return route.fulfill(response=resp)
        route.fulfill(response=resp, headers={**resp.headers, "location": loc + ("&" if "?" in loc else "?") + f"login_hint={info['email']}"})
    page.unroute("**/api/auth/google/login*")
    page.route("**/api/auth/google/login*", hint)
    page.goto(f"{BASE}/login")
    page.get_by_role("link", name="Sign in with Google").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=20000)


def enter_box(page, bbox):
    page.get_by_role("button", name="Enter coordinates").click()
    for label, v in zip(("West (lon)", "South (lat)", "East (lon)", "North (lat)"), bbox):
        page.get_by_label(label).fill(f"{v:.4f}")
    page.get_by_role("button", name="Apply").click()
    time.sleep(2.5)


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1440, "height": 860}, device_scale_factor=1.5)
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(f"{BASE}/login")
    page.screenshot(path=f"{OUT}/ui_login.png")
    login(page, FRESNO)
    expect(page.locator(".coverage-legend")).to_be_visible(timeout=20000)
    time.sleep(3)
    page.screenshot(path=f"{OUT}/ui_coverage.png")                      # EMIT coverage, last 12 months
    page.get_by_label("From").fill("2023-06-01")
    page.get_by_label("To").fill("2023-09-30")
    time.sleep(2)
    enter_box(page, FRESNO["job"]["bbox"])
    page.screenshot(path=f"{OUT}/ui_workspace.png")                     # box check: scene covers the area

    page.goto(f"{BASE}/jobs/{FRESNO['job']['job_id']}")
    expect(page.get_by_role("tab", name="Agriculture")).to_be_visible(timeout=30000)
    time.sleep(5)
    page.screenshot(path=f"{OUT}/ui_agriculture.png")
    page.locator("summary", has_text="Checked against").click()
    time.sleep(1)
    page.screenshot(path=f"{OUT}/ui_checks.png", clip={"x": 0, "y": 0, "width": 440, "height": 860})
    for tab, name in (("Minerals", "ui_minerals.png"), ("Thermal", "ui_thermal.png")):
        page.get_by_role("tab", name=tab).click()
        time.sleep(4)
        page.screenshot(path=f"{OUT}/{name}")
    box = page.locator(".leaflet-container").bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.5)
    time.sleep(4)
    page.screenshot(path=f"{OUT}/ui_inspector.png")

    page.get_by_role("button", name="Sign out").click()
    time.sleep(2)
    login(page, DV)
    page.goto(f"{BASE}/jobs/{DV['job']['job_id']}")
    expect(page.get_by_role("tab", name="Minerals")).to_be_visible(timeout=30000)
    time.sleep(5)
    page.screenshot(path=f"{OUT}/ui_deathvalley.png")
    page.get_by_role("link", name="Jobs").click()
    time.sleep(2)
    page.screenshot(path=f"{OUT}/ui_history.png")
    page.get_by_role("link", name="Platform").click()
    time.sleep(3)
    page.screenshot(path=f"{OUT}/ui_platform.png", full_page=True)
    b.close()
print("console errors:", errors)
