"""Generering av unika kortlänkskoder.

Alfabet: 30 tecken utan visuellt förvirrande homoglyfer (0/O, 1/l/I).
7 tecken → 30^7 ≈ 21,9 miljarder kombinationer ≈ 34 bitar entropi.
Kollar mot både links- och bundles-tabellen för att undvika kollisioner.
"""

import secrets

_MAX_ATTEMPTS = 10
# Exkluderar: 0 (O-likhet), 1 (l/I-likhet), 'i' (I-likhet), 'o' (0-likhet)
_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"


def generate_unique_code(db) -> str:
    """Generera en slumpmässig unik 7-teckenskod som inte krockar med
    befintliga links eller bundles.

    Kastar RuntimeError om ingen unik kod hittas på _MAX_ATTEMPTS försök
    (indikerar fullt namnrymd eller trasigt DB-index).
    """
    for _ in range(_MAX_ATTEMPTS):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(7))
        link_exists = db.execute("SELECT 1 FROM links WHERE code=?", (code,)).fetchone()
        bundle_exists = db.execute("SELECT 1 FROM bundles WHERE code=?", (code,)).fetchone()
        if not link_exists and not bundle_exists:
            return code
    raise RuntimeError(
        f"Kunde inte generera en unik kod efter {_MAX_ATTEMPTS} försök — "
        "databasen kan vara full eller DB-indexet är skadat."
    )
