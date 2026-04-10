import os
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
from app.database import get_db

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
_serializer = URLSafeTimedSerializer(SECRET_KEY)

COOKIE_NAME = "session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 dagar


def create_session_cookie(user_id: int) -> str:
    return _serializer.dumps({"user_id": user_id})


def decode_session_cookie(cookie: str) -> dict | None:
    try:
        return _serializer.loads(cookie, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request) -> dict | None:
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    data = decode_session_cookie(cookie)
    if not data:
        return None
    with get_db() as db:
        row = db.execute(
            "SELECT id, email, is_admin FROM users WHERE id = ?", (data["user_id"],)
        ).fetchone()
    if not row:
        return None
    return dict(row)


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def require_admin(request: Request) -> dict:
    user = get_current_user(request)
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403)
    return user
