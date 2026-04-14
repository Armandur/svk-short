"""CSRF-skydd med per-sessions-hemlighet.

Skyddsprincipen:
- Inloggade användare: CSRF-hemligheten är lagrad i sessionscookien (csrf_secret).
- Ej inloggade användare: en kortlivad `csrf_anon`-cookie bär hemligheten.
- Token = itsdangerous-signatur över hemligheten (salt "csrf").

Typiskt mönster för GET-handler som servar ett unauthenticerat formulär:

    anon_secret, is_new = get_anon_csrf_secret(request)
    response = templates.TemplateResponse(template, {
        "request": request,
        "csrf_secret": anon_secret,
        ...
    })
    if is_new:
        set_anon_csrf_cookie(response, anon_secret)
    return response

Jinja-globalen `{{ csrf_token() }}` prioriterar ctx["csrf_secret"] > sessionscookie
> anon-cookie, så explicit context-passning behövs bara när cookien inte finns ännu.
"""

import hashlib
import hmac
import secrets

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import SECRET_KEY

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="csrf")
_MAX_AGE = 60 * 60 * 24  # 24 timmar

ANON_CSRF_COOKIE_NAME = "csrf_anon"
ANON_CSRF_MAX_AGE = 60 * 60  # 1 timme


def generate_csrf_token(secret: str) -> str:
    """Generera ett CSRF-token signerat med den angivna per-sessions-hemligheten."""
    return _serializer.dumps(secret)


def validate_csrf_token(token: str, secret: str) -> bool:
    """Validera ett CSRF-token mot den angivna hemligheten.

    Returnerar False om token är ogiltig, utgången, eller om hemligheten
    inte matchar den som signerades in i tokenet.
    """
    if not secret:
        return False
    try:
        value = _serializer.loads(token, max_age=_MAX_AGE)
        return value == secret
    except (BadSignature, SignatureExpired):
        return False


def get_csrf_secret(request: Request) -> str:
    """Hämta CSRF-hemligheten för aktuell request.

    Prioritetsordning:
    1. csrf_secret i sessionscookien (inloggad användare, ny session).
    2. Per-user HMAC-fallback för gamla sessions utan csrf_secret (skapade
       före den deploy som introducerade csrf_secret i sessionscookien).
    3. csrf_anon-cookien för ej inloggade formulär.

    Returnerar alltid en sträng — tom sträng om ingen hemlighet alls finns.
    """
    from app.auth import COOKIE_NAME, decode_session_cookie

    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        data = decode_session_cookie(cookie)
        if data:
            if data.get("csrf_secret"):
                return data["csrf_secret"]
            # Övergångslösning: sessions utan csrf_secret (skapade före deploy)
            # får ett per-user HMAC-deriverat secret. Bättre än universellt token.
            user_id = str(data.get("user_id", ""))
            return hmac.new(
                SECRET_KEY.encode(),
                f"csrf-fallback:{user_id}".encode(),
                hashlib.sha256,
            ).hexdigest()[:32]

    return request.cookies.get(ANON_CSRF_COOKIE_NAME) or ""


def get_anon_csrf_secret(request: Request) -> tuple[str, bool]:
    """Hämta eller skapa en anonym CSRF-hemlighet.

    Returnerar (secret, is_new). Om is_new är True ska anroparen kalla
    set_anon_csrf_cookie(response, secret) efter att TemplateResponse skapats.
    """
    existing = request.cookies.get(ANON_CSRF_COOKIE_NAME)
    if existing:
        return existing, False
    return secrets.token_urlsafe(16), True


def set_anon_csrf_cookie(response, secret: str) -> None:
    """Lägg till csrf_anon-cookie på response (anropas när get_anon_csrf_secret returnerat is_new=True)."""
    response.set_cookie(
        ANON_CSRF_COOKIE_NAME,
        secret,
        httponly=True,
        samesite="lax",
        max_age=ANON_CSRF_MAX_AGE,
    )
