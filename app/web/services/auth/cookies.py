from fastapi import Response

from app.web.services.auth.config import get_auth_config

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"


def set_auth_cookies(response: Response, *, access: str, refresh: str, csrf: str) -> None:
    cfg = get_auth_config()
    common: dict = {
        "secure": cfg.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    if cfg.cookie_domain:
        common["domain"] = cfg.cookie_domain

    response.set_cookie(
        ACCESS_COOKIE, access,
        max_age=cfg.access_ttl_seconds, httponly=True, **common,
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh,
        max_age=cfg.refresh_ttl_seconds, httponly=True, **common,
    )
    response.set_cookie(
        CSRF_COOKIE, csrf,
        max_age=cfg.access_ttl_seconds, httponly=False, **common,
    )


def clear_auth_cookies(response: Response) -> None:
    cfg = get_auth_config()
    common: dict = {"path": "/"}
    if cfg.cookie_domain:
        common["domain"] = cfg.cookie_domain
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, **common)
