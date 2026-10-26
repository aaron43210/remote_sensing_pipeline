"""
Attack tests against a running stack. Every check is an attack that must FAIL.

    python tests/security.py --base http://localhost:3000 --env .env

--env is read only to forge tokens signed with the real key (proving that the
claim checks, not just the signature, protect the API). Exits 1 if any attack
succeeds.
"""

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import sys
import time

import requests

from oidc_login import authorize, finish, login, start

failures = []


def ok(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def b64(d):
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def forge(header, claims, key=None, alg="sha256"):
    h, c = b64(json.dumps(header).encode()), b64(json.dumps(claims).encode())
    sig = b"" if key is None else hmac.new(key.encode(), f"{h}.{c}".encode(), getattr(hashlib, alg)).digest()
    return f"{h}.{c}.{b64(sig)}"


def wait_window():
    """Rate limits use one-minute windows: wait for the next one (the limits are working)."""
    time.sleep(61 - time.time() % 60)


def kid(secret):
    return hashlib.sha256(secret.encode()).hexdigest()[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--env", default=".env")
    a = ap.parse_args()
    B = a.base.rstrip("/") + "/api"
    env = dict(l.strip().split("=", 1) for l in open(a.env) if "=" in l and not l.startswith("#"))
    secret = env["JWT_SECRET"]
    J = {"Content-Type": "application/json"}
    CSRF = {"X-Requested-With": "hydranet"}

    base = a.base.rstrip("/")

    def register(sess=None):
        email = f"sec-{secrets.token_hex(4)}@example.com"
        s, token, user = login(base, email, sess)
        return s, email, token, user["id"]

    def error_of(r):
        return r.headers.get("Location", "").partition("error=")[2]

    print("== security headers")
    for path in ("/", "/api/config", "/assets/", "/jobs/x"):
        h = requests.get(a.base.rstrip("/") + path).headers
        ok("default-src 'self'" in h.get("Content-Security-Policy", ""), f"{path}: Content-Security-Policy")
        ok(h.get("X-Content-Type-Options") == "nosniff", f"{path}: nosniff")
        ok(h.get("X-Frame-Options") == "DENY", f"{path}: clickjacking blocked")
    h = requests.get(a.base).headers
    ok("max-age" in h.get("Strict-Transport-Security", ""), "HSTS header")
    ok("script-src 'self'" in h.get("Content-Security-Policy", ""), "CSP forbids inline/foreign scripts")

    print("== Google sign-in (the only way in)")
    for path in ("/auth/login", "/auth/register"):
        r = requests.post(f"{B}{path}", json={"email": "x@example.com", "password": "whatever-123"})
        ok(r.status_code == 404, f"no password endpoint: POST {path} -> {r.status_code}")
    s0, url = start(base)
    c = next(c for c in s0.cookies if c.name == "hn_oauth")
    ok("httponly" in str(c._rest).lower(), "sign-in state cookie is HttpOnly")
    ok((c._rest.get("SameSite") or c._rest.get("samesite")) == "Lax", "sign-in state cookie is SameSite=Lax")
    ok("code_challenge_method=S256" in url and "nonce=" in url and "state=" in url, "PKCE (S256), nonce and state are sent")

    s1, url = start(base)
    cb = authorize(url, f"sec-{secrets.token_hex(3)}@example.com")
    r = finish(requests.Session(), cb)
    ok(error_of(r) == "expired", f"callback without the browser's state cookie refused (login CSRF): {error_of(r)!r}")
    s1, url = start(base)
    cb = authorize(url, f"sec-{secrets.token_hex(3)}@example.com")
    r = finish(s1, cb.replace("state=", "state=x"))
    ok(error_of(r) == "state", f"tampered state refused: {error_of(r)!r}")
    s1, url = start(base)
    cb = authorize(url, f"sec-{secrets.token_hex(3)}@example.com")
    state_cookie = s1.cookies.get("hn_oauth")
    ok(finish(s1, cb).status_code == 303, "a genuine callback signs in")
    s1.cookies.set("hn_oauth", state_cookie, path="/api/auth/google")
    r = finish(s1, cb)
    ok(error_of(r) == "expired", f"replayed callback refused (state is single-use): {error_of(r)!r}")
    s1, url = start(base)
    r = finish(s1, authorize(url, f"sec-{secrets.token_hex(3)}@example.com").replace("code=", "code=forged"))
    ok(error_of(r) == "failed", f"forged authorization code refused: {error_of(r)!r}")
    s1, url = start(base)
    r = finish(s1, authorize(url, f"sec-{secrets.token_hex(3)}@example.com", {"verified": "false"}))
    ok(error_of(r) == "unverified", f"unverified Google email refused: {error_of(r)!r}")
    s1, url = start(base)
    r = finish(s1, authorize(url, f"stranger-{secrets.token_hex(3)}@not-allowed.org"))
    ok(error_of(r) == "not_allowed", f"account outside the allow list refused: {error_of(r)!r}")
    s1, url = start(base)
    r = finish(s1, authorize(url, f"sec-{secrets.token_hex(3)}@example.com", {"hd": ""}))
    ok(error_of(r) == "not_allowed", f"allowed domain without Google Workspace proof (hd) refused: {error_of(r)!r}")
    s1, url = start(base)
    r = finish(s1, authorize(url, "x@example.com", {"deny": "1"}))
    ok(error_of(r) == "cancelled", f"user cancelling at Google handled: {error_of(r)!r}")
    for evil in ("//evil.example", "https://evil.example", "/\\evil.example"):
        s1, url = start(base, frm=evil)
        r = finish(s1, authorize(url, f"sec-{secrets.token_hex(3)}@example.com"))
        ok(r.headers.get("Location") == "/", f"no open redirect after sign-in (from={evil!r} -> {r.headers.get('Location')!r})")

    print("== rate limit cannot be dodged with forged client-IP headers")
    codes = [requests.get(f"{B}/auth/google/login", allow_redirects=False,
                          headers={"X-Forwarded-For": f"10.9.{i}.{i}", "X-Real-IP": f"10.8.{i}.{i}"}).status_code
             for i in range(30)]
    ok(429 in codes, f"per-IP sign-in limit still applies with spoofed X-Forwarded-For / X-Real-IP (got {sorted(set(codes))})")
    print("  (waiting for the rate-limit window to reset)")
    wait_window()

    print("== JWT forgery")
    _, _, tok, uid = register()
    now = int(time.time())
    good = {"sub": str(uid), "iss": "hydranet-gateway", "aud": ["hydranet-api"], "iat": now, "nbf": now,
            "exp": now + 600, "role": "admin", "ver": 0, "jti": secrets.token_hex(8)}
    attacks = {
        "alg=none": forge({"alg": "none", "typ": "JWT"}, good),
        "wrong key": forge({"alg": "HS256", "kid": kid(secret)}, good, "not-the-key-" * 4),
        "wrong audience": forge({"alg": "HS256", "kid": kid(secret)}, {**good, "aud": ["other-api"]}, secret),
        "wrong issuer": forge({"alg": "HS256", "kid": kid(secret)}, {**good, "iss": "evil"}, secret),
        "expired": forge({"alg": "HS256", "kid": kid(secret)}, {**good, "exp": now - 120, "iat": now - 900}, secret),
        "no expiry": forge({"alg": "HS256", "kid": kid(secret)}, {k: v for k, v in good.items() if k != "exp"}, secret),
        "HS512 with the real key": forge({"alg": "HS512", "kid": kid(secret)}, good, secret, "sha512"),
        "unknown kid": forge({"alg": "HS256", "kid": "deadbeef"}, good, secret),
        "garbage": "a.b.c",
    }
    for name, t in attacks.items():
        r = requests.get(f"{B}/me", headers={"Authorization": f"Bearer {t}"})
        ok(r.status_code == 401, f"refuses token: {name}")
    r = requests.get(f"{B}/me", headers={"Authorization": f"Bearer {tok}"})
    ok(r.status_code == 200, "a genuine token works")
    r = requests.get(f"{B}/jobs?access_token={tok}")
    ok(r.status_code == 401, "token in the URL is not accepted")

    print("== refresh tokens: HttpOnly cookie, rotation, theft detection, CSRF")
    s, email, tok, uid = register()
    c = next(c for c in s.cookies if c.name == "hn_refresh")
    ok(c.has_nonstandard_attr("HttpOnly") or "httponly" in str(c._rest).lower(), "refresh cookie is HttpOnly")
    ok((c._rest.get("SameSite") or c._rest.get("samesite")) == "Strict", "refresh cookie is SameSite=Strict")
    ok(c.path == "/api/auth", "refresh cookie only sent to /api/auth")
    stolen = c.value
    r = s.post(f"{B}/auth/refresh")
    ok(r.status_code == 403, "refresh without the anti-CSRF header is refused")
    r = s.post(f"{B}/auth/refresh", headers={**CSRF, "Origin": "https://evil.example"})
    ok(r.status_code == 403, "refresh from a foreign Origin is refused")
    r = s.post(f"{B}/auth/refresh", headers=CSRF)
    ok(r.status_code == 200 and r.json()["token"] != tok, "refresh rotates and returns a new access token")
    new_cookie = s.cookies.get("hn_refresh", path="/api/auth")
    ok(new_cookie and new_cookie != stolen, "refresh token rotated")
    thief = requests.Session()
    thief.cookies.set("hn_refresh", stolen, path="/api/auth")
    r = thief.post(f"{B}/auth/refresh", headers=CSRF)
    ok(r.status_code == 401, "replayed (stolen) refresh token refused")
    r = s.post(f"{B}/auth/refresh", headers=CSRF)
    ok(r.status_code == 401, "... and the whole session family is revoked (theft detection)")

    print("== logout revokes immediately")
    s, email, tok, uid = register()
    kept = s.cookies.get("hn_refresh", path="/api/auth")
    r = s.post(f"{B}/auth/logout", headers={**CSRF, "Authorization": f"Bearer {tok}"})
    ok(r.status_code == 204, "logout")
    ok(requests.get(f"{B}/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401,
       "access token dead right after logout (not 15 min later)")
    r = s.post(f"{B}/auth/refresh", headers=CSRF)
    ok(r.status_code in (204, 401) and "token" not in (r.text or ""), "browser has no session after logout")
    copy = requests.Session()
    copy.cookies.set("hn_refresh", kept, path="/api/auth")
    ok(copy.post(f"{B}/auth/refresh", headers=CSRF).status_code == 401, "a kept copy of the refresh token is dead after logout")
    s, email, tok, uid = register()
    s.post(f"{B}/auth/logout-all", headers={"Authorization": f"Bearer {tok}"})
    ok(requests.get(f"{B}/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401,
       "sign out everywhere kills existing access tokens")

    print("== access control (IDOR) and the event stream")
    _, _, ta, _ = register()
    _, _, tb, _ = register()
    A = {"Authorization": f"Bearer {ta}"}
    Bh = {"Authorization": f"Bearer {tb}"}
    r = requests.post(f"{B}/jobs", headers=A, json={"bbox": [-119.84, 36.71, -119.79, 36.76], "tasks": ["thermal"],
                                                    "source": "fixture"})
    job = r.json()["job_id"]
    for path in (f"/jobs/{job}", f"/jobs/{job}/layers"):
        ok(requests.get(B + path, headers=Bh).status_code == 404, f"other user cannot read {path}")
    ok(requests.delete(f"{B}/jobs/{job}", headers=Bh).status_code == 404, "other user cannot cancel the job")
    ok(requests.post(f"{B}/jobs/{job}/stream-ticket", headers=Bh).status_code == 404, "other user cannot get a stream ticket")
    ok(requests.get(f"{B}/jobs/{job}/events").status_code == 401, "stream without a ticket refused")
    t = requests.post(f"{B}/jobs/{job}/stream-ticket", headers=A).json()["ticket"]
    other = requests.post(f"{B}/jobs", headers=A, json={"bbox": [-119.40, 36.15, -119.35, 36.20], "tasks": ["thermal"],
                                                        "source": "fixture"}).json()["job_id"]
    ok(requests.get(f"{B}/jobs/{other}/events?ticket={t}").status_code == 401, "ticket cannot open another job's stream")
    t = requests.post(f"{B}/jobs/{job}/stream-ticket", headers=A).json()["ticket"]
    r1 = requests.get(f"{B}/jobs/{job}/events?ticket={t}", stream=True, timeout=10)
    ok(r1.status_code == 200, "a valid ticket opens the stream")
    r1.close()
    ok(requests.get(f"{B}/jobs/{job}/events?ticket={t}", timeout=10).status_code == 401, "a ticket works only once")
    ok(requests.get(f"{B}/stats", headers=A).status_code == 403, "platform stats are admin-only")

    print("== input handling")
    r = requests.post(f"{B}/jobs", headers={**A, **J}, data=b'{"bbox":[1,2,3,4],"tasks":["mineral"],"pad":"' + b"x" * 200_000 + b'"}')
    ok(r.status_code == 413, f"oversized body refused ({r.status_code})")
    for body, why in [({"bbox": ["1; DROP TABLE jobs", 1, 2, 3], "tasks": ["mineral"]}, "SQL in bbox"),
                      ({"bbox": [-119.8, 36.7, -119.75, 36.75], "tasks": ["mineral' OR '1'='1"]}, "SQL in tasks"),
                      ({"bbox": [-119.8, 36.7, -119.75, 36.75], "tasks": ["mineral"], "date_from": "2024-01-01'--"}, "SQL in date")]:
        r = requests.post(f"{B}/jobs", headers=A, json=body)
        ok(r.status_code == 400, f"{why}: rejected as bad input ({r.status_code})")
    for p in ("/jobs/..%2F..%2Fetc%2Fpasswd", "/jobs/../../etc/passwd", "/jobs/%2e%2e%2f%2e%2e%2fetc%2fpasswd"):
        r = requests.get(B + p, headers=A)
        # nginx normalises the path; at worst the SPA's index.html comes back, never a file outside the web root
        ok("root:" not in r.text and r.status_code in (200, 400, 404), f"path traversal {p}: nothing leaked ({r.status_code})")
    r = requests.post(f"{B}/jobs", headers=A, json={"bbox": "<script>alert(1)</script>", "tasks": ["<img src=x onerror=1>"]})
    ok(r.headers.get("Content-Type", "").startswith("application/json"), "API answers JSON, never HTML (no reflected XSS)")
    r = requests.get(f"{base}/login?error=%3Cscript%3Ealert(1)%3C/script%3E")
    ok("<script>alert(1)" not in r.text, "sign-in error codes are never reflected into the page")

    print(f"\n{'PASSED' if not failures else 'FAILED'}: {len(failures)} failure(s)")
    for f in failures:
        print("  -", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
