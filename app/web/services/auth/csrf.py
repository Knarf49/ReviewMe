import hmac
import secrets

from fastapi import Header, HTTPException, Request, status


def gen_csrf() -> str:
    return secrets.token_urlsafe(32)


def require_csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    cookie_val = request.cookies.get("csrf_token") or ""
    header_val = x_csrf_token or ""
    if not cookie_val or not header_val or not hmac.compare_digest(cookie_val, header_val):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed",
        )
