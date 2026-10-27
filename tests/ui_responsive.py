"""
Responsive layout test: every page on 8 screen sizes, touch and mouse.

    docker run --rm --network host -v $PWD/tests:/t mcr.microsoft.com/playwright/python:v1.48.0-jammy \
      sh -c "pip install -q playwright==1.48.0 requests && python /t/ui_responsive.py http://localhost:3000"

Needs the stack with the test sign-in (docker compose --profile local --profile test, ALLOW_FIXTURES=true).
Signs in once, seeds one finished fixture job, then opens each device once and moves between pages
by tapping the app's own links (each full page load costs a rate-limited /api/auth/refresh).
"""

import secrets
import sys
import time

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000").rstrip("/")
DEVICES = [
    {"name": "320x568", "width": 320, "height": 568, "touch": True, "mobile": True},
    {"name": "360x740", "width": 360, "height": 740, "touch": True, "mobile": True},
    {"name": "390x844", "width": 390, "height": 844, "touch": True, "mobile": True},
    {"name": "844x390", "width": 844, "height": 390, "touch": True, "mobile": True},   # landscape phone
    {"name": "768x1024", "width": 768, "height": 1024, "touch": True, "mobile": True},
    {"name": "1024x768", "width": 1024, "height": 768, "touch": True, "mobile": True},
    {"name": "1440x900", "width": 1440, "height": 900, "touch": False, "mobile": False},
    {"name": "2560x1440", "width": 2560, "height": 1440, "touch": False, "mobile": False},
]
failures, _loads = [], []


def ok(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def throttle(n=1):
    """Sign-in routes (login, callback, refresh) share a 20/min per-IP limit: reserve n calls under it."""
    for _ in range(n):
        now = time.time()
        while len([t for t in _loads if now - t < 60]) >= 17:
            time.sleep(1)
            now = time.time()
        _loads.append(now)


def wait_until(page, js, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(js):
            return True
        time.sleep(0.25)
    return False


# ── checks (each takes page, dev, route) ─────────────────────────────────────
def check_no_horizontal_overflow(page, dev, route):
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    ok(over <= 0, f"no_horizontal_overflow[{dev['name']}] {route}: {over}px too wide")


TAPPABLE = ("[...document.querySelectorAll('button, a.btn, .tabs button, select, .chip, .nav a, "
            "input:not([type=checkbox]):not([type=radio]):not([type=hidden])')]"
            ".filter(e => e.offsetParent && !(e.closest('.chip') && !e.classList.contains('chip')) && !e.classList.contains('link'))")


def check_tap_targets(page, dev, route):
    if not dev["touch"]:
        return
    small = page.evaluate(f"""() => {TAPPABLE}.map(e => [e.getBoundingClientRect(), e.textContent.trim() || e.getAttribute('aria-label') || e.tagName])
        .filter(([r]) => r.width < 44 || r.height < 44).map(([r, n]) => `${{n.slice(0, 18)}} ${{Math.round(r.width)}}x${{Math.round(r.height)}}`)""")
    ok(not small, f"tap_targets_44px[{dev['name']}] {route}: {len(small)} too small {small[:4]}")


ROUTE_CHECKS = [check_no_horizontal_overflow, check_tap_targets]


def open_app(page, dev):
    """Full load of the app; returns False (and records why) if the map does not appear."""
    throttle()
    page.goto(f"{BASE}/")
    try:
        page.wait_for_selector(".leaflet-container", state="attached", timeout=30000)
        return True
    except Exception:
        page.screenshot(path=f"/t/../tmp-{dev['name']}.png")
        ok(False, f"app_loads[{dev['name']}]: no map at {page.url}")
        return False


def go(page, route, job):
    """Move inside the app by tapping its own links (no full page load)."""
    if route == "/":
        page.locator(".nav a").nth(0).click()
        page.wait_for_selector(".leaflet-container", state="attached")
    elif route == "/jobs":
        page.locator(".nav a").nth(1).click()
        page.wait_for_selector("table.data")
    elif route == "/system":
        page.locator(".nav a").nth(2).click()
        page.wait_for_selector("table.data")
    elif route == "/jobs/{job}":
        page.locator(".nav a").nth(1).click()
        page.locator(f"a[href='/jobs/{job}']").click()
        page.get_by_role("tab", name="Thermal").wait_for(timeout=30000)
    page.wait_for_timeout(600)


EMAIL = f"resp-{secrets.token_hex(4)}@example.com"


def sign_in(page):
    """Sign in through the test IdP, as a person would (refresh tokens rotate: every context signs in itself)."""
    page.route("**/authorize?*", lambda r: r.continue_(url=r.request.url + f"&login_hint={EMAIL}"))
    throttle(4)   # login page refresh, google/login, callback, refresh after landing
    page.goto(f"{BASE}/login")
    page.get_by_role("link", name="Sign in with Google").click()
    page.wait_for_selector(".leaflet-container", state="attached", timeout=30000)


def seed(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    sign_in(page)
    throttle()
    tok = page.request.post(f"{BASE}/api/auth/refresh", headers={"X-Requested-With": "hydranet"}).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    job = page.request.post(f"{BASE}/api/jobs", headers=H, data={"bbox": [-119.84, 36.71, -119.79, 36.76],
                            "tasks": ["agriculture", "mineral", "thermal"], "source": "fixture"}).json()["job_id"]
    end = time.time() + 240
    while time.time() < end and not page.request.get(f"{BASE}/api/jobs/{job}", headers=H).json().get("final"):
        time.sleep(2)
    ctx.close()
    return job


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        job = seed(browser)
        print(f"seeded job {job}")
        for dev in DEVICES:
            print(f"== {dev['name']} ({'touch' if dev['touch'] else 'mouse'})")
            opts = {"viewport": {"width": dev["width"], "height": dev["height"]}, "has_touch": dev["touch"], "is_mobile": dev["mobile"]}
            anon = browser.new_context(**opts)
            page = anon.new_page()
            throttle()
            page.goto(f"{BASE}/login")
            page.wait_for_selector(".auth .card")
            for check in ROUTE_CHECKS:
                check(page, dev, "/login")
            anon.close()
            ctx = browser.new_context(**opts)
            page = ctx.new_page()
            try:
                sign_in(page)
            except Exception as e:
                ok(False, f"app_loads[{dev['name']}]: sign-in failed: {str(e).splitlines()[0][:100]}")
                ctx.close()
                continue
            for route in ["/", "/jobs", "/system", "/jobs/{job}"]:
                try:
                    go(page, route, job)
                except Exception as e:   # a page the user cannot reach is a failure, not a crash
                    ok(False, f"reachable[{dev['name']}] {route}: {str(e).splitlines()[0][:120]}")
                    if not open_app(page, dev):   # recover for the next route
                        break
                    continue
                for check in ROUTE_CHECKS:
                    check(page, dev, route)
            ctx.close()
        browser.close()
    print(f"\n{'PASSED' if not failures else 'FAILED'}: {len(failures)} failure(s)")
    for f in failures:
        print("  -", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
