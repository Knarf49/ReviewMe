import redis as redis_lib
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.db import db_dep
from app.core.models import User
from app.core.redis_client import get_redis
from app.web.services.auth import denylist, tokens
from app.web.services.auth.exceptions import (
    AuthServiceUnavailable,
    InvalidToken,
)


def get_current_user(
    request: Request,
    db: Session = Depends(db_dep),
    rc: redis_lib.Redis = Depends(get_redis),
) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise InvalidToken()
    try:
        claims = tokens.decode_access(token)
    except tokens.InvalidTokenError:
        raise InvalidToken()

    sid = claims["sid"]
    uid = claims["uid"]
    iat = claims["iat"]

    try:
        if denylist.is_sid_revoked(rc, sid):
            raise InvalidToken()
        epoch = denylist.get_user_epoch(rc, uid)
    except redis_lib.RedisError:
        raise AuthServiceUnavailable()
    if epoch is not None and iat < epoch:
        raise InvalidToken()

    user = db.get(User, uid)
    if user is None:
        raise InvalidToken()
    return user
