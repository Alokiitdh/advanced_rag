#!/bin/bash
# Production startup script
# Uses Gunicorn with Uvicorn workers for multi-process serving

WORKERS=${WORKERS:-4}
PORT=${PORT:-8000}

echo "Starting RAG API with $WORKERS workers on port $PORT"

gunicorn src.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers $WORKERS \
    --bind 0.0.0.0:$PORT \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
