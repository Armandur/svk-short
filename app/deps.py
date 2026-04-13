"""Gemensamma FastAPI-beroenden som används i flera route-moduler.

Importera härifrån i stället för att definiera lokala kopior i varje fil:

    from app.deps import get_user_or_redirect, get_admin_or_redirect, check_rate_limit
"""

import logging
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from app.auth import get_current_user
from app.config import RATE_LIMIT_PER_HOUR
from app.database import get_db

logger = logging.getLogger(__name__)


def get_user_or_redirect(request: Request) -> dict:
    """Returnerar inloggad användare eller kastar 302-redirect till /login."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def get_admin_or_redirect(request: Request) -> dict:
    """Returnerar inloggad admin-användare eller kastar 302-redirect till /login."""
    user = get_current_user(request)
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def check_rate_limit(db, ip: str, action: str) -> bool:
    """Returnerar True om begäran är tillåten, False om rate limit nåtts.

    Registrerar automatiskt begäran i rate_limits-tabellen vid framgång.
    """
    cutoff = datetime.utcnow() - timedelta(hours=1)
    count = db.execute(
        "SELECT COUNT(*) FROM rate_limits WHERE ip=? AND action=? AND created_at > ?",
        (ip, action, cutoff.isoformat()),
    ).fetchone()[0]
    if count >= RATE_LIMIT_PER_HOUR:
        return False
    db.execute("INSERT INTO rate_limits (ip, action) VALUES (?, ?)", (ip, action))
    return True


def user_allows_any_domain(email: str) -> bool:
    """Returnerar True om användaren har allow_any_domain=1 i databasen."""
    with get_db() as db:
        row = db.execute(
            "SELECT allow_any_domain FROM users WHERE email=?", (email,)
        ).fetchone()
    return bool(row["allow_any_domain"]) if row else False


def user_allows_external_urls(email: str) -> bool:
    """Returnerar True om användaren har allow_external_urls=1 i databasen."""
    with get_db() as db:
        row = db.execute(
            "SELECT allow_external_urls FROM users WHERE email=?", (email,)
        ).fetchone()
    return bool(row["allow_external_urls"]) if row else False
