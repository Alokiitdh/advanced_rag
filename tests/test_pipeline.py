import pytest
import httpx
import time
import uuid
from src.db.session import SessionLocal
from src.db.models import User, Document, Chunk

# Configuration
BASE_URL = "http://localhost:8000"
USER_ID = str(uuid.uuid4())
TEST_EMAIL = f"test-{USER_ID[:8]}@test.com"
FILENAME = "test_doc.txt"
CONTENT = "This is a test document about the RAG pipeline. It mentions that the pipeline is working correctly."


@pytest.fixture(autouse=True)
def create_test_user():
    """Create a test user in the DB before each test that needs one."""
    db = SessionLocal()
    user = User(id=USER_ID, email=TEST_EMAIL)
    db.add(user)
    db.commit()
    db.close()
    yield
    # Cleanup (order matters: chunks → documents → user)
    db = SessionLocal()
    db.query(Chunk).filter(Chunk.user_id == USER_ID).delete()
    db.query(Document).filter(Document.user_id == USER_ID).delete()
    db.query(User).filter(User.id == USER_ID).delete()
    db.commit()
    db.close()


def test_health_check():
    """Verify that the service is up and healthy."""
    with httpx.Client(base_url=BASE_URL) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "running"}

def test_pipeline_flow():
    """
    Test the entire pipeline:
    1. Upload a document
    2. Wait for it to be indexed (poll /query)
    3. Verify retrieval results
    4. Verify RAG generation
    """
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        # 1. Upload
        print(f"\n[INFO] Uploading document for user: {USER_ID}")
        upload_payload = {
            "user_id": USER_ID,
            "filename": FILENAME,
            "text": CONTENT
        }
        response = client.post("/upload", json=upload_payload)
        assert response.status_code == 200
        assert response.json()["status"] == "queued"

        # 2. Poll for indexing completion
        # We query for a known keyword from the content until we get a result
        print("[INFO] Polling for document availability...")
        max_retries = 10
        found = False
        
        for i in range(max_retries):
            time.sleep(2) # Wait a bit between tries
            
            query_payload = {
                "user_id": USER_ID,
                "query": "pipeline working correctly",
                "top_k": 1
            }
            res = client.post("/query", json=query_payload)
            
            if res.status_code == 200:
                results = res.json().get("results", [])
                if results and len(results) > 0:
                    print(f"[INFO] Document found on attempt {i+1}")
                    found = True
                    # Verify content match
                    assert CONTENT in results[0]["text"]
                    break
            
            print(f"[INFO] Attempt {i+1}: Document not yet indexed...")

        assert found, "Document was not indexed within the timeout period."

        # 3. Test RAG Generation
        print("[INFO] Testing RAG generation...")
        rag_payload = {
            "user_id": USER_ID,
            "query": "What does the document say about the pipeline?",
            "top_k": 3
        }
        rag_res = client.post("/rag", json=rag_payload)
        assert rag_res.status_code == 200
        answer = rag_res.json().get("answer", "")
        print(f"[INFO] RAG Answer: {answer}")
        
        # Verify the answer is relevant (basic check)
        assert answer is not None
        assert len(answer) > 0
