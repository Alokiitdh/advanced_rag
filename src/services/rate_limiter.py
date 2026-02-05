import os
import redis
from dotenv import load_dotenv

load_dotenv()

redis_client = redis.from_url(os.getenv("REDIS_URL"))


def check_rate_limit(user_id: str, action: str, limit: int, window: int = 60):
    """
    user_id: unique user
    action: "rag" or "upload"
    limit: max requests allowed
    window: time window in seconds
    """

    key = f"rate:{action}:{user_id}"

    current = redis_client.get(key)

    if current and int(current) >= limit:
        return False

    pipe = redis_client.pipeline()
    pipe.incr(key, 1)
    pipe.expire(key, window)
    pipe.execute()

    return True
