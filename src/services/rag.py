import os
import time
from dotenv import load_dotenv
from openai import OpenAI
from src.services.retrieval import retrieve_documents
from src.services.cache import cache_get, cache_set
from src.db.session import SessionLocal
from src.db.models import QueryLog

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = "gpt-4o-mini"


def generate_rag_answer(user_id: str, query: str, top_k: int = 5):

    # 🔹 1. Check cache FIRST
    cache_key = f"rag:{user_id}:{query}:{top_k}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    start_time = time.time()

    # 🔹 2. Retrieve chunks
    retrieved_chunks = retrieve_documents(user_id, query, top_k)

    if not retrieved_chunks:
        return {"answer": "No relevant information found.", "sources": []}

    # 🔹 3. Build context
    context_text = "\n\n".join(
        [chunk["text"] for chunk in retrieved_chunks]
    )

    prompt = f"""
You are an enterprise AI assistant.

Answer using ONLY the provided context.
If the answer is not in the context, say:
"I don't have enough information in the provided documents."

Context:
{context_text}

Question:
{query}

Answer:
"""

    # 🔹 4. Call LLM
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": "You are a helpful AI assistant."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2
    )

    answer = response.choices[0].message.content

    result = {
        "answer": answer,
        "sources": [
            {
                "document_id": chunk["document_id"],
                "chunk_index": chunk["chunk_index"]
            }
            for chunk in retrieved_chunks
        ]
    }

    # 🔹 5. Cache final result
    cache_set(cache_key, result)

    # 🔹 6. Log query
    latency = int((time.time() - start_time) * 1000)

    db = SessionLocal()
    db.add(QueryLog(
        user_id=user_id,
        query_text=query,
        response_time_ms=str(latency)
    ))
    db.commit()
    db.close()

    return result
