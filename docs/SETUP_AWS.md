# Deploy HydraNet Maps to AWS

This guide takes you from an empty AWS account to the live site at `https://<your domain>`.
You do the account steps once. After that, every release is two GitHub clicks.

Plan for about 2 hours the first time. Most of that is waiting for DNS and certificates.

---

## 0. What you will collect

Write each value down as you get it. Section 8 says where each one goes.

| # | What | Where you get it | Example |
|---|---|---|---|
| A | Your domain | Any registrar (section 2) | `maps.example.com` |
| B | Route 53 zone name | Route 53 (section 2) | `example.com` |
| C | Google OAuth **Client ID** | Google Cloud Console (section 3) | `1234-abc.apps.googleusercontent.com` |
| D | Google OAuth **Client secret** | Google Cloud Console (section 3) | `GOCSPX-...` (secret) |
| E | Your Google e-mail (becomes admin) | You | `you@gmail.com` |
| F | Who else may sign in | You | `friend@gmail.com`, or a Workspace domain `yourlab.org` |
| G | NASA Earthdata token (optional) | urs.earthdata.nasa.gov (section 4) | long token (secret) |
| H | A unique short name | You | `aaron` (makes bucket `hyperspectral-aaron-prod`) |
| I | Alert e-mail | You | budget and certificate notices |

Secrets **D** and **G** go into AWS SSM Parameter Store only. Never put them in git, in
`terraform.tfvars`, in chat, or in a screenshot.

---

## 1. Secure the AWS account (15 min)

1. Sign in as the **root user** and turn on **MFA**: top-right menu → *Security credentials* → *Assign MFA device*.
2. Check your credits: *Billing and Cost Management* → *Credits*. They should show $120 and an expiry date.
   - If the account is on the **Free plan**, some services or instance sizes may be blocked.
     If `terraform apply` fails with an "account plan" or "not authorized" error on EC2,
     upgrade to the **Paid plan**. Your credits still apply.
3. Create a daily-use admin that is not root: *IAM Identity Center* → enable → create user → give it
   the `AdministratorAccess` permission set. Use this user from now on. Put root away.
4. Turn on *Cost Anomaly Detection* (free): *Billing* → *Cost anomaly detection* → create a monitor with your e-mail.
5. Region: everything runs in **us-west-2 (Oregon)**, next to NASA's EMIT data. Leave it.

On your laptop:

```bash
brew install awscli terraform          # Terraform must be >= 1.10
aws configure sso                       # sign in as the Identity Center admin (not root)
aws sts get-caller-identity             # check: shows your admin user, not root
```

---

## 2. Domain + Route 53 (10 min, then up to 1 h of DNS waiting)

Google sign-in and HTTPS need a real domain.

1. Buy a domain at any registrar (about $10–15 a year). AWS credits usually **cannot** pay for domain
   registration, so Cloudflare, Namecheap or Porkbun are as good as Route 53 here.
2. In AWS: *Route 53* → *Hosted zones* → *Create hosted zone*: name = your domain (for example `example.com`),
   type **Public**. This costs $0.50 a month.
3. Copy the 4 **NS** records of the new zone into your registrar's "custom nameservers".
4. Wait until this prints the AWS nameservers:
   ```bash
   dig +short NS example.com
   ```

You can serve the site on the bare domain (`example.com`) or a sub-domain (`maps.example.com`).
Either way the zone is `example.com`.
→ values **A** (site domain) and **B** (zone name).

---

## 3. Google sign-in client (15 min)

Sign-in is **Google only**: the platform stores no passwords.

1. https://console.cloud.google.com → create a project, for example `hydranet`.
2. *APIs & Services* → *OAuth consent screen* (in newer consoles: *Google Auth Platform* → *Branding*):
   - User type **External**. App name `HydraNet Maps`. Support e-mail = yours.
   - Scopes: only `openid`, `email`, `profile`. These are non-sensitive scopes, so Google review is not needed.
   - While the app is in **Testing**, only the Google accounts you add as *test users* can sign in.
     Add yourself and the people in **F**, or click *Publish app*.
     HydraNet's own allow list still applies either way.
3. *Credentials* → *Create credentials* → *OAuth client ID* → **Web application**:
   - Authorized JavaScript origins: `https://<A>`
   - Authorized redirect URIs: `https://<A>/api/auth/google/callback`
   - To also sign in locally, add `http://localhost:3000` and
     `http://localhost:3000/api/auth/google/callback`.
4. Copy the **Client ID** (**C**) and **Client secret** (**D**).

> **Your old client secret has leaked.** The `client_secret_859615018936-...json` file was pasted into a chat.
> Open that client → *Add secret* → use the new one → **delete the old secret**.
> Delete the `client_secret_*.json` files from `~/Downloads` once you have stored the values somewhere safe,
> for example a password manager.

---

## 4. NASA Earthdata token (optional, 5 min)

Users connect their **own** NASA token inside the app before Minerals or Agriculture jobs run.
A platform token is only used for batch jobs.
https://urs.earthdata.nasa.gov → *Generate Token* → value **G** (valid 60 days). You can skip this.

---

## 5. Terraform state bucket (5 min, once)

Terraform state holds generated secrets, so it lives in a private, encrypted S3 bucket, not on the laptop.

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply -var name=<H>          # prints state_bucket = hydranet-tfstate-<H>
cd ..
cp backend.hcl.example backend.hcl     # set bucket = hydranet-tfstate-<H>
```

---

## 6. Create the platform (20 min, mostly waiting)

```bash
cp terraform.tfvars.example terraform.tfvars   # fill every CHANGE_ME with A, B, E, F, H, I
terraform init -backend-config=backend.hcl
terraform plan          # read it: about 60 resources, no destroys
terraform apply
```

The apply creates:
- the S3 data bucket (private, encrypted, HTTPS-only) and ECR registries;
- an EC2 `t4g.large` server with a fixed Elastic IP;
- CloudFront with an ACM certificate, and the DNS records;
- secrets in SSM, an AWS budget with e-mail alerts, and daily disk snapshots;
- the GitHub deploy role.

Keep the outputs. You need `deploy_role_arn`, `instance_id` and `google_oauth_settings`.

```bash
terraform output
```

Check that `google_oauth_settings` matches what you entered in Google (section 3).

---

## 7. Put the secrets into SSM (5 min)

Terraform made these as `CHANGE_ME` and will never overwrite them. The deploy refuses to run until they are set.

```bash
aws ssm put-parameter --overwrite --type String       --name /hydranet/prod/GOOGLE_CLIENT_ID     --value '<C>'
aws ssm put-parameter --overwrite --type SecureString --name /hydranet/prod/GOOGLE_CLIENT_SECRET --value '<D>'
aws ssm put-parameter --overwrite --type SecureString --name /hydranet/prod/EARTHDATA_TOKEN      --value '<G or unset>'
```

Tip: put a space before each command, so your shell history does not keep the secret
(this works in zsh with `setopt HIST_IGNORE_SPACE`).

---

## 8. Connect GitHub (10 min)

In github.com/aaron43210/remote_sensing_pipeline → *Settings*:

1. *Environments* → *New environment* → name **`production`** → add yourself as a **required reviewer**.
   AWS only trusts this environment and release tags.
2. *Secrets and variables* → *Actions* → *New repository secret*:

| Secret | Value |
|---|---|
| `AWS_DEPLOY_ROLE` | `deploy_role_arn` output |
| `INSTANCE_ID` | `instance_id` output |

3. *Branches* → add a protection rule for your release branch: require a pull request and require CI to pass.

Where every value ends up:

| Value | Goes to |
|---|---|
| A, B, E, F, H, I | `infra/terraform/terraform.tfvars` (gitignored) |
| C, D, G | SSM Parameter Store (section 7) |
| `deploy_role_arn`, `instance_id` | GitHub repository secrets |
| State bucket name | `infra/terraform/backend.hcl` (gitignored) |

---

## 9. First release and deploy (30 min)

```bash
git tag -a v1.0.0 -m "HydraNet v1.0.0"
git push origin v1.0.0
```

1. GitHub → *Actions* → **release** builds 9 images (arm64 + amd64) into ECR. This takes about 20 minutes.
2. *Actions* → **deploy** → *Run workflow* → tag `v1.0.0` → approve (production environment).

   The deploy then runs on the server, in order:
   - reads every setting from SSM;
   - gets the origin's Let's Encrypt certificate;
   - starts the stack and promotes the bundled v1 model;
   - checks health over HTTPS.

The server's first boot also sets up:
- a 4 GB swap file;
- the firewall rule that blocks containers without AWS needs from the instance metadata service;
- daily timers for certificate renewal and Postgres backups to S3.

---

## 10. Check it works (10 min)

```bash
D=<A>
curl -sI https://$D | grep -iE "^HTTP|strict-transport|content-security"     # 200, HSTS, CSP
curl -s -o /dev/null -w "%{http_code}\n" http://$D                          # 301 -> https
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 https://origin.$D    # times out or 403: origin locked
curl -s -X POST -o /dev/null -w "%{http_code}\n" https://$D/api/auth/login  # 404: no password login exists
```

Then in a browser:
1. Open `https://<A>` → **Sign in with Google** with **E**. You are admin: *Platform* shows stats.
2. A Google account not in **F** gets "This Google account is not allowed".
3. Draw a small box over Fresno in summer 2023, tick Thermal, then *Make maps*. Maps appear in a few minutes.
4. Connect your NASA token in the popup and run Minerals over Death Valley.

Server shell, if ever needed (no SSH is open):
*EC2* → instance → *Connect* → *Session Manager*.
Logs are in *CloudWatch* → *Log groups* → `/hydranet/prod`.

---

## 11. Costs (fits $120 of credits)

| Item | About / month |
|---|---|
| EC2 `t4g.large`, 15 h/day (stops 22:00–07:00 Pacific) | $30 |
| EBS 50 GB + daily snapshots | $5 |
| Elastic IP (public IPv4) | $3.65 |
| S3, ECR, CloudWatch logs (14-day retention) | $2–4 |
| Route 53 zone | $0.50 |
| CloudFront, SSM, ACM certificate | free tier / $0 |
| **Total** | **about $42** → $120 lasts about 3 months |

To stretch the credits:
- Keep `office_hours_only = true` (the default).
- Leave `enable_waf = false`: the WAF adds about $8–10 a month.
- Move to `t4g.xlarge` only if large scenes run out of memory.

The budget e-mails you at 25 / 50 / 80 / 100 % of `budget_usd`.

**When the credits run out:**
1. Download what you want to keep from `s3://hyperspectral-<H>-prod/`.
2. Empty that bucket in the S3 console (it is versioned, so also delete "show versions").
3. Run `terraform destroy` in `infra/terraform`.

The Hugging Face model and demo stay online for free (see [SETUP_HUGGINGFACE.md](SETUP_HUGGINGFACE.md)).

---

## 12. If something goes wrong

| Symptom | Fix |
|---|---|
| `terraform apply` stuck on ACM validation | DNS not delegated yet (section 2, step 4). Wait, then re-run apply. |
| Deploy says `... is still CHANGE_ME` | Do section 7. |
| Google says `redirect_uri_mismatch` | The redirect URI in Google must be exactly `https://<A>/api/auth/google/callback`. |
| Sign-in page says "not allowed" | Add the e-mail to `allowed_emails` in tfvars → `terraform apply` → run deploy again. |
| CloudFront 502 | On the server: `cd /opt/hydranet && docker compose -f docker-compose.yml -f docker-compose.aws.yml ps`. Check the certificate with `sudo bash scripts/origin_cert.sh`. |
| Site down every morning | Normal before 07:00 Pacific when `office_hours_only = true`. |
| Restore the database | See the comment at the top of `scripts/pg_backup.sh`. Daily disk snapshots are in *EC2* → *Snapshots*. |
