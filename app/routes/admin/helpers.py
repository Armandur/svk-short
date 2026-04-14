"""Interna hjälpfunktioner gemensamma för admin-submoduler."""

import sqlite3


def pending_takeover_count(db: sqlite3.Connection) -> int:
    """Returnerar antal väntande överlåtelseförfrågningar (länkar + samlingar)."""
    links = db.execute("SELECT COUNT(*) FROM takeover_requests WHERE status='pending'").fetchone()[
        0
    ]
    bundles = db.execute(
        "SELECT COUNT(*) FROM bundle_takeover_requests WHERE status='pending'"
    ).fetchone()[0]
    return links + bundles
