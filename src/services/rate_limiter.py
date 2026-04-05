import os
import redis
from dotenv import load_dotenv

load_dotenv()

redis_client = redis.from_url(os.getenv("REDIS_URL"))


def check_rate_limit(user_id: str, action: str, limit: int, window: int = 60):
    """
    Atomic rate limiter — incr first, then check.
    No race condition between concurrent requests.
    """
    key = f"rate:{action}:{user_id}"

    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, window)
    results = pipe.execute()

    current = results[0]
    return current <= limit
