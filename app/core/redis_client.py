from functools import lru_cache

import redis

from app.web.services.auth.config import get_auth_config


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    cfg = get_auth_config()
    return redis.Redis.from_url(cfg.redis_url, decode_responses=True)
