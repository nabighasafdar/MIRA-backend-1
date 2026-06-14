# MIRA Agent API — deployment notes

The browser agent **must** run on hardware that can launch Chromium (Docker with Playwright deps, a VM, or bare metal). It does **not** run on Vercel serverless functions.

## Environment (Python service)

| Variable | Required | Purpose |
|----------|----------|---------|
| `AGENT_API_SECRET` | Yes | Shared bearer token; Next.js sends `Authorization: Bearer <value>` |
| `GOOGLE_API_KEY` | Usually | Default LLM for the agent (`ChatGoogle`) |
| `SUPABASE_URL` | For wallet/profile | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | For wallet/profile | Service role (server only; never expose to the browser) |
| `CORS_ORIGINS` | Yes (prod) | Comma-separated, e.g. `http://localhost:3000,https://your-app.vercel.app` |
| `MIRA_HEADLESS` | Yes (Docker/Render) | `true` in containers; `false` only for local visible Chromium |
| `MIRA_BROWSER_PROFILE_DIR` | No | Browser profile storage (default `/tmp/mira_browser_profiles`) |
| `MIRA_BROWSER_IDLE_SECONDS` | No | Evict idle browsers (default `1800`) |
| `MIRA_WORKFLOW_OUTPUT_DIR` | No | Directory for recorded workflow JSON files |

Generate a production secret:

```bash
openssl rand -hex 32
```

Use the **same** value for `AGENT_API_SECRET` on the backend host (Render, AWS EC2, etc.) and `AGENT_API_SECRET` on Vercel.

## Run locally

```bash
cd MIRA-backend-main1
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env   # edit keys
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

For a visible browser window locally: `MIRA_HEADLESS=false` in `.env`.

## Docker

```bash
docker build -t mira-agent .
docker run -d --name mira-agent --restart unless-stopped -p 8000:8000 --env-file .env mira-agent
curl http://localhost:8000/health
```

## AWS EC2 (Docker)

Recommended for FYP when you want a fixed public IP and full control. Full step-by-step guide: **[`AWS_DEPLOYMENT.md`](AWS_DEPLOYMENT.md)**.

### Quick setup

1. Launch Ubuntu 24.04 EC2 (`m7i-flex.large` recommended, 8 GB RAM, x86 Intel)
2. Security group: SSH (22) + Custom TCP **8000** (0.0.0.0/0 for demo)
3. SSH in and run [`scripts/aws-ec2-setup.sh`](scripts/aws-ec2-setup.sh) (installs Docker, clones repo)
4. `cp .env.production.example .env` → edit secrets
5. `docker build -t mira-agent .` then `docker run` (see AWS guide)
6. Verify: `curl http://EC2_PUBLIC_IP:8000/health`
7. Set Vercel `AGENT_API_URL=http://EC2_PUBLIC_IP:8000` and matching `AGENT_API_SECRET`

If `git clone` fails (private repo), copy the repo from your Mac with `scp` — see AWS guide.

Optional: [`scripts/mira-agent.service`](scripts/mira-agent.service) for systemd restart on reboot.

## Render (Docker web service)

Recommended for production backend.

### Quick setup (dashboard)

1. [Render Dashboard](https://dashboard.render.com) → **New** → **Web Service**
2. Connect GitHub repo: `nabighasafdar/MIRA-backend-1`, branch **`deployment-backend`**
3. **Runtime:** Docker
4. **Health check path:** `/health`
5. **Instance type:** at least **2 GB RAM** (Chromium + Playwright; 4 GB safer for demos)
6. Set environment variables (see table above). Set `CORS_ORIGINS` to your Vercel URL.
7. Deploy and copy the public HTTPS URL (e.g. `https://mira-agent.onrender.com`)

### Blueprint (repo root)

This repo includes [`render.yaml`](render.yaml). On Render: **New** → **Blueprint** → select the repo and fill in secret env vars when prompted.

### Render notes

- Containers have **no GUI** — users watch automation via Mira **live view** (`/agent/[jobId]`) or in-chat preview.
- Free/starter tiers may **cold start** or OOM on heavy jobs; use paid RAM for FYP demos.
- Optional: attach a **persistent disk** at `/tmp/mira_browser_profiles` for browser session reuse across restarts.

## Frontend (Mira Next.js on Vercel)

Set on the server (never prefix with `NEXT_PUBLIC_`):

- `AGENT_API_URL` — backend URL (Render HTTPS or EC2 `http://IP:8000`), **no trailing slash**
- `AGENT_API_SECRET` — same secret as above
- `OPENAI_API_KEY` — optional; improves `/api/plan-task` structured micro-steps

See [`Mira/DEPLOYMENT.md`](../Mira/DEPLOYMENT.md) for Vercel env vars and Supabase auth URLs.

For long SSE streams on Vercel, `/api/agent-events/[jobId]` uses `maxDuration = 300` (requires a plan that supports 5-minute functions).

## Smoke test (after deploy)

```bash
curl -s http://YOUR-BACKEND-URL/health
# {"status":"ok"}

curl -s -X POST http://YOUR-BACKEND-URL/agent/run \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","chat_id":null,"task":"open google.com"}'
```

Or run [`scripts/smoke-test.sh`](scripts/smoke-test.sh) with env vars set.
