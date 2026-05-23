import ipaddress
from datetime import datetime

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import db_dep as get_db
from app.core.models import User
from app.core.redis_client import get_redis
from app.web.services.auth import cookies, csrf, sessions, tokens
from app.web.services.auth.exceptions import InvalidCredentials

router = APIRouter(tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class SignupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)


class UserResponse(BaseModel):
    id: int
    username: str
    email: EmailStr
    created_at: datetime

    class Config:
        from_attributes = True


SignupResponse = UserResponse


def _client_ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    if host is None:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=72)


class LoginResponse(BaseModel):
    user: UserResponse
    csrf_token: str


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    new_user = User(
        username=payload.username,
        email=payload.email,
        password_hash=pwd_context.hash(payload.password),
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already taken",
        )
    return new_user


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    rc: redis_lib.Redis = Depends(get_redis),
):
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not pwd_context.verify(payload.password, user.password_hash):
        raise InvalidCredentials()

    sessions.enforce_limit(db, rc, user_id=user.id)
    plain_refresh, family_id = sessions.create_session(
        db,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip=_client_ip(request),
    )
    db.commit()

    access = tokens.encode_access(uid=user.id, sid=str(family_id), role=user.role)
    csrf_token = csrf.gen_csrf()
    cookies.set_auth_cookies(
        response, access=access, refresh=plain_refresh, csrf=csrf_token,
    )
    return {"user": user, "csrf_token": csrf_token}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    rc: redis_lib.Redis = Depends(get_redis),
    _csrf: None = Depends(csrf.require_csrf),
):
    from sqlalchemy import select
    from app.core.models import RefreshSession

    plain = request.cookies.get(cookies.REFRESH_COOKIE)
    if plain:
        hashed = tokens.hash_refresh(plain)
        row = db.execute(
            select(RefreshSession).where(RefreshSession.token_hash == hashed)
        ).scalar_one_or_none()
        if row is not None:
            sessions.revoke_family(db, rc, family_id=row.family_id)
            db.commit()
    cookies.clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return None
