"""Gemensamma FastAPI-beroenden som används i flera route-moduler.

Importera härifrån i stället för att definiera lokala kopior i varje fil:

    from app.deps import get_user_or_redirect, get_admin_or_redirect, check_rate_limit
"""

import logging

from fastapi import Request

from app.auth import get_current_user
from app.config import RATE_LIMIT_PER_HOUR
from app.database import get_db

logger = logging.getLogger(__name__)


class RedirectRequired(Exception):
    """Kastas när en route kräver inloggning men användaren inte är inloggad."""

    def __init__(self, location: str = "/login"):
        self.location = location


def get_user_or_redirect(request: Request) -> dict:
    """Returnerar inloggad användare eller kastar RedirectRequired till /login."""
    user = get_current_user(request)
    if not user:
        raise RedirectRequired("/login")
    return user


def get_admin_or_redirect(request: Request) -> dict:
    """Returnerar inloggad admin-användare eller kastar RedirectRequired till /login."""
    user = get_current_user(request)
    if not user or not user["is_admin"]:
        raise RedirectRequired("/login")
    return user


def check_rate_limit(db, ip: str, action: str, limit: int = RATE_LIMIT_PER_HOUR) -> bool:
    """Returnerar True om begäran är tillåten, False om rate limit nåtts.

    Registrerar automatiskt begäran i rate_limits-tabellen vid framgång. ip är
    en fri nyckel - flöden som kräver inloggning nycklar hellre på user:<id>
    eller email:<adress> än på adressen, eftersom en proxy annars kan ge alla
    besökare samma hink.
    """
    # Jämförelsen görs i SQL mot datetime('now'), inte mot en ISO-sträng från
    # Python: created_at sätts av CURRENT_TIMESTAMP och skrivs "2026-08-20
    # 20:49:27", medan isoformat() ger "2026-08-20T19:49:46". SQLite jämför dem
    # som strängar, och mellanslag sorterar före T - så varje rad föll utanför
    # fönstret och räknaren blev alltid 0.
    count = db.execute(
        "SELECT COUNT(*) FROM rate_limits "
        "WHERE ip=? AND action=? AND created_at > datetime('now', '-1 hour')",
        (ip, action),
    ).fetchone()[0]
    if count >= limit:
        return False
    db.execute("INSERT INTO rate_limits (ip, action) VALUES (?, ?)", (ip, action))
    return True


def user_allows_any_domain(email: str) -> bool:
    """Returnerar True om användaren har allow_any_domain=1 i databasen."""
    with get_db() as db:
        row = db.execute("SELECT allow_any_domain FROM users WHERE email=?", (email,)).fetchone()
    return bool(row["allow_any_domain"]) if row else False


def user_allows_external_urls(email: str) -> bool:
    """Returnerar True om användaren har allow_external_urls=1 i databasen."""
    with get_db() as db:
        row = db.execute("SELECT allow_external_urls FROM users WHERE email=?", (email,)).fetchone()
    return bool(row["allow_external_urls"]) if row else False
