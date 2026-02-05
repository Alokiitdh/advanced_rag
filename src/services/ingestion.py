import uuid
from src.db.session import SessionLocal
from src.db.models import Document, Chunk
from src.services.chunking import chunk_text
from src.services.embeddings import generate_embedding
from src.services.vector_store import get_qdrant_client, COLLECTION_NAME


def ingest_document(user_id: str, filename: str, text: str):
    db = SessionLocal()

    try:
        # 1️⃣ Create document record
        document = Document(
            user_id=user_id,
            filename=filename,
            status="processing"
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        # 2️⃣ Chunk text
        chunks = chunk_text(text)

        points = []
        db_chunks = []

        for index, chunk in enumerate(chunks):
            embedding = generate_embedding(chunk)

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

        # 3️⃣ Bulk upsert to Qdrant
        client = get_qdrant_client()
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )

        # 4️⃣ Bulk insert chunks in Postgres
        db.add_all(db_chunks)

        # 5️⃣ Mark document ready
        document.status = "ready"

        db.commit()
        document_id = str(document.id) 

        db.close()

        return {"status": "ingested", "document_id": document_id}

    except Exception as e:
        db.rollback()

        # Write error to file for debugging
        with open("ingestion_error.log", "w") as f:
            import traceback
            f.write(str(e) + "\n")
            f.write(traceback.format_exc())

        if "document" in locals():
            document.status = "failed"
            db.commit()

        db.close()
        return {"error": str(e)}
