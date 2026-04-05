from qdrant_client.models import Filter, FieldCondition, MatchValue
from src.services.embeddings import generate_embedding
from src.services.cache import cache_get, cache_set
from src.services.vector_store import get_qdrant_client, COLLECTION_NAME
from src.db.session import SessionLocal
from src.db.models import Chunk


def retrieve_documents(user_id: str, query: str, top_k: int = 5):

    # 1. Check cache
    cache_key = f"retrieval:{user_id}:{query}:{top_k}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    # 2. Generate query embedding
    query_vector = generate_embedding(query)

    # 3. Query Qdrant (reuse singleton client)
    client = get_qdrant_client()
    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=top_k,
        query_filter=Filter(
            must=[
                FieldCondition(
                    key="user_id",
                    match=MatchValue(value=user_id)
                )
            ]
        )
    )

    # 4. Fetch chunk text from Postgres
    db = SessionLocal()
    try:
        retrieved_chunks = []

        for point in results.points:
            chunk_id = point.id
            db_chunk = db.query(Chunk).filter(Chunk.id == chunk_id).first()

            if db_chunk:
                retrieved_chunks.append({
                    "score": point.score,
                    "text": db_chunk.text,
                    "document_id": str(db_chunk.document_id),
                    "chunk_index": point.payload.get("chunk_index"),
                })
    finally:
        db.close()

    # 5. Save to cache
    cache_set(cache_key, retrieved_chunks)

    return retrieved_chunks
