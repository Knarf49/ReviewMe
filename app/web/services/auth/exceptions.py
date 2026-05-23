from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AuthError(Exception):
    code: str = "auth_error"
    status_code: int = status.HTTP_401_UNAUTHORIZED
    detail: str = "Authentication failed"


class InvalidCredentials(AuthError):
    code = "invalid_credentials"
    detail = "Invalid username or password"


class InvalidToken(AuthError):
    code = "invalid_token"
    detail = "Invalid or expired session"


class ReuseDetected(AuthError):
    code = "reuse_detected"
    detail = "Session terminated"


class CsrfMismatch(AuthError):
    code = "csrf_mismatch"
    status_code = status.HTTP_403_FORBIDDEN
    detail = "CSRF verification failed"


class AuthServiceUnavailable(AuthError):
    code = "auth_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    detail = "Auth service unavailable"


def register_auth_error_handler(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def _handler(_request: Request, exc: AuthError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )
