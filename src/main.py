import os
from fastapi import FastAPI
from qdrant_client import QdrantClient
import psycopg2
import redis
from dotenv import load_dotenv
from src.db.session import engine, Base
from src.db import models
from src.services.vector_store import create_collection
from src.services.ingestion import ingest_document
from pydantic import BaseModel
import uuid
from src.services.retrieval import retrieve_documents
from src.services.rag import generate_rag_answer
from arq import create_pool
from src.worker.celery_app import get_redis_settings
from src.services.rate_limiter import check_rate_limit
from fastapi import HTTPException


load_dotenv()

app = FastAPI()
arq_pool = None

class UploadRequest(BaseModel):
    user_id: str
    filename: str
    text: str

class QueryRequest(BaseModel):
    user_id: str
    query: str
    top_k: int = 5

@app.post("/upload")
async def upload_document(request: UploadRequest):

    allowed = check_rate_limit(
        user_id=request.user_id,
        action="upload",
        limit=5
    )

    if not allowed:
        raise HTTPException(status_code=429, detail="Upload rate limit exceeded")

    job = await arq_pool.enqueue_job(
        "process_document",
        user_id=request.user_id,
        filename=request.filename,
        text=request.text
    )

    return {"status": "queued", "job_id": job.job_id}


@app.post("/query")
def query_documents(request: QueryRequest):
    results = retrieve_documents(
        user_id=request.user_id,
        query=request.query,
        top_k=request.top_k
    )
    return {"results": results}

@app.post("/rag")
def rag_query(request: QueryRequest):

    allowed = check_rate_limit(
        user_id=request.user_id,
        action="rag",
        limit=20
    )

    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return generate_rag_answer(
        user_id=request.user_id,
        query=request.query,
        top_k=request.top_k
    )


@app.get("/health")
async def health():
    return {"status": "running"}

@app.on_event("startup")
async def startup_event():
    global arq_pool
    Base.metadata.create_all(bind=engine)
    create_collection()
    arq_pool = await create_pool(get_redis_settings())


@app.get("/check-connections")
def check_connections():
    results = {}

    # Qdrant
    try:
        qdrant = QdrantClient(url=os.getenv("QDRANT_URL"))
        qdrant.get_collections()
        results["qdrant"] = "connected"
    except Exception as e:
        results["qdrant"] = str(e)

    # Postgres
    try:
        conn = psycopg2.connect(os.getenv("POSTGRES_URL"))
        conn.close()
        results["postgres"] = "connected"
    except Exception as e:
        results["postgres"] = str(e)

    # Redis
    try:
        r = redis.from_url(os.getenv("REDIS_URL"))
        r.ping()
        results["redis"] = "connected"
    except Exception as e:
        results["redis"] = str(e)

    return results
