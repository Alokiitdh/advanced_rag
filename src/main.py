import os
import asyncio
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from qdrant_client import QdrantClient
import psycopg2
import redis
from dotenv import load_dotenv
from pydantic import BaseModel
from src.db.session import engine, Base, SessionLocal
from src.db import models
from src.services.vector_store import create_collection
from src.services.retrieval import retrieve_documents
from src.services.rag import generate_rag_answer
from src.services.rate_limiter import check_rate_limit
from src.services.auth import register_user, login_user, get_current_user
from src.services.file_parser import extract_text
from src.services.logging import setup_logging, get_logger
from arq import create_pool
from src.worker.celery_app import get_redis_settings

load_dotenv()
setup_logging()
logger = get_logger("api")

app = FastAPI(title="Advanced RAG API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
arq_pool = None


# --- Request/Response models ---

class AuthRequest(BaseModel):
    email: str
    password: str

class UploadRequest(BaseModel):
    filename: str
    text: str

class QueryRequest(BaseModel):
    query: str
    top_k: int = 5


# --- Auth endpoints (public) ---

@app.post("/register")
def register(request: AuthRequest):
    result = register_user(request.email, request.password)
    logger.info("user_registered", email=request.email)
    return result

@app.post("/login")
def login(request: AuthRequest):
    result = login_user(request.email, request.password)
    logger.info("user_logged_in", email=request.email)
    return result


# --- Protected endpoints ---

@app.post("/upload")
async def upload_document(
    request: UploadRequest,
    user_id: str = Depends(get_current_user),
):
    allowed = check_rate_limit(user_id=user_id, action="upload", limit=5)
    if not allowed:
        raise HTTPException(status_code=429, detail="Upload rate limit exceeded")

    job = await arq_pool.enqueue_job(
        "process_document",
        user_id=user_id,
        filename=request.filename,
        text=request.text,
    )

    logger.info("document_queued", user_id=user_id, filename=request.filename, job_id=job.job_id)
    return {"status": "queued", "job_id": job.job_id}


@app.post("/upload-file")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
    allowed = check_rate_limit(user_id=user_id, action="upload", limit=5)
    if not allowed:
        raise HTTPException(status_code=429, detail="Upload rate limit exceeded")

    file_bytes = await file.read()
    try:
        text = extract_text(file.filename, file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not text.strip():
        raise HTTPException(status_code=400, detail="No text could be extracted from the file")

    job = await arq_pool.enqueue_job(
        "process_document",
        user_id=user_id,
        filename=file.filename,
        text=text,
    )

    logger.info("file_uploaded", user_id=user_id, filename=file.filename, job_id=job.job_id)
    return {"status": "queued", "job_id": job.job_id, "filename": file.filename}


@app.post("/query")
async def query_documents(
    request: QueryRequest,
    user_id: str = Depends(get_current_user),
):
    results = await asyncio.to_thread(
        retrieve_documents,
        user_id=user_id,
        query=request.query,
        top_k=request.top_k,
    )
    logger.info("query_executed", user_id=user_id, results_count=len(results))
    return {"results": results}


@app.post("/rag")
async def rag_query(
    request: QueryRequest,
    user_id: str = Depends(get_current_user),
):
    allowed = check_rate_limit(user_id=user_id, action="rag", limit=20)
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    result = await asyncio.to_thread(
        generate_rag_answer,
        user_id=user_id,
        query=request.query,
        top_k=request.top_k,
    )
    logger.info("rag_completed", user_id=user_id)
    return result


# --- Public endpoints ---

@app.get("/documents")
def get_documents(user_id: str = Depends(get_current_user)):
    db = SessionLocal()
    try:
        docs = db.query(models.Document).filter(
            models.Document.user_id == user_id
        ).order_by(models.Document.created_at.desc()).all()
        return [
            {
                "id": str(d.id),
                "filename": d.filename,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
    finally:
        db.close()


@app.get("/me")
def get_me(user_id: str = Depends(get_current_user)):
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "id": str(user.id),
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }
    finally:
        db.close()


@app.get("/health")
async def health():
    return {"status": "running"}


@app.on_event("startup")
async def startup_event():
    global arq_pool
    Base.metadata.create_all(bind=engine)
    create_collection()
    arq_pool = await create_pool(get_redis_settings())
    logger.info("server_started")


@app.get("/check-connections")
def check_connections():
    results = {}

    try:
        qdrant = QdrantClient(url=os.getenv("QDRANT_URL"))
        qdrant.get_collections()
        results["qdrant"] = "connected"
    except Exception as e:
        results["qdrant"] = str(e)

    try:
        conn = psycopg2.connect(os.getenv("POSTGRES_URL"))
        conn.close()
        results["postgres"] = "connected"
    except Exception as e:
        results["postgres"] = str(e)

    try:
        r = redis.from_url(os.getenv("REDIS_URL"))
        r.ping()
        results["redis"] = "connected"
    except Exception as e:
        results["redis"] = str(e)

    return results
