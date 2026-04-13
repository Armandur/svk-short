import os
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
from app.database import get_db

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
_serializer = URLSafeTimedSerializer(SECRET_KEY)
_takeover_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="takeover-action")
_transfer_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="transfer-action")

COOKIE_NAME = "session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 dagar
TAKEOVER_ACTION_MAX_AGE = 60 * 60 * 24 * 7  # 7 dagar
TRANSFER_ACTION_MAX_AGE = 60 * 60 * 24 * 7  # 7 dagar


def create_takeover_action_token(req_id: int, action: str) -> str:
    """action är 'approve' eller 'reject'."""
    return _takeover_serializer.dumps({"req_id": req_id, "action": action})


def decode_takeover_action_token(token: str) -> dict | None:
    try:
        return _takeover_serializer.loads(token, max_age=TAKEOVER_ACTION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def create_transfer_action_token(req_id: int, action: str) -> str:
    """action är 'accept' eller 'decline'."""
    return _transfer_serializer.dumps({"req_id": req_id, "action": action})


def create_bulk_transfer_token(req_ids: list[int], action: str, bundle_ids: list[int] | None = None) -> str:
    """action är 'accept' eller 'decline'. Kodar flera transfer_requests + valfria bundles på en gång."""
    payload: dict = {"req_ids": req_ids, "action": action}
    if bundle_ids:
        payload["bundle_ids"] = bundle_ids
    return _transfer_serializer.dumps(payload)


def decode_transfer_action_token(token: str) -> dict | None:
    try:
        return _transfer_serializer.loads(token, max_age=TRANSFER_ACTION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


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
