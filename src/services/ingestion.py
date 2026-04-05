import uuid
from src.db.session import SessionLocal
from src.db.models import Document, Chunk
from src.services.chunking import chunk_text
from src.services.embeddings import generate_embeddings_batch
from src.services.vector_store import get_qdrant_client, COLLECTION_NAME
from src.services.logging import get_logger

logger = get_logger("ingestion")


def ingest_document(user_id: str, filename: str, text: str):
    db = SessionLocal()

    try:
        # 1. Create document record
        document = Document(
            user_id=user_id,
            filename=filename,
            status="processing"
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        # 2. Chunk text
        chunks = chunk_text(text)
        logger.info("chunks_created", filename=filename, count=len(chunks))

        # 3. Batch embed all chunks in one API call
        embeddings = generate_embeddings_batch(chunks)
        logger.info("embeddings_generated", filename=filename, count=len(embeddings))

        points = []
        db_chunks = []

        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = str(uuid.uuid4())

            points.append({
                "id": chunk_id,
                "vector": embedding,
                "payload": {
                    "user_id": str(user_id),
                    "document_id": str(document.id),
                    "chunk_index": index,
                }
            })

            db_chunks.append(
                Chunk(
                    id=chunk_id,
                    document_id=document.id,
                    user_id=user_id,
                    text=chunk,
                    embedding_id=chunk_id
                )
            )

        # 4. Bulk upsert to Qdrant
        client = get_qdrant_client()
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )

        # 5. Bulk insert chunks in Postgres
        db.add_all(db_chunks)

        # 6. Mark document ready
        document.status = "ready"
        db.commit()
        document_id = str(document.id)

        logger.info("document_ingested", document_id=document_id, filename=filename)
        return {"status": "ingested", "document_id": document_id}

    except Exception as e:
        logger.exception("ingestion_failed", filename=filename, error=str(e))
        db.rollback()

        # Mark as failed in a fresh transaction
        try:
            if "document" in locals():
                document.status = "failed"
                db.commit()
        except Exception:
            db.rollback()

        return {"error": str(e)}

    finally:
        db.close()
