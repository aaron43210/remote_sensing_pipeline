# Security

How HydraNet Maps is protected, what each control stops, and how to operate it.
Every control below is exercised by `tests/security.py` (attacks that must fail),
which runs in CI against the full stack.

## Threat model (what we defend against)

| Threat | Control | Verified by |
|---|---|---|
| Password theft, guessing, reuse | **No passwords exist.** Sign-in is Google only (OpenID Connect, authorization code + PKCE S256, `state` + `nonce`); `/auth/register` and `/auth/login` are gone | "no password endpoint" tests |
| Forged or replayed sign-in | `state` and `nonce` are random, kept server-side (Redis, 10 min, **single use**), bound to the browser by an HttpOnly `SameSite=Lax` cookie; the ID token's signature (Google's keys), issuer, audience, expiry and nonce are verified; `email_verified` is required | login-CSRF, tampered-state, replay, forged-code, unverified-email tests |
| Strangers using paid compute | Allow list: `ADMIN_EMAIL`, `ALLOWED_EMAILS`, `ALLOWED_EMAIL_DOMAINS`. A domain counts only when Google proves it owns the account (Workspace `hd` claim, or gmail.com). Accounts are matched on Google's stable `sub`, not on e-mail. A production gateway refuses to start with an empty list, a non-Google issuer, a non-HTTPS redirect or insecure cookies | allow-list + startup unit tests |
| Open redirect after sign-in | The post-login path is checked server-side (same site only) | open-redirect tests |
| Sign-in flooding from many IPs | Per-IP limit (20/min) on the sign-in routes | spoofed-IP limit test |
| Dodging limits with a fake client IP | `X-Forwarded-For` is never trusted; nginx **overwrites** `X-Real-IP` with the TCP peer (or, behind CloudFront only, `CloudFront-Viewer-Address`); the gateway has `SetTrustedProxies(nil)` | spoofed XFF / X-Real-IP test |
| Forged / tampered JWTs | HS256 only (`alg: none`, HS512, RS/HS confusion refused), signature by a key in the ring (`kid`), issuer and audience checked, `exp` required, `nbf`/`iat` checked, 4 KB size cap | 9 forgery tests |
| Stolen access token | 15-minute lifetime; kept **in memory only** (never in `localStorage`), so XSS cannot read it from storage; revoked instantly on logout (denylisted `jti`) | logout test |
| Stolen refresh token | Opaque random 256-bit token, only its SHA-256 is stored; `HttpOnly`, `Secure` (HTTPS), `SameSite=Strict`, `Path=/api/auth` cookie; **rotated on every use**; replaying an old one revokes the whole session family | rotation + theft-detection tests |
| CSRF on cookie endpoints | `SameSite=Strict` + required `X-Requested-With: hydranet` header + `Origin` allow-list | CSRF tests |
| Token leaks via URLs/logs | nginx logs the path only, never the query (so no OAuth `code`/`state` or stream tickets in logs); tokens are never accepted in query strings; the live-status stream uses a **single-use, 30-second ticket** bound to one user and one job | ticket tests |
| Account takeover persistence | `POST /api/auth/logout-all` (ops/API) bumps `users.token_version` (kills all access tokens) and revokes all refresh families | logout-all test |
| Reading other users' data (IDOR) | Every `/jobs/:id*` route checks ownership and answers 404 (no existence leak); admin-only `/stats` | IDOR tests |
| Injection | Parameterised SQL everywhere (pgx), strict request validation (bbox numbers, task enum, dates), JSON only | SQL-string tests |
| XSS / clickjacking / sniffing | React escaping; strict CSP (`script-src 'self'`, no inline/eval, explicit image/connect origins, `frame-ancestors 'none'`), `X-Frame-Options: DENY`, `nosniff`, COOP, Permissions-Policy, HSTS | header tests + browser test with CSP on |
| Oversized / abusive requests | 64 KB body cap, nginx 30 req/s per IP with burst, per-user API limit, admission control (3 active jobs per user, 500 global) | 413 test, load tests |
| Leaking users' NASA Earthdata tokens | AES-256-GCM with `CREDENTIALS_KEY`, each value bound to its user (or job), so a copied ciphertext does not open elsewhere; never returned by the API, never in Kafka, logs or job status; handed to ingestion per job through Redis, expiring at the ingest deadline and deleted after the download; tried on a real EMIT file before saving and only ever sent to `*.nasa.gov` | `pkg/secretbox` and `handlers/earthdata_test.go` unit tests |
| Data exposure in storage | Bucket blocks public access, SSE-encrypted; maps reach browsers only through **15-minute presigned URLs** | e2e |
| Compromised container | Non-root users, `no-new-privileges`, all Linux capabilities dropped, read-only root filesystems for the Go services, distroless images, internal services not published (only nginx and MinIO's S3 port) | compose |
| Vulnerable dependencies / leaked secrets | CI: `govulncheck`, `pip-audit`, `npm audit`, Trivy image scan (critical), gitleaks | CI `security` job |

Every authentication event (`login_ok`, `login_fail` with its reason, e.g. `not_allowed`, `state`, `unverified`,
`refresh_reuse_detected`, `logout`, `logout_all`, and for NASA accounts `earthdata_connect`,
`earthdata_refused`, `earthdata_disconnect`) is recorded in `audit_log`
with time, user, IP and user agent.

## Operating it

**Rotate the JWT key** without logging anyone out:
1. Set `JWT_PREVIOUS_SECRET` to the current `JWT_SECRET`, and `JWT_SECRET` to a new value (`openssl rand -hex 32`).
2. Restart the gateway. Old tokens still verify (their `kid` points at the previous key); new ones use the new key.
3. After 15 minutes (the access-token lifetime) remove `JWT_PREVIOUS_SECRET`.

**Rotate `CREDENTIALS_KEY`:** stored NASA tokens cannot be re-read with a new key, so run
`DELETE FROM earthdata_tokens;`, set the new key and restart the gateway and ingestion. Each user is asked
for their token again the next time they choose Minerals or Agriculture.

**Respond to a compromised account:** `UPDATE users SET token_version = token_version + 1 WHERE email = '…'`,
revoke its refresh tokens (`UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = …`), then
`SET token_version:<id> <new>` in Redis. Or call `POST /api/auth/logout-all` with that user's token.

**Watch for attacks:**
```sql
SELECT event, count(*), count(DISTINCT ip) FROM audit_log WHERE ts > now() - interval '1 hour' GROUP BY event;
SELECT * FROM audit_log WHERE event = 'refresh_reuse_detected' ORDER BY ts DESC LIMIT 20;
```

**Give someone access / take it away:** add or remove the e-mail in `allowed_emails` (terraform.tfvars) →
`terraform apply` → run the deploy workflow. To end their current sessions too, use the compromised-account steps.

**In production (AWS)** (setup: [docs/SETUP_AWS.md](docs/SETUP_AWS.md)):

| Layer | Control |
|---|---|
| Edge | CloudFront with your domain's ACM certificate (TLS ≥ 1.2_2021), HTTP → HTTPS; optional AWS WAF (`enable_waf`) |
| CloudFront → server | **HTTPS only** (Let's Encrypt certificate on the server, renewed daily by a timer) and a 48-character secret header (`X-Origin-Verify`) that nginx requires, so the server cannot be reached around CloudFront, and the client-IP header it trusts cannot be forged |
| Server network | Security group: inbound 443 from CloudFront's address list only, outbound 443 only; no SSH (Session Manager); fixed Elastic IP |
| Containers | Nginx, Kafka, Postgres and Redis are firewalled off the instance metadata service, so they can never obtain the instance role's AWS credentials |
| Secrets | SSM Parameter Store (SecureString). Generated ones (JWT, DB, origin, encryption keys) by Terraform; yours (Google, NASA) set by you and never stored in Terraform state; Terraform state itself in a private, versioned, encrypted, HTTPS-only S3 bucket |
| Data | Bucket: no public access, encrypted, HTTPS-only policy, presigned URLs (15 min); daily Postgres dump to S3 (30 days) and daily EBS snapshots (7) |
| Deploys | GitHub OIDC (no AWS keys anywhere): only the `production` environment (required reviewer) and `v*` release tags can assume the deploy role; immutable, scanned ECR images |
| Cost | Monthly budget with e-mail alerts; the server stops overnight |

`deploy.sh` sets `COOKIE_SECURE=true`, `TRUST_CLOUDFRONT=on`, `ALLOWED_ORIGINS` = your domain only,
`STORAGE_ORIGIN` = the bucket host (CSP) and `upgrade-insecure-requests`, and refuses to deploy while a secret
is still `CHANGE_ME`. The frontend ships no source maps.

## Known trade-offs

* Account security (password strength, 2-step verification, recovery) is Google's: ask users to turn on
  2-Step Verification. Removing someone from the allow list blocks new sign-ins; existing sessions end
  within 15 minutes of a logout-all or at refresh expiry (14 days) otherwise.
* Without `enable_waf` there is no managed attack filtering at the edge; nginx and gateway rate limits
  still apply.
* S3 and logs use AWS-managed encryption (SSE-S3), not customer-managed KMS keys (cost).
* Local development serves plain HTTP; `Secure` cookies and HSTS take effect behind HTTPS (CloudFront).

## Reporting a vulnerability

Email the maintainers privately with steps to reproduce; please do not open a public issue.
