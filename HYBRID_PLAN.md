# Hybrid Deployment Plan: Vercel (Frontend) + Mac Mini 4 (Backend)

## Overview

Deploy the React frontend as a static site on Vercel and run the **entire** FastAPI backend (API, worker, databases, AI models) on a Mac Mini M4 in the office.

| Layer | Where | What |
|---|---|---|
| Frontend | Vercel (static site) | React + Vite SPA |
| Backend API | Mac Mini 4 | FastAPI (auth, upload, query, RAG) |
| AI Inference | Mac Mini 4 | Reranker (PyTorch), embeddings & LLM (OpenRouter) |
| Databases | Mac Mini 4 (Docker) | PostgreSQL, Qdrant, Redis |
| Worker | Mac Mini 4 | ARQ worker for async document ingestion |
| Tunnel | Cloudflare Tunnel | Exposes Mac Mini API with HTTPS |

**Why this approach?** No code splitting needed. The Mac Mini M4 has plenty of power to run everything — reranker model, vector DB, Postgres, Redis, and the API. Vercel serves the static frontend for free with global CDN. Cloudflare Tunnel provides free HTTPS without port forwarding.

---

## Architecture

```
Users (browser)
    │
    ▼
Vercel CDN (React SPA)
    │
    │  HTTPS (API calls)
    ▼
Cloudflare Tunnel ──► Mac Mini 4 (office)
                         │
                    Nginx (port 443)
                         │
                    FastAPI (port 8000)
                         │
                  ┌──────┼──────────┐
                  ▼      ▼          ▼
               Qdrant  Postgres   Redis ← ARQ Worker
```

---

## What Runs Where

### On Vercel (static site — free tier)
- React + Vite SPA served via global CDN
- No serverless functions, no backend logic
- `VITE_API_URL` points to Mac Mini's Cloudflare Tunnel URL

### On Mac Mini 4 (everything else)
- **FastAPI** — all API routes (auth, upload, query, RAG)
- **ARQ worker** — async document ingestion
- **PostgreSQL** (Docker) — users, documents, chunks, query logs
- **Redis** (Docker) — cache, rate limiting, job queue
- **Qdrant** (Docker) — vector database
- **Reranker** — BAAI/bge-reranker-base (local PyTorch)
- **Embeddings + LLM** — via OpenRouter API
- **Nginx** — reverse proxy, SSL termination
- **Cloudflare Tunnel** — public HTTPS endpoint

---

## Changes Required

Only 3 minor code changes + 2 new config files. No code splitting, no new services.

### 1. NEW: `advanced_rag_fe/vercel.json`

SPA rewrites so React Router works on Vercel:

```json
{
  "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }]
}
```

### 2. NEW: `advanced_rag_fe/.env.production`

```env
VITE_API_URL=https://rag-api.yourdomain.com
```

No code changes needed — `src/lib/constants.ts` already reads from `import.meta.env.VITE_API_URL`.

### 3. MODIFIED: `advanced_rag/src/main.py` (line 29-33)

Update CORS to allow both local dev and the Vercel production domain:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        os.getenv("FRONTEND_URL", "https://your-app.vercel.app"),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 4. MODIFIED: `advanced_rag/.env`

Add `FRONTEND_URL` so CORS is configurable without code changes:

```env
POSTGRES_URL=postgresql://raguser:ragpass@postgres:5432/ragdb
REDIS_URL=redis://redis:6379
QDRANT_URL=http://qdrant:6333
OPENROUTER_API_KEY=sk-or-v1-...
JWT_SECRET=<generate-secure-random>
FRONTEND_URL=https://your-app.vercel.app
```

---

## Mac Mini 4 Setup

### Step 1: Install Docker Desktop

Download Docker Desktop for Mac from docker.com and install it. The M4 chip uses ARM images natively.

### Step 2: Clone & Configure

```bash
git clone git@github.com:Alokiitdh/advanced_rag.git
cd advanced_rag

# Create production .env
cp .env .env.prod
# Edit .env.prod with production values (real JWT_SECRET, FRONTEND_URL, etc.)
```

### Step 3: Start Services

Use the existing `docker-compose.prod.yml` — it already has everything:

```bash
# Build and start all services
docker compose -f docker-compose.prod.yml up -d

# Verify
docker compose -f docker-compose.prod.yml ps
curl http://localhost:8000/health
curl http://localhost:8000/check-connections
```

### Step 4: Install Cloudflare Tunnel

```bash
# Install
brew install cloudflared

# Authenticate (opens browser — login to your Cloudflare account)
cloudflared tunnel login

# Create a named tunnel
cloudflared tunnel create rag-api
# → prints TUNNEL-UUID
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /Users/alokj/.cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: rag-api.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Add DNS record (Cloudflare dashboard or CLI):

```bash
cloudflared tunnel route dns rag-api rag-api.yourdomain.com
```

Install as a macOS LaunchAgent (auto-starts on reboot):

```bash
sudo cloudflared service install
```

Or run manually:

```bash
cloudflared tunnel run rag-api
```

**Quick test (no domain needed):**

```bash
cloudflared tunnel --url http://localhost:8000
# Prints a temporary trycloudflare.com URL — use this for testing
```

### Step 5: Auto-start on Boot

Create a launchd plist so Docker Compose starts automatically:

```bash
# Docker Desktop starts on login by default
# The Cloudflare Tunnel service is already installed above

# For extra reliability, use the systemd approach from DEPLOY.md section 7
# adapted for macOS launchd
```

---

## Vercel Frontend Deployment

### Option A: Connect via GitHub (recommended)

1. Push `advanced_rag_fe/` to GitHub
2. Go to vercel.com → New Project → Import the repo
3. Set root directory to `advanced_rag_fe`
4. Framework preset: Vite
5. Add environment variable: `VITE_API_URL=https://rag-api.yourdomain.com`
6. Deploy

### Option B: CLI

```bash
cd advanced_rag_fe
npm install -g vercel
vercel login
vercel env add VITE_API_URL  # paste: https://rag-api.yourdomain.com
vercel deploy --prod
```

---

## Environment Variables

### Vercel (dashboard or CLI)

| Variable | Value |
|---|---|
| `VITE_API_URL` | `https://rag-api.yourdomain.com` |

### Mac Mini (`advanced_rag/.env`)

| Variable | Value |
|---|---|
| `POSTGRES_URL` | `postgresql://raguser:ragpass@postgres:5432/ragdb` |
| `REDIS_URL` | `redis://redis:6379` |
| `QDRANT_URL` | `http://qdrant:6333` |
| `OPENROUTER_API_KEY` | Your OpenRouter key |
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FRONTEND_URL` | `https://your-app.vercel.app` |

---

## Verification Steps

### Phase 1 — Mac Mini local

```bash
curl http://localhost:8000/health
# → {"status":"running"}

curl http://localhost:8000/check-connections
# → {"qdrant":"connected","postgres":"connected","redis":"connected"}
```

### Phase 2 — Cloudflare Tunnel

```bash
curl https://rag-api.yourdomain.com/health
# → {"status":"running"}
```

### Phase 3 — Frontend on Vercel

1. Open `https://your-app.vercel.app`
2. Register a new account
3. Upload a document (PDF/DOCX/TXT)
4. Navigate to Chat → ask a question about the document
5. Verify answer includes sources from the uploaded document

### Phase 4 — End-to-end regression

1. Register → Login → Upload document → Wait for "ready" status
2. `/query` returns relevant chunks
3. `/rag` returns generated answer with sources
4. Rate limiting works (hit 20 RAG queries in a minute)
5. Multiple users work independently

---

## Known Pitfalls

| Issue | Mitigation |
|---|---|
| Mac Mini goes to sleep | System Settings → Energy → Prevent automatic sleeping |
| Power outage | Docker Desktop + cloudflared auto-start on boot |
| Reranker cold start | First `/rag` call after restart takes 5–10s extra (model loading) |
| Reranker RAM: ~550MB per worker | 2 workers = ~1.1GB — fine on Mac Mini (16GB+) |
| Office internet outage | API goes down; frontend on Vercel stays up (shows "Cannot reach server") |
| CORS misconfiguration | Verify `FRONTEND_URL` matches exact Vercel domain (no trailing slash) |
| Cloudflare Tunnel reconnect | `cloudflared` auto-reconnects; check `cloudflared tunnel info` |
| Docker disk usage | Schedule `docker system prune` monthly |

---

## Optional Enhancements

### Tailscale Funnel (alternative to Cloudflare Tunnel)

Simpler setup if you don't have a domain:

```bash
brew install tailscale
tailscale up
tailscale funnel 8000
# → https://mac-mini.tailnet-name.ts.net
```

### Nginx on Mac Mini (optional, recommended for production)

Install via Homebrew for SSL termination and upload size limits:

```bash
brew install nginx
# Configure as documented in DEPLOY.md section 5
# Cloudflare Tunnel points to Nginx (port 443) instead of FastAPI directly
```

### Database Backups

Use the backup scripts from DEPLOY.md section 8, adapted for macOS paths. Schedule with `launchd` instead of `cron`.

---

## Summary: What Changes vs. Original Monolith

| Component | Status | Notes |
|---|---|---|
| `advanced_rag/` (all backend code) | **Unchanged** | Runs as-is on Mac Mini |
| `advanced_rag/src/main.py` | **Minor edit** | Add `FRONTEND_URL` to CORS origins |
| `advanced_rag/.env` | **Minor edit** | Add `FRONTEND_URL` variable |
| `advanced_rag/docker-compose.prod.yml` | **Unchanged** | Already has all services |
| `advanced_rag_fe/` (all frontend code) | **Unchanged** | Deploys to Vercel as-is |
| `advanced_rag_fe/vercel.json` | **New** | SPA rewrites for React Router |
| `advanced_rag_fe/.env.production` | **New** | Production API URL |

**Total changes: 2 new files, 2 minor edits. Zero code splitting.**
