from src.services.ingestion import ingest_document
from src.worker.celery_app import get_redis_settings


async def process_document(ctx, user_id: str, filename: str, text: str):
    """ARQ task: ingest a document asynchronously."""
    return ingest_document(user_id, filename, text)


class WorkerSettings:
    """ARQ worker configuration."""
    functions = [process_document]
    redis_settings = get_redis_settings()
    max_jobs = 10
    job_timeout = 300  # 5 minutes per ingestion job
