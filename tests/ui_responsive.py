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
    # against the DEVICE width: a too-wide page makes mobile Chrome zoom out, so innerWidth grows with it
    over = page.evaluate("Math.max(document.documentElement.scrollWidth, window.innerWidth)") - dev["width"]
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


def check_topbar_one_line(page, dev, route):
    if route == "/login":
        return
    r = page.evaluate("""(W) => { const t = document.querySelector('.topbar').getBoundingClientRect();
        const out = [...document.querySelectorAll('.topbar a, .topbar button')].filter(e => e.offsetParent)
          .filter(e => { const b = e.getBoundingClientRect(); return b.right > W + 0.5 || b.left < -0.5 || b.bottom > t.bottom + 0.5 })
          .map(e => e.textContent.trim() || e.getAttribute('aria-label'));
        const wide = document.querySelector('.topbar').scrollWidth - document.querySelector('.topbar').clientWidth;
        return { h: t.height, out, wide } }""", dev["width"])
    ok(r["h"] <= 56 and not r["out"] and r["wide"] <= 0,
       f"topbar_one_line[{dev['name']}] {route}: height {r['h']:.0f}px, cut off {r['out']}, {r['wide']}px hidden")


def check_nothing_clipped(page, dev, route):
    """Overflow hidden inside the layout (not just the page): nothing wider than the screen."""
    wide = page.evaluate("""(W) => [...document.querySelectorAll('.topbar, .sidebar, .page, .card, table.data')]
        .filter(e => e.offsetParent && e.getBoundingClientRect().right > W + 1 && !e.closest('.table-wrap'))
        .map(e => e.className.toString().split(' ')[0])""", dev["width"])
    ok(not wide, f"nothing_clipped[{dev['name']}] {route}: {wide[:5]}")


ROUTE_CHECKS = [check_no_horizontal_overflow, check_nothing_clipped, check_topbar_one_line, check_tap_targets]


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


# ── device checks (run once per device, after the page checks) ───────────────
def touch_drag(page, x0, y0, x1, y1, steps=6):
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
    for i in range(1, steps + 1):
        x, y = x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
        page.wait_for_timeout(30)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


def touch_draw_makes_box(page, dev, job):
    if dev["name"] not in ("390x844", "768x1024", "1440x900"):
        return
    go(page, "/", job)
    page.get_by_role("button", name="Draw area").click()
    page.wait_for_timeout(300)
    m = page.locator(".leaflet-container")
    m.scroll_into_view_if_needed()
    b = m.bounding_box()
    summary = page.locator("form p.secondary.num", has_text="km²")   # the form's box summary
    text = lambda: summary.first.inner_text() if summary.count() else ""
    had_box = text()
    before = page.evaluate("document.querySelector('.leaflet-map-pane').style.transform")
    pts = (b["x"] + b["width"] * 0.4, b["y"] + b["height"] * 0.4, b["x"] + b["width"] * 0.6, b["y"] + b["height"] * 0.6)
    if dev["touch"]:
        touch_drag(page, *pts)
    else:   # desktop: the same drawing with a mouse must keep working
        page.mouse.move(pts[0], pts[1]); page.mouse.down(); page.mouse.move(pts[2], pts[3], steps=6); page.mouse.up()
    page.wait_for_timeout(500)
    after = page.evaluate("document.querySelector('.leaflet-map-pane').style.transform")
    boxed = text() not in ("", had_box)
    ok(boxed and before == after, f"touch_draw_makes_box[{dev['name']}]: new box drawn {boxed}, map panned {before != after}")


DEVICE_CHECKS = [touch_draw_makes_box]


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


EMAIL = f"responsive-tester-with-a-long-name-{secrets.token_hex(3)}@example.com"   # long names must fit too


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
            for check in DEVICE_CHECKS:
                try:
                    check(page, dev, job)
                except Exception as e:
                    ok(False, f"{check.__name__}[{dev['name']}]: {str(e).splitlines()[0][:120]}")
            ctx.close()
        browser.close()
    print(f"\n{'PASSED' if not failures else 'FAILED'}: {len(failures)} failure(s)")
    for f in failures:
        print("  -", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
