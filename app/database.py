import sqlite3
import os
from contextlib import contextmanager

DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/links.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DATABASE_PATH) if os.path.dirname(DATABASE_PATH) else ".", exist_ok=True)
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY,
                email      TEXT UNIQUE NOT NULL,
                is_admin   INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_login DATETIME
            );

            CREATE TABLE IF NOT EXISTS links (
                id           INTEGER PRIMARY KEY,
                code         TEXT UNIQUE NOT NULL,
                target_url   TEXT NOT NULL,
                owner_id     INTEGER REFERENCES users(id),
                status       INTEGER DEFAULT 0,
                note         TEXT,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_used_at DATETIME
            );

            CREATE TABLE IF NOT EXISTS tokens (
                id         INTEGER PRIMARY KEY,
                token      TEXT UNIQUE NOT NULL,
                user_id    INTEGER REFERENCES users(id),
                link_id    INTEGER REFERENCES links(id),
                purpose    TEXT NOT NULL,
                expires_at DATETIME NOT NULL,
                used_at    DATETIME
            );

            CREATE TABLE IF NOT EXISTS clicks (
                id         INTEGER PRIMARY KEY,
                link_id    INTEGER REFERENCES links(id),
                clicked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                referer    TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY,
                action     TEXT NOT NULL,
                actor_id   INTEGER REFERENCES users(id),
                link_id    INTEGER REFERENCES links(id),
                detail     TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rate_limits (
                id         INTEGER PRIMARY KEY,
                ip         TEXT NOT NULL,
                action     TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_links_code ON links(code);
            CREATE INDEX IF NOT EXISTS idx_tokens_token ON tokens(token);
            CREATE INDEX IF NOT EXISTS idx_clicks_link_id ON clicks(link_id);
        """)
        conn.commit()
