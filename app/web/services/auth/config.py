import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class AuthConfig:
    jwt_secret: str
    jwt_alg: str
    jwt_issuer: str
    access_ttl_seconds: int
    refresh_ttl_seconds: int
    cookie_domain: str | None
    cookie_secure: bool
    session_limit_per_user: int
    redis_url: str


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def get_auth_config() -> AuthConfig:
    secret = os.environ.get("JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET missing or too short (need >=32 chars). "
            "Generate: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    domain = os.environ.get("COOKIE_DOMAIN", "") or None
    return AuthConfig(
        jwt_secret=secret,
        jwt_alg=os.environ.get("JWT_ALG", "HS256"),
        jwt_issuer=os.environ.get("JWT_ISSUER", "reviewme-auth"),
        access_ttl_seconds=int(os.environ.get("ACCESS_TTL_SECONDS", "7200")),
        refresh_ttl_seconds=int(os.environ.get("REFRESH_TTL_SECONDS", "1209600")),
        cookie_domain=domain,
        cookie_secure=_bool_env("COOKIE_SECURE", True),
        session_limit_per_user=int(os.environ.get("SESSION_LIMIT_PER_USER", "5")),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    )
