import os
from arq.connections import RedisSettings
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


def get_redis_settings() -> RedisSettings:
    """Parse REDIS_URL into ARQ RedisSettings."""
    # redis://host:port/db
    url = REDIS_URL.replace("redis://", "")
    host = "localhost"
    port = 6379
    database = 0

    if "/" in url:
        url, db_str = url.rsplit("/", 1)
        database = int(db_str) if db_str else 0

    if ":" in url:
        host, port_str = url.split(":", 1)
        port = int(port_str)
    elif url:
        host = url

    return RedisSettings(host=host, port=port, database=database)
