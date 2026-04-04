# RAG System Architecture

## High-Level Architecture

```
                          ┌──────────────────────────────────┐
                          │           Client / UI             │
                          └──────────┬───────────────────────┘
                                     │ HTTP
                                     ▼
                          ┌──────────────────────────────────┐
                          │        FastAPI Application        │
                          │          (src/main.py)            │
                          │                                    │
                          │  POST /register   GET /health      │
                          │  POST /login      GET /check-conn  │
                          │  POST /upload     POST /query      │
                          │  POST /upload-file POST /rag       │
                          └──┬──────┬──────┬─────────────────┘
                             │      │      │
              ┌──────────────┘      │      └──────────────┐
              ▼                     ▼                      ▼
   ┌─────────────────┐  ┌─────────────────┐   ┌─────────────────┐
   │  Auth Service    │  │  Retrieval      │   │  RAG Service     │
   │  (JWT + bcrypt)  │  │  Service        │   │  (Orchestrator)  │
   └─────────────────┘  └────────┬────────┘   └───┬─────┬───────┘
                                 │                 │     │
   ┌─────────────────┐          │                 │     ▼
   │  Rate Limiter    │          │                 │  ┌──────────────┐
   │  (Redis)         │          │                 │  │  Reranker     │
   └─────────────────┘          │                 │  │  (bge-base)   │
                                 │                 │  └──────────────┘
   ┌─────────────────┐          │                 │
   │  File Parser     │          │                 │
   │  (PDF/DOCX/TXT)  │          │                 │
   └─────────────────┘          ▼                 ▼
                          ┌─────────────────────────────┐
                          │       OpenRouter API          │
                          │  Embeddings + Gemini Flash    │
                          └─────────────────────────────┘
              ┌──────────────────────────────────────────────┐
              │               Infrastructure                  │
              │                                                │
              │  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
              │  │  Qdrant   │  │ Postgres │  │  Redis   │    │
              │  │  :6333    │  │  :5433   │  │  :6379   │    │
              │  │ (vectors) │  │ (data)   │  │ (cache)  │    │
              │  └──────────┘  └──────────┘  └──────────┘    │
              │                                                │
              │  ┌──────────────────────────────────────┐     │
              │  │  ARQ Worker (Redis broker)             │     │
              │  │  Async document ingestion               │     │
              │  └──────────────────────────────────────┘     │
              └──────────────────────────────────────────────┘
```

---

## Directory Structure

```
advanced_rag/
├── src/
│   ├── main.py                  # FastAPI app, route definitions, startup hooks
│   ├── db/
│   │   ├── models.py            # SQLAlchemy ORM models (User, Document, Chunk, QueryLog)
│   │   └── session.py           # Database engine, connection pool & session factory
│   ├── services/
│   │   ├── auth.py              # JWT authentication (register, login, token validation)
│   │   ├── cache.py             # Redis get/set with JSON serialization
│   │   ├── chunking.py          # Sliding-window text chunking
│   │   ├── embeddings.py        # Embedding generation (OpenRouter)
│   │   ├── file_parser.py       # Text extraction (PDF, DOCX, TXT)
│   │   ├── ingestion.py         # Document processing pipeline
│   │   ├── logging.py           # Structured JSON logging (structlog)
│   │   ├── rag.py               # RAG orchestration (retrieve -> rerank -> generate)
│   │   ├── rate_limiter.py      # Per-user rate limiting via Redis
│   │   ├── reranker.py          # Cross-encoder reranking (BAAI/bge-reranker-base)
│   │   ├── retrieval.py         # Vector search + chunk text fetching
│   │   └── vector_store.py      # Qdrant client & collection management
│   └── worker/
│       ├── celery_app.py        # ARQ Redis settings helper
│       └── tasks.py             # ARQ task: process_document + WorkerSettings
├── tests/
│   └── test_pipeline.py         # Integration tests (auth + full pipeline)
├── docker-compose.yml           # Qdrant, PostgreSQL, Redis containers
├── pyproject.toml               # Dependencies & project metadata
├── run_prod.sh                  # Production startup (Gunicorn + Uvicorn workers)
├── .env                         # Environment variables
├── ARCHITECTURE.md              # This file
├── PLAN.md                      # Pipeline plan & design decisions
└── README.md                    # Setup & usage guide
```

---

## Component Details

### API Layer (`src/main.py`)

The FastAPI application exposes eight endpoints and runs startup initialization (table creation, Qdrant collection setup, ARQ pool creation).

| Endpoint | Method | Description | Auth | Rate Limit |
|----------|--------|-------------|------|------------|
| `/register` | POST | Create user account | None | None |
| `/login` | POST | Authenticate and get JWT | None | None |
| `/upload` | POST | Queue text document for async ingestion | JWT | 5/min |
| `/upload-file` | POST | Upload PDF/DOCX/TXT file for ingestion | JWT | 5/min |
| `/query` | POST | Retrieve relevant chunks | JWT | None |
| `/rag` | POST | Generate answer from documents | JWT | 20/min |
| `/health` | GET | Liveness check | None | None |
| `/check-connections` | GET | Verify all service connections | None | None |

### Authentication (`src/services/auth.py`)

JWT-based authentication flow:

```
Register: email + password -> bcrypt hash -> store User -> return JWT
Login:    email + password -> verify hash -> return JWT
Request:  Authorization: Bearer <token> -> decode JWT -> extract user_id
```

- Tokens expire after 24 hours (configurable via `JWT_EXPIRY_HOURS`)
- Passwords hashed with bcrypt (salt rounds auto-generated)
- `get_current_user` FastAPI dependency protects all data endpoints

### Database Layer (`src/db/`)

**Engine**: PostgreSQL via SQLAlchemy ORM with connection pooling (`pool_size=20`, `max_overflow=10`, `pool_pre_ping=True`, `pool_recycle=300s`)

#### Entity-Relationship Diagram

```
┌───────────────────┐       ┌───────────────────┐       ┌───────────────────┐
│       User        │       │     Document      │       │      Chunk        │
├───────────────────┤       ├───────────────────┤       ├───────────────────┤
│ id (UUID, PK)     │──1:N─>│ id (UUID, PK)     │──1:N─>│ id (UUID, PK)     │
│ email (idx)       │       │ user_id (FK, idx)  │       │ document_id (FK,idx)│
│ password_hash     │       │ filename          │       │ user_id (FK, idx)  │
│ created_at        │       │ status            │       │ text              │
└───────────────────┘       │ created_at        │       │ embedding_id (idx) │
        │                   │ updated_at        │       │ created_at        │
        │                   └───────────────────┘       └───────────────────┘
        │
        │ 1:N
        ▼
┌───────────────────┐
│    QueryLog       │
├───────────────────┤
│ id (UUID, PK)     │
│ user_id (FK, idx) │
│ query_text        │
│ response_time_ms  │  (Float)
│ created_at        │
└───────────────────┘
```

- **Document.status**: `processing` -> `ready` | `failed`
- **Chunk.embedding_id**: Links to the corresponding vector in Qdrant
- All foreign keys use `ondelete="CASCADE"` — deleting a user cascades to documents, chunks, and query logs
- Indexes on all foreign keys and `email` for query performance

### Service Layer (`src/services/`)

#### File Parser (`file_parser.py`)

Extracts text from uploaded files:

| Format | Library | Method |
|--------|---------|--------|
| PDF | PyMuPDF (`fitz`) | Page-by-page text extraction |
| DOCX | python-docx | Paragraph extraction |
| TXT | Built-in | UTF-8 decode |

#### Ingestion Pipeline (`ingestion.py`)

Processes uploaded documents end-to-end:

```
Text Input
    │
    ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  chunk_text   │───>│  generate    │───>│  upsert to   │
│  (500 chars,  │    │  embedding   │    │  Qdrant      │
│   50 overlap) │    │  per chunk   │    │  (vectors)   │
└──────────────┘    └──────────────┘    └──────────────┘
                                              │
                                              ▼
                                        ┌──────────────┐
                                        │  Insert      │
                                        │  Chunks to   │
                                        │  PostgreSQL  │
                                        └──────────────┘
```

Runs inside an **ARQ worker** for non-blocking uploads.

#### Retrieval (`retrieval.py`)

Performs user-scoped semantic search:

1. Generate query embedding via OpenRouter
2. Search Qdrant with `user_id` filter (cosine similarity)
3. Fetch chunk text from PostgreSQL by `embedding_id`
4. Return scored results: `[{score, text, document_id, chunk_index}]`

#### Reranker (`reranker.py`)

Cross-encoder reranking using `BAAI/bge-reranker-base`:

```
Input:  [(query, chunk_1), (query, chunk_2), ..., (query, chunk_N)]
          │
          ▼
   Cross-encoder scoring (PyTorch)
          │
          ▼
Output: Top-K chunks sorted by relevance score
```

- Loaded once at module import (model stays in memory)
- Adds `rerank_score` field to each document
- Handles single-document edge case (0-d tensor)
- Used in the RAG pipeline to refine vector search results

#### RAG Orchestrator (`rag.py`)

Ties everything together for the `/rag` endpoint:

```
Query
  │
  ├──> Cache check (Redis)
  │         │ hit -> return cached answer
  │         │ miss ↓
  │
  ├──> retrieve_documents(top_k * 3)   <- over-fetch
  │
  ├──> rerank(query, chunks, top_k)    <- refine
  │
  ├──> Build context string
  │
  ├──> Gemini 2.0 Flash completion (via OpenRouter)
  │
  ├──> Cache answer (300s TTL)
  │
  ├──> Log to QueryLog (latency as Float)
  │
  └──> Return {answer, sources}
```

#### Caching (`cache.py`)

Redis-backed JSON cache used at two levels:

- **Retrieval cache**: Avoids re-running vector search for repeated queries
- **RAG cache**: Avoids re-calling the LLM for repeated queries

Both use a 300-second TTL.

#### Rate Limiter (`rate_limiter.py`)

Sliding-window counter per `(user_id, action)`:

```
Key: rate_limit:{user_id}:{action}
Operation: INCR + EXPIRE (atomic)
```

#### Structured Logging (`logging.py`)

JSON logging via structlog, configured at app startup:

- ISO timestamps
- Log level and logger name
- Exception formatting
- Context variables (user_id, endpoint, job_id)

### Worker Layer (`src/worker/`)

ARQ application with Redis as both broker and result backend.

```
/upload endpoint
      │
      ▼
  arq_pool.enqueue_job("process_document")
      │
      ▼
  Redis Queue ──> ARQ Worker ──> ingest_document()
```

Configuration (`WorkerSettings`):
- `max_jobs`: 10 concurrent jobs
- `job_timeout`: 300 seconds per ingestion task
- Workers can be scaled by running multiple `arq` processes

---

## Data Flow: End-to-End Query

```
1. Client sends POST /rag with JWT Bearer token
2. Auth middleware validates JWT, extracts user_id
3. Rate limiter checks: <=20 requests/min for this user?
4. Cache lookup: rag:{user_id}:{query}:5
5. Cache miss -> retrieve_documents(user_id, query, top_k=15)
   5a. Cache lookup: retrieval:{user_id}:{query}:15
   5b. Cache miss -> embed query via OpenRouter -> search Qdrant (cosine, user_id filter)
   5c. Fetch chunk text from PostgreSQL
   5d. Cache retrieval results
6. Rerank 15 candidates -> return top 5
7. Concatenate top-5 chunk texts into context
8. Prompt Gemini 2.0 Flash (via OpenRouter): system message + context + question
9. Cache {answer, sources} in Redis
10. Log query + latency (Float) to PostgreSQL
11. Return response to client
```

---

## Infrastructure (Docker Compose)

| Service | Image | Port | Volume | Purpose |
|---------|-------|------|--------|---------|
| Qdrant | `qdrant/qdrant` | 6333 | `qdrant_data` | Vector storage & ANN search |
| PostgreSQL | `postgres:15` | 5433 | `pgdata` | Relational data (users, docs, chunks, logs) |
| Redis | `redis:7` | 6379 | — | Cache, rate limiting, ARQ broker |

All services are containerized. The FastAPI app and ARQ worker run on the host (or via Gunicorn in production).

---

## Security

| Area | Implementation | Details |
|------|---------------|---------|
| Authentication | JWT (PyJWT) | 24h expiry, HS256 algorithm |
| Password Storage | bcrypt | Auto-generated salt, hash stored in PostgreSQL |
| Data Isolation | Qdrant user_id filter | Enforced at retrieval layer |
| API Keys | `.env` file | Not committed to git (in `.gitignore`) |
| Rate Limiting | Per-user via Redis | Prevents abuse (5 uploads/min, 20 queries/min) |
| Input Validation | Pydantic models | Type-safe request parsing |
| DB Integrity | CASCADE deletes + FK constraints | No orphaned records |

---

## Key Design Decisions

1. **Retrieve-then-Rerank**: Fetch 3x candidates from vector search, then rerank to top_k. This balances recall (bi-encoder) with precision (cross-encoder).

2. **User-scoped everything**: All Qdrant searches include a `user_id` filter. Documents, chunks, and queries are all tied to an authenticated user.

3. **Async ingestion via ARQ**: Document processing (text extraction + chunking + embedding) is CPU/IO-intensive. ARQ decouples upload latency from processing time, with async-native Python support and simpler config than Celery.

4. **Two-level caching**: Both retrieval and RAG answers are cached separately. A cache hit at the RAG level skips everything; a cache hit at retrieval still runs reranking + LLM (cheaper than re-embedding + vector search).

5. **Local reranker**: The BGE cross-encoder runs on the host (CPU/GPU), avoiding external API calls and keeping reranking fast and free.

6. **JWT authentication**: Stateless tokens eliminate the need for session storage. The `get_current_user` dependency injects user_id into every protected endpoint, replacing the old trust-based `user_id` parameter.

7. **OpenRouter as LLM gateway**: Single API for both embeddings (OpenAI models) and chat completions (Gemini Flash), avoiding multiple API key management.
