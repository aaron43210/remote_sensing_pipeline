"""
Sign in through Google's flow against the test IdP (docker compose --profile test,
OIDC_ISSUER=http://mock-oidc:9998), exactly as a browser does: gateway -> IdP
authorize -> gateway callback (sets the refresh cookie) -> POST /api/auth/refresh.

    s, token, user = login("http://localhost:3000", "someone@example.com")

extra= adds test knobs to the IdP request: {"verified": "false"}, {"hd": ""}, {"deny": "1"}.
"""

import time
from urllib.parse import urlencode

import requests

CSRF = {"X-Requested-With": "hydranet"}


def _send(fn, *args, **kw):
    """Sign-in routes share a per-IP limit (20/min): wait it out like a person would."""
    for _ in range(4):
        r = fn(*args, **kw)
        if r.status_code != 429:
            return r
        time.sleep(int(r.headers.get("Retry-After", "10")) + 1)
    return r


def start(base, session=None, frm="/"):
    """Step 1: the gateway's redirect to the IdP (sets the hn_oauth cookie)."""
    s = session or requests.Session()
    r = _send(s.get, f"{base}/api/auth/google/login", params={"from": frm}, allow_redirects=False)
    assert r.status_code == 302, f"login start: {r.status_code} {r.text[:200]}"
    return s, r.headers["Location"]


def authorize(authorize_url, email, extra=None):
    """Step 2: the IdP signs the user in and redirects to the gateway callback."""
    r = requests.get(authorize_url + "&" + urlencode({"login_hint": email, **(extra or {})}), allow_redirects=False)
    assert r.status_code == 302, f"authorize: {r.status_code} {r.text[:200]}"
    return r.headers["Location"]


def finish(s, callback_url):
    """Step 3: the gateway callback; returns its response (303 to the app or to /login?error=)."""
    return _send(s.get, callback_url, allow_redirects=False)


def login(base, email, session=None, extra=None):
    s, url = start(base, session)
    r = finish(s, authorize(url, email, extra))
    assert r.status_code == 303 and "error=" not in r.headers.get("Location", ""), \
        f"callback: {r.status_code} -> {r.headers.get('Location')}"
    r = _send(s.post, f"{base}/api/auth/refresh", headers=CSRF)
    assert r.status_code == 200, f"refresh after sign-in: {r.status_code} {r.text[:200]}"
    d = r.json()
    return s, d["token"], d["user"]
