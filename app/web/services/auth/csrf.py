import hmac
import secrets

from fastapi import HTTPException, Request, status


def gen_csrf() -> str:
    return secrets.token_urlsafe(32)


def require_csrf(request: Request) -> None:
    cookie_val = request.cookies.get("csrf_token") or ""
    header_val = request.headers.get("x-csrf-token") or ""
    if not cookie_val or not header_val or not hmac.compare_digest(cookie_val, header_val):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed",
        )
