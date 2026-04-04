# Advanced RAG System

A production-grade Retrieval-Augmented Generation pipeline with JWT authentication, multi-user isolation, async document processing, cross-encoder reranking, and LLM-powered answer generation.

## Features

- **JWT Authentication** — Secure registration and login with bcrypt password hashing
- **Semantic Search** — OpenAI `text-embedding-3-large` embeddings via OpenRouter + Qdrant vector database
- **Cross-Encoder Reranking** — BAAI/bge-reranker-base refines retrieval results for higher precision
- **Async Ingestion** — ARQ workers process documents in the background
- **File Upload** — Supports PDF, DOCX, and TXT file uploads with automatic text extraction
- **Multi-Tenant** — All data is user-scoped; no cross-user leakage
- **Caching** — Redis caches retrieval results and RAG answers (300s TTL)
- **Rate Limiting** — Per-user limits on uploads (5/min) and queries (20/min)
- **Structured Logging** — JSON logging via structlog for observability
- **Connection Pooling** — SQLAlchemy pool with pre-ping for reliable DB connections
- **Query Logging** — Tracks queries and response latency in PostgreSQL

## Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI |
| Auth | JWT (PyJWT + bcrypt) |
| Vector DB | Qdrant |
| Relational DB | PostgreSQL (pooled connections) |
| Cache & Broker | Redis |
| Embeddings | OpenAI text-embedding-3-large (via OpenRouter) |
| Reranker | BAAI/bge-reranker-base (local) |
| LLM | Google Gemini 2.0 Flash (via OpenRouter) |
| Task Queue | ARQ (async, Redis-backed) |
| Logging | structlog (JSON) |
| File Parsing | PyMuPDF (PDF), python-docx (DOCX) |
| Containers | Docker Compose |

## Prerequisites

- Python 3.13+
- Docker & Docker Compose
- OpenRouter API key
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd advanced_rag
```

### 2. Configure environment variables

Create a `.env` file:

```env
POSTGRES_URL=postgresql://raguser:ragpass@localhost:5433/ragdb
REDIS_URL=redis://localhost:6379
QDRANT_URL=http://localhost:6333
OPENROUTER_API_KEY=sk-or-v1-your-key-here
JWT_SECRET=your-secret-key-change-in-production
```

### 3. Start infrastructure

```bash
docker compose up -d
```

This starts Qdrant (`:6333`), PostgreSQL (`:5433`), and Redis (`:6379`).

### 4. Install dependencies

```bash
uv sync
```

### 5. Start the API server

```bash
# Development
uv run uvicorn src.main:app --reload

# Production (Linux — multi-worker)
./run_prod.sh
```

### 6. Start the ARQ worker (separate terminal)

```bash
uv run arq src.worker.tasks.WorkerSettings
```

## API Endpoints

### Register a user

```bash
curl -X POST http://localhost:8000/register \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "securepass"}'
```

Response: `{"user_id": "uuid", "token": "jwt-token"}`

### Login

```bash
curl -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "securepass"}'
```

Response: `{"user_id": "uuid", "token": "jwt-token"}`

### Upload a document (text)

```bash
curl -X POST http://localhost:8000/upload \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "filename": "notes.txt",
    "text": "Your document text here..."
  }'
```

Response: `{"status": "queued", "job_id": "uuid"}`

### Upload a file (PDF/DOCX/TXT)

```bash
curl -X POST http://localhost:8000/upload-file \
  -H "Authorization: Bearer <token>" \
  -F "file=@document.pdf"
```

Response: `{"status": "queued", "job_id": "uuid", "filename": "document.pdf"}`

### Retrieve relevant chunks

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "query": "What are the key points?",
    "top_k": 5
  }'
```

### Generate a RAG answer

```bash
curl -X POST http://localhost:8000/rag \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "query": "Summarize the main findings",
    "top_k": 5
  }'
```

Response:

```json
{
  "answer": "Based on the documents...",
  "sources": [
    {"document_id": "uuid", "chunk_index": 0},
    {"document_id": "uuid", "chunk_index": 1}
  ]
}
```

### Health check

```bash
curl http://localhost:8000/health
```

### Check service connections

```bash
curl http://localhost:8000/check-connections
```

## How It Works

```
Register/Login → JWT Token

Upload → ARQ Worker → Chunk → Embed → Store (Qdrant + PostgreSQL)

Query → Auth → Embed → Vector Search (top_k x 3) → Rerank (top_k) → Gemini Flash → Answer
```

1. **Authentication**: Users register and login to get a JWT token. All data endpoints require a valid token.
2. **Ingestion**: Documents are chunked (500 chars, 50 overlap), embedded via OpenRouter, and stored in Qdrant + PostgreSQL. Processed asynchronously via ARQ workers.
3. **Retrieval**: Queries are embedded and matched against Qdrant vectors, filtered by user.
4. **Reranking**: Retrieved chunks are re-scored by a cross-encoder model for better precision.
5. **Generation**: Top chunks are sent as context to Gemini 2.0 Flash (via OpenRouter), which generates a grounded answer.

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed system design and [PLAN.md](PLAN.md) for the full pipeline plan.

## Running Tests

```bash
# Ensure Docker services are running
docker compose up -d

# Start the API server
uv run uvicorn src.main:app --reload &

# Start the ARQ worker
uv run arq src.worker.tasks.WorkerSettings &

# Run integration tests
uv run python -m pytest tests/test_pipeline.py -v
```

Tests cover: health check, registration/login flow, token protection, and the full upload-to-RAG pipeline.

## Project Structure

```
src/
├── main.py              # FastAPI app & routes (auth + data endpoints)
├── db/
│   ├── models.py        # ORM models (User, Document, Chunk, QueryLog)
│   └── session.py       # Database engine, connection pool & session
├── services/
│   ├── auth.py          # JWT authentication (register, login, token validation)
│   ├── cache.py         # Redis cache utilities
│   ├── chunking.py      # Text chunking (sliding window)
│   ├── embeddings.py    # Embedding generation (OpenRouter)
│   ├── file_parser.py   # File text extraction (PDF, DOCX, TXT)
│   ├── ingestion.py     # Document processing pipeline
│   ├── logging.py       # Structured JSON logging (structlog)
│   ├── rag.py           # RAG orchestration
│   ├── rate_limiter.py  # Redis-based rate limiting
│   ├── reranker.py      # Cross-encoder reranking
│   ├── retrieval.py     # Vector search & retrieval
│   └── vector_store.py  # Qdrant collection management
└── worker/
    ├── celery_app.py    # ARQ Redis settings
    └── tasks.py         # ARQ async ingestion task & worker config
tests/
└── test_pipeline.py     # Integration tests (auth + full pipeline)
run_prod.sh              # Production startup script (Gunicorn + Uvicorn workers)
```
