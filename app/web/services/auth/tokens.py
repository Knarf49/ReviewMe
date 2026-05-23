import hashlib
import secrets
import time

import jwt

from app.web.services.auth.config import get_auth_config


class InvalidTokenError(Exception):
    """Raised when an access token fails verification."""


def encode_access(uid: int, sid: str, role: str) -> str:
    cfg = get_auth_config()
    now = int(time.time())
    payload = {
        "uid": uid,
        "sid": sid,
        "role": role,
        "iat": now,
        "exp": now + cfg.access_ttl_seconds,
        "iss": cfg.jwt_issuer,
        "typ": "access",
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_alg)


def decode_access(token: str) -> dict:
    cfg = get_auth_config()
    try:
        claims = jwt.decode(
            token,
            cfg.jwt_secret,
            algorithms=[cfg.jwt_alg],
            issuer=cfg.jwt_issuer,
            leeway=10,
            options={"require": ["exp", "iat", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise InvalidTokenError(str(e)) from e
    if claims.get("typ") != "access":
        raise InvalidTokenError("wrong token type")
    return claims


def gen_refresh() -> tuple[str, str]:
    plain = secrets.token_urlsafe(32)
    return plain, hash_refresh(plain)


def hash_refresh(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
