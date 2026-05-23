import time

import redis


def _sid_key(sid: str) -> str:
    return f"revoked_sid:{sid}"


def _epoch_key(uid: int) -> str:
    return f"user_epoch:{uid}"


def revoke_sid(client: redis.Redis, sid: str, *, ttl_seconds: int) -> None:
    client.set(_sid_key(sid), "1", ex=ttl_seconds)


def is_sid_revoked(client: redis.Redis, sid: str) -> bool:
    return client.exists(_sid_key(sid)) == 1


def bump_user_epoch(client: redis.Redis, uid: int, *, ttl_seconds: int) -> None:
    client.set(_epoch_key(uid), str(int(time.time())), ex=ttl_seconds)


def get_user_epoch(client: redis.Redis, uid: int) -> int | None:
    val = client.get(_epoch_key(uid))
    return int(val) if val is not None else None
