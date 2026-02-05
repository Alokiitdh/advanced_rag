import os
import redis
import json
from dotenv import load_dotenv

load_dotenv()

redis_client = redis.from_url(os.getenv("REDIS_URL"))


def cache_get(key: str):
    value = redis_client.get(key)
    if value:
        return json.loads(value)
    return None


def cache_set(key: str, value, ttl: int = 300):
    redis_client.setex(key, ttl, json.dumps(value))
