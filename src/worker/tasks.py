from src.worker.celery_app import celery
from src.services.ingestion import ingest_document


@celery.task(name="process_document")
def process_document(user_id: str, filename: str, text: str):
    return ingest_document(user_id, filename, text)
