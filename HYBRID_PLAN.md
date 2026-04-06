# Hybrid Deployment Plan: Vercel + Mac Mini

## Overview

Split the monolithic FastAPI RAG backend into two independently deployed apps:

| Layer | Where | What |
|---|---|---|
| Light backend | Vercel Serverless | Auth, upload, routing |
| AI inference | Mac Mini (Cloudflare Tunnel) | Reranker, Qdrant, embeddings, LLM |
| Database | Neon (managed, free) | PostgreSQL |
| Cache / Queue | Upstash (managed, free) | Redis |

**Why split?** The local reranker (`BAAI/bge-reranker-base`) requires PyTorch (~550MB). Vercel has a 50MB bundle limit. Qdrant also runs locally. Everything else moves to managed free-tier services.

---

## Architecture

```
Client
  └─► Vercel Serverless (vercel_app/)
        ├── POST /register          → Neon PostgreSQL
        ├── POST /login             → Neon PostgreSQL
        ├── POST /upload            → Upstash Redis (ARQ job queue)
        ├── POST /upload-file       → Upstash Redis (ARQ job queue)
        ├── GET  /health            → static response
        ├── POST /query  ───────────────────────────────────────┐
        └── POST /rag    ───────────────────────────────────────┤
                                     Cloudflare Tunnel (HTTPS)  │
                                                                 ▼
                                          Mac Mini (inference_server/)
                                            POST /infer/query
                                            POST /infer/rag
                                            GET  /infer/health
                                            ├── Qdrant (local Docker)
                                            ├── Reranker (local PyTorch)
                                            ├── Embeddings (OpenRouter API)
                                            └── LLM (Gemini via OpenRouter)

Mac Mini also runs:
  └── ARQ Worker → polls Upstash Redis → processes ingestion jobs
```

---

## What Runs Where

### On Vercel (serverless functions)
- User registration & login (JWT auth)
- Document upload (queues jobs to Upstash Redis)
- `/query` and `/rag` — **proxy only** to Mac Mini inference server
- No AI, no Qdrant, no PyTorch

### On Mac Mini
- **Inference server** (FastAPI, port 8001) — handles all AI work
- **ARQ worker** — processes document ingestion jobs from Upstash queue
- **Qdrant** (Docker) — vector database

### Managed Services (free tier)
- **Neon** — PostgreSQL (users, chunks, query logs)
- **Upstash** — Redis (cache, rate limiting, ARQ job queue)

---

## New Directory Structure

```
advanc_rag/
├── advanced_rag/               (existing — keep as reference)
│
├── vercel_app/                 (NEW — Vercel light backend)
│   ├── api/
│   │   └── index.py            (Vercel ASGI entry point)
│   ├── src/
│   │   ├── db/
│   │   │   ├── models.py       (same ORM models)
│   │   │   └── session.py      (MODIFIED: NullPool for serverless)
│   │   └── services/
│   │       ├── auth.py         (unchanged)
│   │       ├── cache.py        (unchanged)
│   │       ├── file_parser.py  (unchanged)
│   │       ├── logging.py      (unchanged)
│   │       ├── rate_limiter.py (unchanged)
│   │       └── inference_client.py  (NEW — httpx proxy to Mac Mini)
│   ├── requirements.txt        (no torch / transformers / qdrant-client)
│   └── vercel.json
│
└── inference_server/           (NEW — Mac Mini FastAPI app)
    ├── src/
    │   ├── main.py             (NEW — /infer/* routes + secret middleware)
    │   ├── db/
    │   │   ├── models.py       (unchanged)
    │   │   └── session.py      (unchanged)
    │   └── services/
    │       ├── auth.py
    │       ├── cache.py
    │       ├── chunking.py
    │       ├── embeddings.py
    │       ├── file_parser.py
    │       ├── ingestion.py
    │       ├── logging.py
    │       ├── rag.py
    │       ├── rate_limiter.py
    │       ├── reranker.py     (stays here — PyTorch runs on Mac Mini)
    │       ├── retrieval.py
    │       └── vector_store.py
    ├── worker/
    │   ├── celery_app.py       (MODIFIED: add rediss:// TLS support)
    │   └── tasks.py            (unchanged)
    ├── docker-compose.yml      (MODIFIED: Qdrant only)
    ├── run_inference.sh        (NEW)
    └── run_worker.sh           (NEW)
```

---

## Files to Create / Modify

### NEW: `vercel_app/src/services/inference_client.py`
The HTTP bridge from Vercel to Mac Mini. Most critical new file.

```python
import os
import httpx
from fastapi import HTTPException

INFERENCE_SERVER_URL = os.getenv("INFERENCE_SERVER_URL")
INFERENCE_SECRET = os.getenv("INFERENCE_SECRET")


async def _post(path: str, payload: dict) -> dict:
    if not INFERENCE_SERVER_URL:
        raise HTTPException(status_code=503, detail="Inference server not configured")
    url = f"{INFERENCE_SERVER_URL.rstrip('/')}{path}"
    headers = {"X-Inference-Secret": INFERENCE_SECRET, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Inference server timed out")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Inference server unreachable")


async def proxy_query(user_id: str, query: str, top_k: int) -> dict:
    return await _post("/infer/query", {"user_id": user_id, "query": query, "top_k": top_k})


async def proxy_rag(user_id: str, query: str, top_k: int) -> dict:
    return await _post("/infer/rag", {"user_id": user_id, "query": query, "top_k": top_k})
```

---

### NEW: `inference_server/src/main.py`
Mac Mini FastAPI app with shared-secret middleware.

```python
import os
from fastapi import FastAPI
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import asyncio
from src.services.rag import generate_rag_answer
from src.services.retrieval import retrieve_documents
from src.services.vector_store import create_collection
from src.db.session import engine, Base

INFERENCE_SECRET = os.getenv("INFERENCE_SECRET")


class SecretMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/infer/health":
            return await call_next(request)
        if request.headers.get("X-Inference-Secret") != INFERENCE_SECRET:
            return Response("Forbidden", status_code=403)
        return await call_next(request)


app = FastAPI(title="RAG Inference Server")
app.add_middleware(SecretMiddleware)


@app.on_event("startup")
async def startup():
    Base.metadata.create_all(bind=engine)
    create_collection()


@app.get("/infer/health")
async def health():
    return {"status": "running", "service": "inference"}


@app.post("/infer/query")
async def infer_query(request: InferRequest):
    results = await asyncio.to_thread(
        retrieve_documents, request.user_id, request.query, request.top_k
    )
    return {"results": results}


@app.post("/infer/rag")
async def infer_rag(request: InferRequest):
    return await asyncio.to_thread(
        generate_rag_answer, request.user_id, request.query, request.top_k
    )
```

---

### MODIFIED: `vercel_app/api/index.py`
- `/query` and `/rag` call `inference_client.proxy_*` instead of services directly
- Rate limiting for `/rag` still checked on Vercel (fast Redis call before Mac Mini round-trip)
- ARQ pool initialized lazily (serverless has no persistent startup)
- No `create_collection()` or `Base.metadata.create_all()` on startup

---

### MODIFIED: `vercel_app/src/db/session.py`
```python
# NullPool is critical — serverless functions cannot maintain connection pools
engine = create_engine(DATABASE_URL, poolclass=NullPool)
```

---

### MODIFIED: `inference_server/worker/celery_app.py`
Add TLS support for Upstash `rediss://` URLs:
```python
ssl = REDIS_URL.startswith("rediss://")
clean_url = REDIS_URL.replace("rediss://", "").replace("redis://", "")
# pass ssl=ssl to RedisSettings(...)
```

---

### MODIFIED: `inference_server/docker-compose.yml`
Qdrant only — postgres and redis removed (now managed):
```yaml
services:
  qdrant:
    image: qdrant/qdrant
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage
    restart: unless-stopped

volumes:
  qdrant_data:
```

---

### NEW: `vercel_app/vercel.json`
```json
{
  "version": 2,
  "builds": [
    {
      "src": "api/index.py",
      "use": "@vercel/python",
      "config": { "maxLambdaSize": "50mb" }
    }
  ],
  "functions": {
    "api/index.py": { "maxDuration": 60 }
  },
  "routes": [
    { "src": "/(.*)", "dest": "api/index.py" }
  ]
}
```
> `maxDuration: 60` requires **Vercel Pro** plan. RAG calls take 10–30s — the free tier's 10s limit will time out.

---

### NEW: `vercel_app/requirements.txt`
```
fastapi>=0.128.0
uvicorn>=0.40.0
httpx>=0.28.1
sqlalchemy>=2.0.46
psycopg2-binary>=2.9.11
arq>=0.26.1
redis>=4.2.0,<6
PyJWT>=2.8.0
bcrypt>=4.0.0
python-dotenv>=1.2.1
python-multipart>=0.0.9
PyMuPDF>=1.24.0
python-docx>=1.1.0
structlog>=24.0.0
```
**Excluded:** `torch`, `transformers`, `qdrant-client` — these stay on Mac Mini only.

---

### NEW: `inference_server/run_inference.sh`
```bash
#!/bin/bash
set -e
cd "$(dirname "$0")"
source .env
gunicorn src.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --bind 0.0.0.0:8001 \
    --timeout 180 \
    --graceful-timeout 30
```

### NEW: `inference_server/run_worker.sh`
```bash
#!/bin/bash
set -e
cd "$(dirname "$0")"
source .env
python -m arq src.worker.tasks.WorkerSettings
```

---

## Environment Variables

### Vercel (set via `vercel env add` or dashboard)

| Variable | Value |
|---|---|
| `POSTGRES_URL` | Neon PostgreSQL connection string |
| `REDIS_URL` | Upstash Redis TLS URL (`rediss://...`) |
| `JWT_SECRET` | Same value as Mac Mini |
| `INFERENCE_SERVER_URL` | `https://rag-infer.yourdomain.com` |
| `INFERENCE_SECRET` | 32-byte hex: `python -c "import secrets; print(secrets.token_hex(32))"` |

### Mac Mini (`inference_server/.env`)

| Variable | Value |
|---|---|
| `POSTGRES_URL` | Same Neon string |
| `REDIS_URL` | Same Upstash TLS string |
| `JWT_SECRET` | Same value as Vercel |
| `QDRANT_URL` | `http://localhost:6333` |
| `OPENROUTER_API_KEY` | Your OpenRouter key |
| `INFERENCE_SECRET` | Same value as Vercel |

> **JWT flow:** Vercel validates the JWT on `/query` and `/rag`, extracts `user_id`, and passes it in the JSON body to the Mac Mini. The Mac Mini trusts this `user_id` — it does not re-validate the token. The `INFERENCE_SECRET` header protects this trust channel.

---

## Cloudflare Tunnel Setup (Mac Mini)

```bash
# Install
brew install cloudflared

# Authenticate (opens browser)
cloudflared tunnel login

# Create a named tunnel (persists across restarts)
cloudflared tunnel create rag-inference
# → saves credentials to ~/.cloudflare/tunnels/<UUID>.json
# → prints your TUNNEL-UUID
```

Create `~/.cloudflare/config.yml`:
```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /Users/alokv/.cloudflare/tunnels/<TUNNEL-UUID>.json

ingress:
  - hostname: rag-infer.yourdomain.com
    service: http://localhost:8001
  - service: http_status:404
```

In Cloudflare DNS dashboard, add a CNAME:
- Name: `rag-infer`
- Target: `<TUNNEL-UUID>.cfargotunnel.com`

```bash
# Install as LaunchAgent (auto-starts on reboot)
cloudflared service install

# Or run manually
cloudflared tunnel run rag-inference
```

**Quick test (no domain needed):**
```bash
cloudflared tunnel --url http://localhost:8001
# Prints a temporary trycloudflare.com URL — use this as INFERENCE_SERVER_URL for testing
```

---

## Running Services on Mac Mini

```bash
# 1. Start Qdrant
docker compose up -d qdrant

# 2. Start inference server (port 8001)
./run_inference.sh

# 3. Start ARQ worker (polls Upstash for ingestion jobs)
./run_worker.sh

# 4. Start Cloudflare tunnel (if not installed as service)
cloudflared tunnel run rag-inference
```

---

## Verification Steps

### Phase 1 — Mac Mini local test
```bash
curl http://localhost:8001/infer/health
# → {"status":"running","service":"inference"}

curl -X POST http://localhost:8001/infer/rag \
  -H "X-Inference-Secret: <your-secret>" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","query":"hello","top_k":3}'
# → {"answer":"...","sources":[...]}

# Verify secret rejection
curl -X POST http://localhost:8001/infer/rag \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","query":"hello","top_k":3}'
# → 403 Forbidden
```

### Phase 2 — Cloudflare Tunnel
```bash
curl https://rag-infer.yourdomain.com/infer/health
# → {"status":"running","service":"inference"}
```

### Phase 3 — Vercel local dev
```bash
cd vercel_app
vercel dev
# Set INFERENCE_SERVER_URL=https://rag-infer.yourdomain.com in .env

curl -X POST http://localhost:3000/register -d '{"email":"test@test.com","password":"pass"}'
curl -X POST http://localhost:3000/login -d '{"email":"test@test.com","password":"pass"}'
# → {"token":"..."}

curl -X POST http://localhost:3000/rag \
  -H "Authorization: Bearer <token>" \
  -d '{"query":"what is RAG?","top_k":3}'
# → answer proxied from Mac Mini
```

### Phase 4 — Vercel production
```bash
vercel env add POSTGRES_URL
vercel env add REDIS_URL
vercel env add JWT_SECRET
vercel env add INFERENCE_SERVER_URL
vercel env add INFERENCE_SECRET
vercel deploy --prod
```

### Phase 5 — End-to-end regression
1. Upload a document via `POST /upload-file` on Vercel
2. Check Upstash dashboard → job appears in queue
3. Mac Mini ARQ worker logs → `document_ingested`
4. Check Qdrant: `curl http://localhost:6333/collections/documents` → vector count increases
5. Run `/rag` query → answer references the uploaded document

---

## Known Pitfalls

| Issue | Mitigation |
|---|---|
| Reranker RAM: ~550MB per worker | 2 gunicorn workers = ~1.1GB total — fine on Mac Mini 16GB+ |
| Vercel free tier timeout: 10s | Requires Vercel **Pro** for `maxDuration: 60` |
| Upstash free tier: 10k cmds/day | Monitor usage; upgrade at ~$0.20/100k cmds |
| PyMuPDF on Vercel Linux x86_64 | Pre-built Linux wheels available — verify in Phase 3 |
| Reranker cold start on first request | First `/rag` call after restart may take 5–10s extra |
| ARQ pool on Vercel is stateless | Must initialize lazily per-request, not in startup event |
| Neon connection limit on free tier | Using NullPool + Neon's built-in PgBouncer handles this |

---

## Summary: What Changes vs. What Stays the Same

| Component | Status | Notes |
|---|---|---|
| `services/rag.py` | Unchanged | Moves to inference_server only |
| `services/reranker.py` | Unchanged | Moves to inference_server only |
| `services/retrieval.py` | Unchanged | Moves to inference_server only |
| `services/vector_store.py` | Unchanged | Moves to inference_server only |
| `services/embeddings.py` | Unchanged | Moves to inference_server only |
| `services/auth.py` | Unchanged | Copied to both apps |
| `services/cache.py` | Unchanged | Copied to both apps |
| `services/rate_limiter.py` | Unchanged | Copied to both apps |
| `services/file_parser.py` | Unchanged | Vercel only (parsing before queue) |
| `db/models.py` | Unchanged | Copied to both apps |
| `db/session.py` | **Modified** | NullPool on Vercel, standard on Mac Mini |
| `worker/celery_app.py` | **Modified** | Add `rediss://` TLS support for Upstash |
| `main.py` | **Split** | Light routes → Vercel; inference routes → Mac Mini |
| `docker-compose.yml` | **Modified** | Qdrant only (remove postgres + redis) |
| `inference_client.py` | **New** | httpx async bridge Vercel → Mac Mini |
| `inference_server/main.py` | **New** | Mac Mini FastAPI with secret middleware |
| `vercel.json` | **New** | Vercel routing + 60s timeout |
| `run_inference.sh` | **New** | Gunicorn startup on port 8001 |
| `run_worker.sh` | **New** | ARQ worker startup |
