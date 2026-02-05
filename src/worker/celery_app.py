import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

celery = Celery(
    "rag_worker",
    broker=os.getenv("REDIS_URL"),
    backend=os.getenv("REDIS_URL"),
    include=["src.worker.tasks"]
)


