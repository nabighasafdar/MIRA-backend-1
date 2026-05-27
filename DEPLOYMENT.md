# MIRA Agent API — deployment notes

The browser agent **must** run on hardware that can launch Chromium (Docker with Playwright deps, a VM, or bare metal). It does **not** run on Vercel serverless functions.

## Environment (Python service)

| Variable | Required | Purpose |
|----------|----------|---------|
| `AGENT_API_SECRET` | Yes | Shared bearer token; Next.js sends `Authorization: Bearer <value>` |
| `GOOGLE_API_KEY` | Usually | Default LLM for the agent (`ChatGoogle`) |
| `SUPABASE_URL` | For wallet/profile | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | For wallet/profile | Service role (server only; never expose to the browser) |
| `CORS_ORIGINS` | No | Comma-separated list, e.g. `http://localhost:3000,https://your-app.vercel.app` |
| `MIRA_WORKFLOW_OUTPUT_DIR` | No | Directory for recorded workflow JSON files |

## Run locally

```bash
cd MIRA-backend-main1
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
export AGENT_API_SECRET=your-secret
export GOOGLE_API_KEY=your-key
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## Docker

```bash
docker build -t mira-agent .
docker run -p 8000:8000 --env-file .env mira-agent
```

Point the Next.js app at this host with `AGENT_API_URL` and the same `AGENT_API_SECRET`.

## Frontend (Mira Next.js)

Set on the server (never prefix with `NEXT_PUBLIC_`):

- `AGENT_API_URL` — HTTPS URL of the agent API  
- `AGENT_API_SECRET` — same secret as Python  
- `OPENAI_API_KEY` — optional; improves `/api/plan-task` structured micro-steps  

For long SSE streams on Vercel, use Node runtime and configure `maxDuration` on `/api/agent-events/[jobId]` (already set in repo).
