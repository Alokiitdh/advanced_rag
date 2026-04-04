import pytest
import httpx
import time
import uuid

# Configuration
BASE_URL = "http://localhost:8000"
TEST_EMAIL = f"test-{uuid.uuid4().hex[:8]}@test.com"
TEST_PASSWORD = "testpassword123"
FILENAME = "test_doc.txt"
CONTENT = "This is a test document about the RAG pipeline. It mentions that the pipeline is working correctly."


@pytest.fixture(scope="module")
def auth_headers():
    """Register a test user and return auth headers."""
    with httpx.Client(base_url=BASE_URL) as client:
        response = client.post("/register", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        assert response.status_code == 200
        token = response.json()["token"]
        return {"Authorization": f"Bearer {token}"}


def test_health_check():
    """Verify that the service is up and healthy."""
    with httpx.Client(base_url=BASE_URL) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "running"}


def test_register_and_login():
    """Test user registration and login flow."""
    email = f"test-{uuid.uuid4().hex[:8]}@test.com"
    with httpx.Client(base_url=BASE_URL) as client:
        # Register
        res = client.post("/register", json={"email": email, "password": "pass123"})
        assert res.status_code == 200
        assert "token" in res.json()
        assert "user_id" in res.json()

        # Login
        res = client.post("/login", json={"email": email, "password": "pass123"})
        assert res.status_code == 200
        assert "token" in res.json()

        # Wrong password
        res = client.post("/login", json={"email": email, "password": "wrong"})
        assert res.status_code == 401


def test_protected_endpoint_without_token():
    """Ensure protected endpoints reject unauthenticated requests."""
    with httpx.Client(base_url=BASE_URL) as client:
        res = client.post("/query", json={"query": "test", "top_k": 1})
        assert res.status_code in (401, 403)  # No credentials


def test_pipeline_flow(auth_headers):
    """
    Test the entire pipeline:
    1. Upload a document
    2. Wait for it to be indexed (poll /query)
    3. Verify retrieval results
    4. Verify RAG generation
    """
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        # 1. Upload
        print(f"\n[INFO] Uploading document...")
        upload_payload = {
            "filename": FILENAME,
            "text": CONTENT,
        }
        response = client.post("/upload", json=upload_payload, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "queued"

        # 2. Poll for indexing completion
        print("[INFO] Polling for document availability...")
        max_retries = 10
        found = False

        for i in range(max_retries):
            time.sleep(2)

            query_payload = {
                "query": "pipeline working correctly",
                "top_k": 1,
            }
            res = client.post("/query", json=query_payload, headers=auth_headers)

            if res.status_code == 200:
                results = res.json().get("results", [])
                if results and len(results) > 0:
                    print(f"[INFO] Document found on attempt {i+1}")
                    found = True
                    assert CONTENT in results[0]["text"]
                    break

            print(f"[INFO] Attempt {i+1}: Document not yet indexed...")

        assert found, "Document was not indexed within the timeout period."

        # 3. Test RAG Generation
        print("[INFO] Testing RAG generation...")
        rag_payload = {
            "query": "What does the document say about the pipeline?",
            "top_k": 3,
        }
        rag_res = client.post("/rag", json=rag_payload, headers=auth_headers)
        assert rag_res.status_code == 200
        answer = rag_res.json().get("answer", "")
        print(f"[INFO] RAG Answer: {answer}")

        assert answer is not None
        assert len(answer) > 0
