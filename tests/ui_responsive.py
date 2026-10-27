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


def check_tables_scroll_inside(page, dev, route):
    if route not in ("/jobs", "/system") or dev["width"] > 640:
        return
    loose = page.evaluate("[...document.querySelectorAll('table.data')].filter(t => t.offsetParent && !t.closest('.table-wrap')).length")
    ok(loose == 0, f"tables_scroll_inside[{dev['name']}] {route}: {loose} table(s) outside .table-wrap")


ROUTE_CHECKS = [check_tables_scroll_inside, check_no_horizontal_overflow, check_nothing_clipped, check_topbar_one_line, check_tap_targets]


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
PANE = "document.querySelector('.leaflet-map-pane').style.transform"


def touch_drag(page, x0, y0, x1, y1, steps=6):
    """One-finger drag; returns the map pane transform just before the finger lifts."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
    for i in range(1, steps + 1):
        x, y = x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
        page.wait_for_timeout(30)
    mid = page.evaluate(PANE)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()
    return mid


def open_sheet(page):
    """On phones the form lives in the bottom sheet: open it first, as a person would."""
    h = page.locator(".sheet-handle")
    if h.count() and h.get_attribute("aria-expanded") != "true":
        h.tap(); page.wait_for_timeout(400)


def touch_draw_makes_box(page, dev, job):
    if dev["name"] not in ("390x844", "768x1024", "1440x900"):
        return
    go(page, "/", job)
    open_sheet(page)
    page.get_by_role("button", name="Draw area").click()
    page.wait_for_timeout(300)
    m = page.locator(".leaflet-container")
    m.scroll_into_view_if_needed()
    b = m.bounding_box()
    summary = page.locator("form p.secondary.num", has_text="km²")   # the form's box summary
    text = lambda: summary.first.inner_text() if summary.count() else ""
    had_box = text()
    before = page.evaluate("document.querySelector('.leaflet-map-pane').style.transform")
    # half the map: still a real box when an earlier check left the map zoomed far in (min side 0.002 deg)
    pts = (b["x"] + b["width"] * 0.25, b["y"] + b["height"] * 0.2, b["x"] + b["width"] * 0.75, b["y"] + b["height"] * 0.6)
    # "panned" is judged during the gesture: once a box is set the app fits the map to it on purpose
    if dev["touch"]:
        after = touch_drag(page, *pts)
    else:   # desktop: the same drawing with a mouse must keep working
        page.mouse.move(pts[0], pts[1]); page.mouse.down(); page.mouse.move(pts[2], pts[3], steps=6)
        after = page.evaluate(PANE)
        page.mouse.up()
    page.wait_for_timeout(500)
    boxed = text() not in ("", had_box)
    ok(boxed and before == after, f"touch_draw_makes_box[{dev['name']}]: new box drawn {boxed}, map panned {before != after}")


TILES_COVER = """() => { const c = document.querySelector('.leaflet-container').getBoundingClientRect();
  const t = [...document.querySelectorAll('img.leaflet-tile')].filter(i => i.complete && i.naturalWidth > 0)
    .map(i => i.getBoundingClientRect());
  let miss = 0;
  for (let i = 0; i < 12; i++) for (let j = 0; j < 8; j++) {
    const x = c.left + (i + 0.5) * c.width / 12, y = c.top + (j + 0.5) * c.height / 8;
    if (!t.some(r => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom)) miss++;
  }
  return miss }"""


def tiles_cover_map_after_resize(page, dev, job):
    if dev["name"] not in ("390x844", "1440x900"):
        return
    go(page, "/jobs/{job}", job)
    wait_until(page, f"({TILES_COVER})() === 0", 20)
    # The window keeps its size; only the map's box grows (what a panel or sheet change does).
    # Leaflet already follows window resizes; this is the case that left grey tile areas.
    page.evaluate("""() => { const w = document.querySelector('.mapwrap'); const r = w.getBoundingClientRect();
        w.style.position = 'fixed'; w.style.inset = '0'; w.style.zIndex = '9999'; }""")
    wait_until(page, f"({TILES_COVER})() === 0", 6)
    miss = page.evaluate(f"({TILES_COVER})()")
    page.evaluate("() => { const w = document.querySelector('.mapwrap'); w.style.position = w.style.inset = w.style.zIndex = ''; }")
    ok(miss == 0, f"tiles_cover_map_after_resize[{dev['name']}]: {miss}/96 sample points with no map tile")


def rect(page, sel):
    return page.evaluate(f"(() => {{ const e = document.querySelector('{sel}'); if (!e) return null; const r = e.getBoundingClientRect(); return {{top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height}} }})()")


def sheet_toggles(page, dev, job):
    if dev["name"] not in ("360x740", "390x844"):
        return
    go(page, "/", job)
    H = dev["height"]
    handle = page.locator(".sheet-handle")
    if handle.count() == 0:
        return ok(False, f"sheet_toggles[{dev['name']}]: no sheet handle")
    if handle.get_attribute("aria-expanded") == "true":
        handle.tap(); page.wait_for_timeout(400)
    sheet = rect(page, ".sidebar")
    map_visible = sheet["top"] - rect(page, ".mapwrap")["top"]
    h = rect(page, ".sheet-handle")
    ok(map_visible >= 0.55 * H and 0 <= h["top"] and h["bottom"] <= H,
       f"sheet_toggles[{dev['name']}] closed: map shows {map_visible:.0f}px of {H}, handle visible {h['bottom'] <= H}")
    # nothing of the map (attribution, controls) may sit on top of the handle, and the attribution stays visible
    on_top = page.evaluate("""(() => { const h = document.querySelector('.sheet-handle').getBoundingClientRect();
        const pts = [[h.left + 8, h.top + h.height / 2], [h.left + h.width / 2, h.top + h.height / 2], [h.right - 8, h.top + h.height / 2]];
        return pts.every(([x, y]) => document.elementFromPoint(x, y)?.closest('.sheet-handle')) })()""")
    att = rect(page, ".leaflet-control-attribution")
    ok(on_top and att and att["bottom"] <= h["top"] + 0.5,
       f"sheet_above_map_controls[{dev['name']}]: handle on top {on_top}, attribution above the sheet {att and att['bottom'] <= h['top'] + 0.5}")
    handle.tap(); page.wait_for_timeout(400)
    btn = page.get_by_role("button", name="Make maps")
    btn.scroll_into_view_if_needed()
    b = btn.bounding_box()
    ok(handle.get_attribute("aria-expanded") == "true" and b and b["y"] >= 0 and b["y"] + b["height"] <= H,
       f"sheet_toggles[{dev['name']}] open: 'Make maps' reachable {bool(b)}")
    page.get_by_role("button", name="Draw area").tap(); page.wait_for_timeout(400)
    ok(handle.get_attribute("aria-expanded") == "false", f"sheet_toggles[{dev['name']}]: drawing closes the sheet")
    page.get_by_role("button", name="Cancel").tap(); page.wait_for_timeout(300)


def landscape_side_by_side(page, dev, job):
    if dev["name"] != "844x390":
        return
    go(page, "/", job)
    sb, mw = rect(page, ".sidebar"), rect(page, ".mapwrap")
    ok(abs(sb["width"] - 300) <= 2 and sb["right"] <= mw["left"] + 1 and mw["height"] >= 200 and page.locator(".sheet-handle").count() == 0,
       f"landscape_side_by_side[{dev['name']}]: panel {sb['width']:.0f}px, map {mw['width']:.0f}x{mw['height']:.0f}")


def tablet_columns(page, dev, job):
    if dev["name"] != "768x1024":
        return
    go(page, "/", job)
    sb = rect(page, ".sidebar")
    ok(abs(sb["width"] - 340) <= 2, f"tablet_columns[{dev['name']}]: panel {sb['width']:.0f}px")


def desktop_unchanged(page, dev, job):
    if dev["name"] != "1440x900":
        return
    go(page, "/", job)
    sb = rect(page, ".sidebar")
    ok(abs(sb["width"] - 420) <= 1 and page.locator(".sheet-handle").count() == 0,
       f"desktop_unchanged[{dev['name']}]: panel {sb['width']:.0f}px, handle {page.locator('.sheet-handle').count()}")


def inside(r, W, H):
    return r and r["left"] >= -0.5 and r["top"] >= -0.5 and r["right"] <= W + 0.5 and r["bottom"] <= H + 0.5


def overlays_fit(page, dev, job):
    if dev["name"] != "360x740":
        return
    W, H = dev["width"], dev["height"]
    go(page, "/", job)
    try:
        page.locator(".coverage-legend").wait_for(state="attached", timeout=10000)
    except Exception:
        ok(False, f"overlays_fit[{dev['name']}]: no coverage legend on the map")
    else:
        is_open = page.evaluate("document.querySelector('.coverage-legend').open")
        ok(is_open is False and inside(rect(page, ".coverage-legend"), W, H),
           f"overlays_fit[{dev['name']}] coverage legend starts collapsed and on screen (open={is_open})")
    go(page, "/jobs/{job}", job)
    page.wait_for_timeout(1500)
    m = rect(page, ".leaflet-container")
    page.touchscreen.tap(m["left"] + m["width"] / 2, m["top"] + 0.2 * H)
    page.locator(".inspector").wait_for(timeout=10000)
    page.wait_for_timeout(500)
    ins, hd = rect(page, ".inspector"), rect(page, ".sheet-handle")
    ok(inside(ins, W, H) and ins["height"] <= 0.4 * H + 1 and (not hd or ins["bottom"] <= hd["top"] + 0.5),
       f"overlays_fit[{dev['name']}] inspector {ins['width']:.0f}x{ins['height']:.0f} at {ins['top']:.0f}-{ins['bottom']:.0f}, handle top {hd and round(hd['top'])}")
    page.locator(".inspector button[aria-label=Close]").tap()


def map_tools_reachable(page, dev, job):
    if dev["name"] != "320x568":
        return
    go(page, "/", job)
    boxes = page.evaluate("""() => [...document.querySelectorAll('.map-tools .btn, .leaflet-control-zoom a')]
        .map(e => { const r = e.getBoundingClientRect(); return {n: e.textContent.trim() || e.title, l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height} })""")
    small = [b["n"] for b in boxes if b["w"] < 44 or b["h"] < 44]
    clash = [(a["n"], b["n"]) for i, a in enumerate(boxes) for b in boxes[i + 1:]
             if a["l"] < b["r"] and b["l"] < a["r"] and a["t"] < b["b"] and b["t"] < a["b"]]
    ok(boxes and not small and not clash, f"map_tools_reachable[{dev['name']}]: too small {small}, overlapping {clash}")


def dialog_fits_small_screen(page, dev, job):
    if dev["name"] != "360x740":
        return
    page.set_viewport_size({"width": 360, "height": 640})
    try:
        go(page, "/", job)
        open_sheet(page)
        page.get_by_role("checkbox", name="Minerals").tap()
        dlg = page.get_by_role("dialog", name="Connect your NASA account")
        dlg.wait_for(timeout=5000)
        r = rect(page, "dialog[open]")
        scrolls = page.evaluate("(() => { const d = document.querySelector('dialog[open]'); return d.scrollHeight > d.clientHeight && getComputedStyle(d).overflowY !== 'visible' })()")
        btn = dlg.get_by_role("button", name="Submit")
        btn.scroll_into_view_if_needed()
        b = btn.bounding_box()
        ok((r["bottom"] <= 640.5 or scrolls) and b and 0 <= b["y"] and b["y"] + b["height"] <= 640,
           f"dialog_fits_small_screen[360x640]: dialog {r['top']:.0f}-{r['bottom']:.0f}, scrolls {scrolls}, Submit on screen {bool(b)}")
        page.keyboard.press("Escape")
    finally:
        page.set_viewport_size({"width": dev["width"], "height": dev["height"]})


ADMIN = "admin@example.com"   # ADMIN_EMAIL of the test stack: the platform stats are admin-only


def stats_grid(page, dev, job):
    if dev["name"] not in ("320x568", "360x740"):
        return
    ctx = page.context.browser.new_context(viewport={"width": dev["width"], "height": dev["height"]}, has_touch=True, is_mobile=True)
    p2 = ctx.new_page()
    try:
        sign_in(p2, email=ADMIN)
        go(p2, "/system", job)
        p2.locator(".grid4").wait_for(timeout=10000)
        cols = p2.evaluate("getComputedStyle(document.querySelector('.grid4')).gridTemplateColumns.split(' ').length")
        want = 1 if dev["width"] <= 360 and dev["name"] == "320x568" else 2
        ok(cols == want, f"stats_grid[{dev['name']}]: {cols} columns (want {want})")
    finally:
        ctx.close()


def two_finger(page, a0, b0, a1, b1, steps=5):
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": a0[0], "y": a0[1], "id": 1}, {"x": b0[0], "y": b0[1], "id": 2}]})
    for i in range(1, steps + 1):
        f = i / steps
        pts = [{"x": a0[0] + (a1[0] - a0[0]) * f, "y": a0[1] + (a1[1] - a0[1]) * f, "id": 1},
               {"x": b0[0] + (b1[0] - b0[0]) * f, "y": b0[1] + (b1[1] - b0[1]) * f, "id": 2}]
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": pts})
        page.wait_for_timeout(30)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


ZOOM = """(() => { const c = [...document.querySelectorAll('.leaflet-tile-container')].filter(c => c.querySelector('img'))
    .sort((a, b) => (+b.style.zIndex) - (+a.style.zIndex))[0];   // the level on top is the current zoom
  const m = c && c.querySelector('img').src.match(/tile\\/(\\d+)\\//); return m ? +m[1] : null })()"""


def drawing_edge_cases(page, dev, job):
    """Review findings: a pinch must not draw; the zoom buttons work while drawing; Cancel keeps the sheet shut."""
    if dev["name"] not in ("390x844", "1440x900"):
        return
    go(page, "/", job)
    handle = page.locator(".sheet-handle")
    summary = page.locator("form p.secondary.num", has_text="km²")
    text = lambda: summary.first.inner_text() if summary.count() else ""
    open_sheet(page)
    page.get_by_role("button", name="Draw area").click(); page.wait_for_timeout(400)
    m = rect(page, ".leaflet-container")
    cx, cy = m["left"] + m["width"] / 2, m["top"] + m["height"] * 0.4
    if dev["touch"]:
        before = text()
        two_finger(page, (cx - 30, cy - 30), (cx + 30, cy + 30), (cx - 90, cy - 90), (cx + 90, cy + 90))
        page.wait_for_timeout(600)
        ok(text() == before, f"pinch_does_not_draw[{dev['name']}]: box changed {text() != before}")
    z0 = page.evaluate(ZOOM)
    page.locator(".leaflet-control-zoom-in").click(); page.wait_for_timeout(1200)
    z1 = page.evaluate(ZOOM)
    ok(z0 is not None and z1 == z0 + 1, f"zoom_buttons_while_drawing[{dev['name']}]: zoom {z0} -> {z1}")
    page.get_by_role("button", name="Cancel").click(); page.wait_for_timeout(400)
    if dev["touch"]:
        ok(handle.get_attribute("aria-expanded") == "false", f"cancel_keeps_sheet_closed[{dev['name']}]: expanded={handle.get_attribute('aria-expanded')}")
        hidden = page.evaluate("""(() => { const b = document.querySelector('.sheet-body'); if (!b) return 'no .sheet-body';
            const f = [...b.querySelectorAll('button, input, select, a')]; f[0] && f[0].focus();
            return b.inert === true && !b.contains(document.activeElement) })()""")
        ok(hidden is True, f"closed_sheet_not_focusable[{dev['name']}]: {hidden}")


def desktop_short_window(page, dev, job):
    if dev["name"] != "1440x900":
        return
    page.set_viewport_size({"width": 1366, "height": 480})   # desktop with devtools docked: not a phone
    try:
        go(page, "/", job)
        sb = rect(page, ".sidebar")
        ok(sb["width"] >= 400, f"desktop_short_window[1366x480]: panel {sb['width']:.0f}px (desktop column, not the phone one)")
    finally:
        page.set_viewport_size({"width": dev["width"], "height": dev["height"]})


DEVICE_CHECKS = [drawing_edge_cases, desktop_short_window, stats_grid, dialog_fits_small_screen, overlays_fit, map_tools_reachable, sheet_toggles, landscape_side_by_side, tablet_columns, desktop_unchanged,
                 touch_draw_makes_box, tiles_cover_map_after_resize]


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


def hint_login(page, email):
    """The test IdP signs in whoever login_hint names. Playwright does not route redirect hops, so add the
    hint to the gateway's redirect to the IdP itself (the hn_oauth cookie on that response is kept)."""
    def handle(route):
        resp = route.fetch(max_redirects=0)
        loc = resp.headers.get("location")
        if not loc:   # e.g. 429 from the sign-in rate limit: pass it through unchanged
            return route.fulfill(response=resp)
        route.fulfill(response=resp, headers={**resp.headers, "location": loc + ("&" if "?" in loc else "?") + f"login_hint={email}"})
    page.route("**/api/auth/google/login*", handle)

def sign_in(page, retry=True, email=None):
    """Sign in through the test IdP, as a person would (refresh tokens rotate: every context signs in itself)."""
    hint_login(page, email or EMAIL)
    throttle(4)   # login page refresh, google/login, callback, refresh after landing
    page.goto(f"{BASE}/login")
    page.get_by_role("link", name="Sign in with Google").click()
    try:
        page.wait_for_selector(".leaflet-container", state="attached", timeout=30000)
    except Exception:
        if not retry:
            raise
        time.sleep(61)   # an earlier run in the same minute used up the sign-in rate limit
        page.unroute("**/api/auth/google/login*")
        sign_in(page, retry=False, email=email)


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
