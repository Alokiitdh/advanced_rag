import os
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance
from dotenv import load_dotenv

load_dotenv()

COLLECTION_NAME = "documents"
VECTOR_SIZE = 1536  # text-embedding-3-small

# Singleton client — reuse across the app
qdrant_client = QdrantClient(url=os.getenv("QDRANT_URL"))


def get_qdrant_client() -> QdrantClient:
    return qdrant_client


def create_collection():
    try:
        collection_info = qdrant_client.get_collection(COLLECTION_NAME)

        if hasattr(collection_info.config.params.vectors, 'size'):
            current_size = collection_info.config.params.vectors.size
            if current_size != VECTOR_SIZE:
                print(f"Dimension mismatch: Collection has {current_size}, expected {VECTOR_SIZE}. Recreating...")
                qdrant_client.delete_collection(COLLECTION_NAME)
                raise ValueError("Collection deleted for recreation")
        return

    except (Exception, ValueError):
        pass

    qdrant_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE
        ),
    )
