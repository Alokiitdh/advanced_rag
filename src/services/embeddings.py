import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = "text-embedding-3-large"


def generate_embedding(text: str):
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY")
    )
    response = client.embeddings.create(
        model=MODEL_NAME,
        input=text
    )
    return response.data[0].embedding
