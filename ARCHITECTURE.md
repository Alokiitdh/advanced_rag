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
                          │  POST /upload    GET /health       │
                          │  POST /query     GET /check-conn   │
                          │  POST /rag                         │
                          └──┬──────┬──────┬─────────────────┘
                             │      │      │
              ┌──────────────┘      │      └──────────────┐
              ▼                     ▼                      ▼
   ┌─────────────────┐  ┌─────────────────┐   ┌─────────────────┐
   │  Rate Limiter    │  │  Retrieval      │   │  RAG Service     │
   │  (Redis)         │  │  Service        │   │  (Orchestrator)  │
   └─────────────────┘  └────────┬────────┘   └───┬─────┬───────┘
                                 │                 │     │
                                 │                 │     ▼
                                 │                 │  ┌──────────────┐
                                 │                 │  │  Reranker     │
                                 │                 │  │  (bge-base)   │
                                 │                 │  └──────────────┘
                                 │                 │
                                 ▼                 ▼
                          ┌─────────────────────────────┐
                          │       OpenAI API             │
                          │  Embeddings + Chat (GPT-4o)  │
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
              │  │  Celery Worker (Redis broker)         │     │
              │  │  Async document ingestion              │     │
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
│   │   └── session.py           # Database engine & session factory
│   ├── services/
│   │   ├── cache.py             # Redis get/set with JSON serialization
│   │   ├── chunking.py          # Sliding-window text chunking
│   │   ├── embeddings.py        # OpenAI embedding generation
│   │   ├── ingestion.py         # Document processing pipeline
│   │   ├── rag.py               # RAG orchestration (retrieve → rerank → generate)
│   │   ├── rate_limiter.py      # Per-user rate limiting via Redis
│   │   ├── reranker.py          # Cross-encoder reranking (BAAI/bge-reranker-base)
│   │   ├── retrieval.py         # Vector search + chunk text fetching
│   │   └── vector_store.py      # Qdrant client & collection management
│   └── worker/
│       ├── celery_app.py        # Celery configuration
│       └── tasks.py             # Async task: process_document
├── tests/
│   └── test_pipeline.py         # End-to-end integration test
├── docker-compose.yml           # Qdrant, PostgreSQL, Redis containers
├── pyproject.toml               # Dependencies & project metadata
├── .env                         # Environment variables
└── README.md                    # Setup & usage guide
```

---

## Component Details

### API Layer (`src/main.py`)

The FastAPI application exposes five endpoints and runs startup initialization (table creation, Qdrant collection setup).

| Endpoint | Method | Description | Auth | Rate Limit |
|----------|--------|-------------|------|------------|
| `/upload` | POST | Queue document for async ingestion | user_id | 5/min |
| `/query` | POST | Retrieve relevant chunks | user_id | None |
| `/rag` | POST | Generate answer from documents | user_id | 20/min |
| `/health` | GET | Liveness check | None | None |
| `/check-connections` | GET | Verify all service connections | None | None |

### Database Layer (`src/db/`)

**Engine**: PostgreSQL via SQLAlchemy ORM

#### Entity-Relationship Diagram

```
┌───────────────┐       ┌───────────────────┐       ┌───────────────────┐
│     User      │       │     Document      │       │      Chunk        │
├───────────────┤       ├───────────────────┤       ├───────────────────┤
│ id (UUID, PK) │──1:N─▶│ id (UUID, PK)     │──1:N─▶│ id (UUID, PK)     │
│ email         │       │ user_id (FK)      │       │ document_id (FK)  │
│ created_at    │       │ filename          │       │ user_id (FK)      │
└───────────────┘       │ status            │       │ text              │
                        │ created_at        │       │ embedding_id      │
                        └───────────────────┘       │ created_at        │
                                                    └───────────────────┘

┌───────────────────┐
│    QueryLog       │
├───────────────────┤
│ id (UUID, PK)     │
│ user_id           │
│ query_text        │
│ response_time_ms  │
│ created_at        │
└───────────────────┘
```

- **Document.status**: `processing` → `ready` | `failed`
- **Chunk.embedding_id**: Links to the corresponding vector in Qdrant

### Service Layer (`src/services/`)

#### Ingestion Pipeline (`ingestion.py`)

Processes uploaded documents end-to-end:

```
Text Input
    │
    ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  chunk_text   │───▶│  generate    │───▶│  upsert to   │
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

Runs inside a **Celery worker** for non-blocking uploads.

#### Retrieval (`retrieval.py`)

Performs user-scoped semantic search:

1. Generate query embedding via OpenAI
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
- Used in the RAG pipeline to refine vector search results

#### RAG Orchestrator (`rag.py`)

Ties everything together for the `/rag` endpoint:

```
Query
  │
  ├──▶ Cache check (Redis)
  │         │ hit → return cached answer
  │         │ miss ↓
  │
  ├──▶ retrieve_documents(top_k * 3)   ← over-fetch
  │
  ├──▶ rerank(query, chunks, top_k)    ← refine
  │
  ├──▶ Build context string
  │
  ├──▶ GPT-4o-mini completion
  │
  ├──▶ Cache answer (300s TTL)
  │
  ├──▶ Log to QueryLog (latency)
  │
  └──▶ Return {answer, sources}
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

### Worker Layer (`src/worker/`)

Celery application with Redis as both broker and result backend.

```
/upload endpoint
      │
      ▼
  celery.send_task("process_document")
      │
      ▼
  Redis Queue ──▶ Celery Worker ──▶ ingest_document()
```

Workers can be scaled horizontally by running multiple instances.

---

## Data Flow: End-to-End Query

```
1. Client sends POST /rag {user_id, query, top_k=5}
2. Rate limiter checks: ≤20 requests/min for this user?
3. Cache lookup: rag:{user_id}:{query}:5
4. Cache miss → retrieve_documents(user_id, query, top_k=15)
   4a. Cache lookup: retrieval:{user_id}:{query}:15
   4b. Cache miss → embed query → search Qdrant (cosine, user_id filter)
   4c. Fetch chunk text from PostgreSQL
   4d. Cache retrieval results
5. Rerank 15 candidates → return top 5
6. Concatenate top-5 chunk texts into context
7. Prompt GPT-4o-mini: system message + context + question
8. Cache {answer, sources} in Redis
9. Log query + latency to PostgreSQL
10. Return response to client
```

---

## Infrastructure (Docker Compose)

| Service | Image | Port | Volume | Purpose |
|---------|-------|------|--------|---------|
| Qdrant | `qdrant/qdrant` | 6333 | `qdrant_data` | Vector storage & ANN search |
| PostgreSQL | `postgres:15` | 5433 | `pgdata` | Relational data (users, docs, chunks, logs) |
| Redis | `redis:7` | 6379 | — | Cache, rate limiting, Celery broker |

All services are containerized. The FastAPI app and Celery worker run on the host.

---

## Security Considerations

| Area | Current State | Notes |
|------|---------------|-------|
| Authentication | Trust-based (client sends `user_id`) | Should add JWT/OAuth |
| Data Isolation | Qdrant queries filtered by `user_id` | Enforced at retrieval layer |
| API Keys | Stored in `.env` file | Not committed to git |
| Rate Limiting | Per-user via Redis | Prevents abuse |
| Input Validation | Pydantic request models | Basic field validation |

---

## Key Design Decisions

1. **Retrieve-then-Rerank**: Fetch 3x candidates from vector search, then rerank to top_k. This balances recall (bi-encoder) with precision (cross-encoder).

2. **User-scoped everything**: All Qdrant searches include a `user_id` filter. Documents, chunks, and queries are all tied to a user.

3. **Async ingestion**: Document processing (chunking + embedding) is CPU/IO-intensive. Celery decouples upload latency from processing time.

4. **Two-level caching**: Both retrieval and RAG answers are cached separately. A cache hit at the RAG level skips everything; a cache hit at retrieval still runs reranking + LLM (cheaper than re-embedding + vector search).

5. **Local reranker**: The BGE cross-encoder runs on the host (CPU/GPU), avoiding external API calls and keeping reranking fast and free.
