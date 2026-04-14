import os
import secrets

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import SECRET_KEY
from app.database import get_db

_serializer = URLSafeTimedSerializer(SECRET_KEY)
_takeover_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="takeover-action")
_transfer_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="transfer-action")

COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "session")
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 dagar
TAKEOVER_ACTION_MAX_AGE = 60 * 60 * 24 * 7  # 7 dagar
TRANSFER_ACTION_MAX_AGE = 60 * 60 * 24 * 7  # 7 dagar


def create_takeover_action_token(req_id: int, action: str, kind: str = "link") -> str:
    """action är 'approve' eller 'reject'. kind är 'link' eller 'bundle'."""
    return _takeover_serializer.dumps({"req_id": req_id, "action": action, "kind": kind})


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


def create_session_cookie(user_id: int, csrf_secret: str | None = None) -> str:
    """Skapa en signerad sessionscookie med inbyggd CSRF-hemlighet."""
    return _serializer.dumps({
        "user_id": user_id,
        "csrf_secret": csrf_secret or secrets.token_urlsafe(16),
    })


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
            "SELECT id, email, is_admin, allow_external_urls FROM users WHERE id = ?",
            (data["user_id"],),
        ).fetchone()
    if not row:
        return None
    return dict(row)
