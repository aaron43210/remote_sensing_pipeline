"""
Browser test of the real UI (headless Chromium, Playwright).

    docker run --rm --network host -v $PWD/tests:/t -v /tmp/shots:/shots \
      mcr.microsoft.com/playwright/python:v1.48.0-jammy python /t/ui_smoke.py http://localhost:3000 /shots

Registers, enters an area, submits a job (fixture scene), follows the live
timeline, then checks the map overlay image actually loaded, the legend and
chart rendered, every task tab works, mineral highlighting re-renders, and the
pixel inspector reads values from the COGs. Fails on any browser console error.
"""

import secrets
import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else "/tmp"
errors, failures = [], []


def wait_until(page, js, timeout=20000):
    """Poll a predicate via page.evaluate (devtools protocol): unlike
    wait_for_function it does not need 'unsafe-eval', so the app's strict CSP
    stays fully on during the test and any real violation still fails it."""
    import time
    end = time.time() + timeout / 1000
    while time.time() < end:
        if page.evaluate(js):
            return
        time.sleep(0.25)
    raise TimeoutError(f"condition not met: {js[:80]}")


def ok(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(f"{BASE}/")
    expect(page).to_have_url(f"{BASE}/login")
    ok(True, "unauthenticated user is sent to sign in")
    page.get_by_role("link", name="Create an account").click()
    page.get_by_label("Name").fill("UI Test")
    page.get_by_label("Email").fill(f"ui-{secrets.token_hex(4)}@example.com")
    page.get_by_label("Password").fill("Ui-test-passphrase-7")
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("heading", name="Make maps for an area")).to_be_visible()
    ok(True, "registered and landed on the workspace")
    page.screenshot(path=f"{SHOTS}/1-workspace.png")

    page.get_by_role("button", name="Enter coordinates").click()
    for label, v in (("West (lon)", "-119.8400"), ("South (lat)", "36.7100"), ("East (lon)", "-119.7900"), ("North (lat)", "36.7600")):
        page.get_by_label(label).fill(v)
    page.get_by_role("button", name="Apply").click()
    page.get_by_label("Imagery source").select_option("fixture")
    page.get_by_role("button", name="Make maps").click()
    page.wait_for_url("**/jobs/**", timeout=15000)
    ok(True, "job submitted, job page open")
    page.screenshot(path=f"{SHOTS}/2-running.png")

    expect(page.get_by_text("Completed", exact=True)).to_be_visible(timeout=180000)
    ok(True, "live timeline reached Completed")
    expect(page.get_by_role("tab", name="Agriculture")).to_be_visible(timeout=20000)
    wait_until(page, """() => [...document.querySelectorAll('img.leaflet-image-layer')]
        .some(i => i.complete && i.naturalWidth > 0)""", 20000)
    ok(True, "map overlay image loaded from object storage")
    ok(page.locator(".ramp").count() > 0, "continuous legend rendered")
    ok(page.locator(".chart svg path").count() > 5, "histogram rendered")
    page.screenshot(path=f"{SHOTS}/3-agriculture.png")

    page.get_by_role("tab", name="Minerals").click()
    expect(page.get_by_label("Highlight one mineral")).to_be_visible()
    ok(page.locator(".legend .item").count() > 0, "mineral legend lists classes present")
    options = page.get_by_label("Highlight one mineral").locator("option").all_inner_texts()
    if len(options) > 1:
        page.get_by_label("Highlight one mineral").select_option(index=1)
        wait_until(page, """() => [...document.querySelectorAll('img.leaflet-image-layer')]
            .some(i => i.src.startsWith('data:image/png') && i.naturalWidth > 0)""", 20000)
        ok(True, f"highlight re-rendered the class map client-side ({options[1]})")
    page.screenshot(path=f"{SHOTS}/4-minerals.png")

    page.get_by_role("tab", name="Thermal").click()
    expect(page.get_by_text("Urban heat island")).to_be_visible()
    ok(True, "thermal summary rendered")
    box = page.locator(".leaflet-container").bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
    expect(page.get_by_role("dialog", name="Pixel values")).to_be_visible()
    wait_until(page, """() => { const d = document.querySelector('[aria-label="Pixel values"]');
        return d && d.querySelectorAll('td.r').length > 3 && !d.innerText.includes('Reading values'); }""", 20000)
    cells = page.locator('[aria-label="Pixel values"] td.r').all_inner_texts()
    ok(sum(1 for c in cells if c not in ("–", "unavailable")) >= 3, f"pixel inspector read values: {cells[:6]}")
    page.screenshot(path=f"{SHOTS}/5-thermal-inspector.png")

    page.get_by_role("link", name="Jobs").click()
    expect(page.get_by_role("heading", name="Your jobs")).to_be_visible()
    expect(page.locator("table.data tbody tr").first).to_be_visible(timeout=15000)
    ok(page.locator("table.data tbody tr").count() >= 1, "job history lists the job")
    page.get_by_role("link", name="Platform").click()
    expect(page.get_by_text("Map verification", exact=False)).to_be_visible(timeout=15000)
    ok(True, "platform page shows the model's verification gates")
    page.screenshot(path=f"{SHOTS}/6-platform.png", full_page=True)
    browser.close()

relevant = [e for e in errors if "favicon" not in e]
ok(not relevant, f"no browser console errors {relevant[:3]}")
print("\nPASSED" if not failures else f"\nFAILED: {failures}")
sys.exit(1 if failures else 0)
