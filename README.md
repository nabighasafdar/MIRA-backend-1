# MIRA Backend

Python browser automation (`system.agent.Agent`) packaged as an installable library. A **FastAPI** service exposes long-running browser jobs over HTTP:

- `POST /agent/run` — run a composed task (`user_id`, `chat_id`, `task`)
- `POST /agent/run-bookmark` — run a saved workflow from Supabase bookmarks
- `GET /agent/jobs/{job_id}/events` — SSE stream (`agent_thought`, `telemetry_billing`, `workflow_macro_recorded`, `done`, `cancelled`, `error`)
- `POST /agent/jobs/{job_id}/cancel` — stop a running job mid-task

## Quick start — Agent API

```bash
pip install -e .
playwright install chromium
cp .env.example .env   # edit keys
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Configure the **Mira** Next.js app with matching `AGENT_API_URL` and `AGENT_API_SECRET`. Details: [`DEPLOYMENT.md`](DEPLOYMENT.md).
