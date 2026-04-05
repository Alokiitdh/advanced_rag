import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = "text-embedding-3-small"

_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    timeout=30.0,
)


def generate_embedding(text: str) -> list[float]:
    response = _client.embeddings.create(
        model=MODEL_NAME,
        input=text
    )
    return response.data[0].embedding


def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    response = _client.embeddings.create(
        model=MODEL_NAME,
        input=texts
    )
    return [item.embedding for item in response.data]
