import os
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance
from dotenv import load_dotenv

load_dotenv()

def get_qdrant_client():
    return QdrantClient(url=os.getenv("QDRANT_URL"))


COLLECTION_NAME = "documents"


def create_collection():
    client = get_qdrant_client()
    try:
        collection_info = client.get_collection(COLLECTION_NAME)
        
        # Check if vector configuration matches requirements
        # Note: This handles the simple case. Complex configs might need more checks.
        if hasattr(collection_info.config.params.vectors, 'size'):
            current_size = collection_info.config.params.vectors.size
            if current_size != 3072:
                print(f"Dimension mismatch: Collection has {current_size}, expected 3072. Recreating...")
                client.delete_collection(COLLECTION_NAME)
                # Fall through to creation
                raise ValueError("Collection deleted for recreation")
        return
                
    except (Exception, ValueError):
        # Collection doesn't exist or was deleted
        pass

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=3072,
            distance=Distance.COSINE
        ),
    )
