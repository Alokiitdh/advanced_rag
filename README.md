# Advanced RAG System

A production-grade Retrieval-Augmented Generation pipeline with multi-user isolation, async document processing, cross-encoder reranking, and LLM-powered answer generation.

## Features

- **Semantic Search** — OpenAI `text-embedding-3-large` embeddings + Qdrant vector database
- **Cross-Encoder Reranking** — BAAI/bge-reranker-base refines retrieval results for higher precision
- **Async Ingestion** — Celery workers process documents in the background
- **Multi-Tenant** — All data is user-scoped; no cross-user leakage
- **Caching** — Redis caches retrieval results and RAG answers (300s TTL)
- **Rate Limiting** — Per-user limits on uploads (5/min) and queries (20/min)
- **Query Logging** — Tracks queries and response latency in PostgreSQL

## Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI |
| Vector DB | Qdrant |
| Relational DB | PostgreSQL |
| Cache & Broker | Redis |
| Embeddings | OpenAI text-embedding-3-large |
| Reranker | BAAI/bge-reranker-base (local) |
| LLM | GPT-4o-mini |
| Task Queue | Celery |
| Containers | Docker Compose |

## Prerequisites

- Python 3.13+
- Docker & Docker Compose
- OpenAI API key
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
OPENAI_API_KEY=sk-your-key-here
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
uv run uvicorn src.main:app --reload
```

### 6. Start the Celery worker (separate terminal)

```bash
uv run celery -A src.worker.celery_app worker --loglevel=info
```

## API Endpoints

### Upload a document

```bash
curl -X POST http://localhost:8000/upload \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-1",
    "filename": "notes.txt",
    "text": "Your document text here..."
  }'
```

Response: `{"status": "queued"}`

### Retrieve relevant chunks

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-1",
    "query": "What are the key points?",
    "top_k": 5
  }'
```

### Generate a RAG answer

```bash
curl -X POST http://localhost:8000/rag \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-1",
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
Upload → Celery Worker → Chunk → Embed → Store (Qdrant + PostgreSQL)

Query → Embed → Vector Search (top_k × 3) → Rerank (top_k) → LLM → Answer
```

1. **Ingestion**: Documents are chunked (500 chars, 50 overlap), embedded via OpenAI, and stored in Qdrant + PostgreSQL
2. **Retrieval**: Queries are embedded and matched against Qdrant vectors, filtered by user
3. **Reranking**: Retrieved chunks are re-scored by a cross-encoder model for better precision
4. **Generation**: Top chunks are sent as context to GPT-4o-mini, which generates a grounded answer

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed system design and [PLAN.md](PLAN.md) for the full pipeline plan.

## Running Tests

```bash
# Ensure Docker services are running
docker compose up -d

# Start the API server
uv run uvicorn src.main:app --reload &

# Run integration tests
uv run pytest tests/test_pipeline.py -v
```

## Project Structure

```
src/
├── main.py              # FastAPI app & routes
├── db/
│   ├── models.py        # ORM models (User, Document, Chunk, QueryLog)
│   └── session.py       # Database engine & session
├── services/
│   ├── cache.py         # Redis cache utilities
│   ├── chunking.py      # Text chunking (sliding window)
│   ├── embeddings.py    # OpenAI embedding generation
│   ├── ingestion.py     # Document processing pipeline
│   ├── rag.py           # RAG orchestration
│   ├── rate_limiter.py  # Redis-based rate limiting
│   ├── reranker.py      # Cross-encoder reranking
│   ├── retrieval.py     # Vector search & retrieval
│   └── vector_store.py  # Qdrant collection management
└── worker/
    ├── celery_app.py    # Celery configuration
    └── tasks.py         # Async ingestion task
```
