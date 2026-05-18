"""Hantering av admin-konfigurerade tillåtna måldomäner för kortlänkar.

Tabellen `allowed_domains` styr vilka domäner vanliga användare får peka
kortlänkar mot. Trusted-användare och admin (allow_external=True) går förbi
listan helt.

Leaf-modul: importerar bara app.database, så validation.py kan importera
härifrån utan cyklar.
"""

import re
import sqlite3

from app.database import get_db

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def normalize_domain(raw: str) -> str:
    """Normalisera admininmatad domän till bara värdnamnet.

    Strippar schema (https://), inledande '*.', userinfo, port, samt
    eventuell sökväg. Returnerar gemener.
    """
    d = (raw or "").strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0]
    d = d.split("@")[-1]
    d = d.split(":", 1)[0]
    if d.startswith("*."):
        d = d[2:]
    return d.strip(".")


def validate_domain(domain: str) -> str | None:
    """Returnerar felmeddelande (str) eller None om domänen är giltig."""
    if not domain:
        return "Domänen får inte vara tom."
    if len(domain) > 253:
        return "Domänen är för lång."
    if not _DOMAIN_RE.match(domain):
        return "Ogiltig domän. Ange t.ex. svenskakyrkan.luvit.se."
    return None


def get_allowed_domains() -> list[sqlite3.Row]:
    """Hämtar alla tillåtna domäner sorterade på domännamn."""
    with get_db() as db:
        return db.execute(
            """SELECT id, domain, include_subdomains, allow_free_url, note, created_at
               FROM allowed_domains ORDER BY domain"""
        ).fetchall()


def match_domain(host: str, rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    """Returnerar raden som matchar `host`, eller None om ingen matchar.

    Exakt matchning alltid; subdomänmatchning bara när include_subdomains=1.
    """
    host = (host or "").lower()
    for r in rows:
        d = r["domain"]
        if host == d:
            return r
        if r["include_subdomains"] and host.endswith("." + d):
            return r
    return None
