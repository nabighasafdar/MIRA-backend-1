# MIRA Agent API — AWS EC2 deployment

Deploy the Python browser agent on **AWS EC2** with Docker. The Next.js frontend stays on **Vercel** and calls this API server-side.

## Architecture

```
User → Vercel (Mira Next.js) → HTTP → EC2:8000 (Docker + headless Chromium)
              ↓
          Supabase (auth, chats, profiles)
```

Vercel talks to the backend **server-side** only, so `http://EC2_PUBLIC_IP:8000` works even though the frontend is HTTPS. No custom domain or SSL certificate is required for an FYP demo.

**Repo:** [nabighasafdar/MIRA-backend-1](https://github.com/nabighasafdar/MIRA-backend-1), branch `deployment-backend`

---

## Instance sizing

| Instance | RAM | Architecture | Recommendation |
|----------|-----|--------------|----------------|
| t3.micro | 1 GB | x86 | **No** — OOM with Chromium |
| t3.small | 2 GB | x86 | Minimum paid fallback |
| **m7i-flex.large** | **8 GB** | x86 (Intel) | **Best choice** |
| c7i-flex.large | 4 GB | x86 (Intel) | OK alternative |
| m7g / c7g (Graviton) | varies | **ARM** | **Avoid** — Dockerfile targets x86 |

Use **m7i-flex.large** or **c7i-flex.large** (Intel). Do not pick Graviton (m7g/c7g) unless you rebuild the image for ARM.

---

## Phase 1 — AWS account and key pair (~15 min)

1. Sign in to [AWS Console](https://console.aws.amazon.com)
2. Pick a region (e.g. `us-east-1`)
3. **EC2 → Key Pairs → Create key pair**
   - Name: `mira-backend-key`
   - Type: RSA or ED25519
   - Download the `.pem` file and keep it safe

On your Mac:

```bash
chmod 400 ~/Downloads/mira-backend-key.pem
# Optional: move to ~/.ssh/
mv ~/Downloads/mira-backend-key.pem ~/.ssh/
```

---

## Phase 2 — Launch EC2 instance (~10 min)

1. **EC2 → Launch instance**
2. Settings:
   - **Name:** `mira-agent-backend`
   - **AMI:** Ubuntu Server 24.04 LTS
   - **Instance type:** `m7i-flex.large` (fallback: `c7i-flex.large`)
   - **Key pair:** `mira-backend-key`
   - **Storage:** 30 GB gp3
3. **Security group** — create new:

| Type | Port | Source | Purpose |
|------|------|--------|---------|
| SSH | 22 | My IP | SSH access |
| Custom TCP | 8000 | 0.0.0.0/0 | Agent API (tighten to your IP later) |

4. Launch → copy **Public IPv4 address** (e.g. `16.170.205.222`)

---

## Phase 3 — Generate and gather secrets (~5 min)

Generate a shared agent secret (or reuse the one from local `.env`):

```bash
openssl rand -hex 32
```

Use the **same** value for `AGENT_API_SECRET` on EC2 and Vercel.

Gather from your local env files:

| Variable | Source |
|----------|--------|
| `GOOGLE_API_KEY` | `MIRA-backend-main1/.env` |
| `SUPABASE_URL` | Same |
| `SUPABASE_SERVICE_ROLE_KEY` | Same (service role, not anon key) |
| Vercel production URL | Vercel dashboard |

Copy [`.env.production.example`](.env.production.example) as a template for EC2 `.env`.

---

## Phase 4 — SSH and deploy (~20–40 min first build)

```bash
ssh -i ~/Downloads/mira-backend-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

Replace `YOUR_EC2_PUBLIC_IP` with the address from the EC2 console. Username is **`ubuntu`** for Ubuntu AMI.

### Option A — Automated setup script

On EC2:

```bash
curl -fsSL https://raw.githubusercontent.com/nabighasafdar/MIRA-backend-1/deployment-backend/scripts/aws-ec2-setup.sh | bash
```

Or, if you already cloned the repo:

```bash
cd ~/MIRA-backend-1
bash scripts/aws-ec2-setup.sh
```

The script installs Docker and prepares the repo. Then:

```bash
cd ~/MIRA-backend-1
nano .env          # paste production values (see .env.production.example)
docker build -t mira-agent .
docker run -d --name mira-agent --restart unless-stopped \
  -p 8000:8000 --env-file .env mira-agent
```

### Option B — Manual steps

```bash
# Install Docker (Ubuntu 24.04)
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu
newgrp docker

# Get the code (pick one method)
```

**Clone (private repo needs a GitHub token):**

```bash
cd ~
git clone -b deployment-backend https://github.com/nabighasafdar/MIRA-backend-1.git
cd MIRA-backend-1
```

If the repo is private, create a [GitHub Personal Access Token](https://github.com/settings/tokens) with **`repo`** scope, then:

```bash
git clone -b deployment-backend https://nabighasafdar:ghp_YOUR_TOKEN@github.com/nabighasafdar/MIRA-backend-1.git
```

**Or copy from your Mac (no GitHub token needed):**

On your **Mac**:

```bash
ssh -i ~/Downloads/mira-backend-key.pem ubuntu@YOUR_EC2_PUBLIC_IP "rm -rf ~/MIRA-backend-1"
scp -i ~/Downloads/mira-backend-key.pem -r \
  /path/to/MIRA-backend-main1 ubuntu@YOUR_EC2_PUBLIC_IP:~/MIRA-backend-1
```

On **EC2**:

```bash
cd ~/MIRA-backend-1
ls Dockerfile    # must exist before docker build
nano .env
docker build -t mira-agent .
docker run -d --name mira-agent --restart unless-stopped \
  -p 8000:8000 --env-file .env mira-agent
```

### Production `.env` on EC2

```env
AGENT_API_SECRET=<same as Vercel>
GOOGLE_API_KEY=<your key>
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service role key>
CORS_ORIGINS=https://your-app.vercel.app,http://localhost:3000
MIRA_HEADLESS=true
MIRA_BROWSER_PROFILE_DIR=/tmp/mira_browser_profiles
MIRA_BROWSER_IDLE_SECONDS=1800
```

---

## Phase 5 — Verify backend (~2 min)

On EC2:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

docker logs mira-agent --tail 50
```

From your Mac:

```bash
curl http://YOUR_EC2_PUBLIC_IP:8000/health
```

If this times out, open **port 8000** in the EC2 security group (inbound Custom TCP 8000).

Full smoke test:

```bash
AGENT_API_URL=http://YOUR_EC2_PUBLIC_IP:8000 \
AGENT_API_SECRET=your-secret \
./scripts/smoke-test.sh
```

---

## Phase 6 — Wire Vercel frontend (~10 min)

Vercel → Project → Settings → Environment Variables:

| Variable | Value |
|----------|--------|
| `AGENT_API_URL` | `http://YOUR_EC2_PUBLIC_IP:8000` (no trailing slash) |
| `AGENT_API_SECRET` | same as EC2 |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | service role (server only) |
| `GROQ_API_KEY` | if using voice |

Then **Redeploy** Vercel.

### Supabase auth URLs

Supabase Dashboard → Authentication → URL Configuration:

- **Site URL:** `https://your-app.vercel.app`
- **Redirect URLs:** `https://your-app.vercel.app/**`, `http://localhost:3000/**`

---

## Phase 7 — End-to-end test

1. Open your Vercel app → log in
2. New chat → send *"open google.com"*
3. Confirm the agent starts (no "agent not connected")
4. Check in-chat preview or **Open live view** → `/agent/[jobId]`

---

## Restart container after `.env` changes

```bash
docker stop mira-agent && docker rm mira-agent
docker run -d --name mira-agent --restart unless-stopped \
  -p 8000:8000 --env-file .env mira-agent
```

Or:

```bash
docker restart mira-agent
```

---

## Optional — systemd (survive reboot)

Copy the unit file and enable it:

```bash
sudo cp scripts/mira-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mira-agent
sudo systemctl start mira-agent
sudo systemctl status mira-agent
```

Edit `/etc/systemd/system/mira-agent.service` if your repo path is not `/home/ubuntu/MIRA-backend-1`.

---

## Optional — HTTPS with custom domain (later)

For FYP, skip this. If you want production polish:

1. Register a domain (Route 53 or any registrar)
2. Point `api.yourdomain.com` → EC2 public IP
3. Install Nginx + Certbot on EC2
4. Set `AGENT_API_URL=https://api.yourdomain.com` on Vercel

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `lstat .../Dockerfile: no such file` | Wrong directory or empty clone | `cd ~/MIRA-backend-1 && ls Dockerfile`; use `scp` from Mac if clone failed |
| `curl` to `:8000` times out | Security group | Open inbound TCP 8000 on EC2 security group |
| Docker build fails (OOM) | Instance too small | Use m7i-flex.large (8 GB), not t3.micro |
| Docker build fails on Graviton | ARM vs x86 | Use m7i-flex / c7i-flex (Intel) |
| Vercel "agent not connected" | Secret or URL mismatch | Match `AGENT_API_SECRET`; set `AGENT_API_URL=http://IP:8000`; redeploy |
| CORS error | Missing Vercel URL | Add exact Vercel URL to `CORS_ORIGINS`, restart container |
| `git clone` auth failed | Private repo | Use GitHub PAT with `repo` scope, or `scp` from Mac |
| Container exits immediately | Bad `.env` or missing keys | `docker logs mira-agent` |

---

## Deployment checklist

Use this after following the phases above:

- [ ] EC2 instance running (`m7i-flex.large` or `c7i-flex.large`)
- [ ] Security group allows SSH (22) and agent API (8000)
- [ ] `~/MIRA-backend-1/Dockerfile` exists
- [ ] `.env` on EC2 with production values
- [ ] `docker build -t mira-agent .` succeeded
- [ ] `docker run` container is up (`docker ps`)
- [ ] `curl http://EC2_IP:8000/health` returns `{"status":"ok"}`
- [ ] Vercel `AGENT_API_URL` and `AGENT_API_SECRET` set
- [ ] Vercel redeployed
- [ ] Supabase redirect URLs include Vercel domain
- [ ] Chat agent smoke test passes

---

## Local development (unchanged)

Frontend: `AGENT_API_URL=http://localhost:8000` in `Mira/.env.local`

Backend: `uvicorn api.main:app --host 0.0.0.0 --port 8000` with `MIRA_HEADLESS=false` for a visible browser.

See [`DEPLOYMENT.md`](DEPLOYMENT.md) for Render and local options.
