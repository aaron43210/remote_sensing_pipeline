# Publish HydraNet on Hugging Face

Hugging Face is the free, permanent home for HydraNet. It stays up after the AWS credits end.

| Repo | What it holds | Status checked 2026-10-26 |
|---|---|---|
| Model: `aaronr43210/HydraNet-Hyperspectral` | `hydra.onnx`, LUT, model card, verification | **Outdated**: holds the old 1.93 MB model, not v1 (2.06 MB, hash `be8eb2b9cf99ce42`) |
| Space: `aaronr43210/HydraNet-Maps` | the web app | **Broken for visitors**: the frontend only, so every `/api` call is 404 and the page shows a sign-in that cannot work. It also ships `.map` source files. |

The two scripts below fix both. Each one checks before it uploads, and each upload replaces the old files.

---

## 0. What you need from Hugging Face

1. Sign in at huggingface.co as `aaronr43210`.
2. *Settings* → *Access Tokens* → *Create new token* → type **Fine-grained**:
   - Repository permissions: **Write** on `aaronr43210/HydraNet-Hyperspectral` and on the Space `aaronr43210/HydraNet-Maps`.
   - Nothing else.
3. Copy it once. It starts with `hf_`. It is a secret: do not commit it and do not paste it into chats.
   You only pass it as the environment variable `HF_TOKEN` when you run the script.
   Revoke it afterwards if you like.

Nothing from Hugging Face goes into this repository.

---

## 1. Update the model repo (5 min)

```bash
pip install "huggingface_hub>=1.0" onnx
python scripts/publish_hf.py --dry-run                 # checks hash, gates, prints the new model card
 HF_TOKEN=hf_xxx python scripts/publish_hf.py           # note the leading space: keeps it out of shell history
```

What the script does:
- Uploads `hydra.onnx`, `agri_lut.npz`, `model_card.json`, `metrics.json` and `map_verification.*`
  from `training/artifacts/`.
- Writes a fresh README model card: what the model does, the exact numbers, a usage example and its limits.
- Deletes any other file still in the repo.
- Refuses to publish if the ONNX hash and the card disagree, or if any release gate failed.

---

## 2. Rebuild the demo Space (20 min)

The Space becomes a **read-only demo of real results**:
- It shows finished maps from real NASA EMIT and Landsat jobs.
- Visitors can switch tabs, use the legends, inspect pixel values and download the GeoTIFFs.
- There is no sign-in and no server. It is static files only, so it is free and never sleeps.

Making new maps needs the full platform, so the demo links to the GitHub repo and the model.

```bash
# 1. local stack with the test sign-in (it needs your real finished EMIT jobs in the local database)
docker compose --profile local --profile test up -d
#    in .env: OIDC_ISSUER=http://mock-oidc:9998, GOOGLE_CLIENT_ID=ci-client,
#    GOOGLE_CLIENT_SECRET=ci-secret, ADMIN_EMAIL=admin@example.com

# 2. pick 2-4 finished real jobs (one agriculture, one mineral, one thermal)
docker compose exec postgres psql -U spectral hyperspectral -c \
  "select job_id, tasks, status, created_at from jobs where source='nasa' and status='COMPLETED' order by created_at desc limit 10"

# 3. export their maps and build the demo app around them
python scripts/export_demo.py --jobs <id1>,<id2>,<id3> --out docs/hf_space/build

# 4. look at it locally before publishing
python -m http.server -d docs/hf_space/build 8099        # open http://localhost:8099

# 5. publish model + Space together
 HF_TOKEN=hf_xxx python scripts/publish_hf.py --space-dir docs/hf_space/build
```

The export keeps only the job, its layers and its results. It drops user ids, e-mails and presigned
storage links, and it stops if any presigned link remains.
The demo build contains no source maps and no API code paths that could reach a server.

---

## 3. About running the full platform on a Hugging Face Docker Space

An idea came up to run the whole platform in a free Docker Space (2 vCPU, 16 GB, port 7860)
as the permanent production site. Before choosing it, know what the free tier does:

| Need of the platform | Free Docker Space |
|---|---|
| Postgres (users, jobs), Kafka, Redis, 8 services | One container: all of them would have to run in it under a supervisor |
| Data that survives a restart | **No**: the disk is wiped on every restart or rebuild unless you pay for persistent storage |
| Always on | **No**: the Space sleeps after about 48 h without visitors; the first visit after that waits for a cold start |
| Big EMIT downloads (GBs) and processing | Possible, but slow on 2 vCPU, and wiped on restart |
| Google sign-in | Possible: add `https://<space>.hf.space/api/auth/google/callback` in Google |

So a free Docker Space can host a demo of the full app, but not a production service.
Every user, job and map would be lost on each restart.

The recommendation:
- **AWS** runs the real platform while the credits last.
- **Hugging Face** keeps the model and the static demo forever.

That is what sections 1 and 2 set up. If you later want a live, always-on version on Hugging Face,
it needs paid persistent storage plus an external database (for example a free-tier Postgres such as
Neon or Supabase). That is a separate project.

---

## 4. Checklist

- [ ] Fine-grained HF token with write access to the two repos only
- [ ] `publish_hf.py --dry-run` passes
- [ ] Model page shows v1, 2.06 MB, hash `be8eb2b9cf99ce42`
- [ ] Space opens without sign-in, *Jobs* lists the samples, and maps and the pixel inspector work
- [ ] No `.map` files in the Space (*Files* tab)
- [ ] README links (GitHub, model, Space) all open
