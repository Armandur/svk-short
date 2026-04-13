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

            CREATE TABLE IF NOT EXISTS page_views (
                id         INTEGER PRIMARY KEY,
                path       TEXT NOT NULL,
                viewed_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                referer    TEXT
            );

            CREATE TABLE IF NOT EXISTS takeover_requests (
                id               INTEGER PRIMARY KEY,
                link_id          INTEGER NOT NULL REFERENCES links(id),
                requester_email  TEXT NOT NULL,
                reason           TEXT,
                status           TEXT NOT NULL DEFAULT 'pending',
                created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at      DATETIME
            );

            CREATE TABLE IF NOT EXISTS site_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transfer_requests (
                id              INTEGER PRIMARY KEY,
                link_id         INTEGER NOT NULL REFERENCES links(id),
                from_user_id    INTEGER NOT NULL REFERENCES users(id),
                to_email        TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at     DATETIME
            );

            CREATE INDEX IF NOT EXISTS idx_page_views_viewed_at ON page_views(viewed_at);
            CREATE INDEX IF NOT EXISTS idx_links_code ON links(code);
            CREATE INDEX IF NOT EXISTS idx_tokens_token ON tokens(token);
            CREATE INDEX IF NOT EXISTS idx_clicks_link_id ON clicks(link_id);
            CREATE INDEX IF NOT EXISTS idx_takeover_link ON takeover_requests(link_id);
            CREATE INDEX IF NOT EXISTS idx_takeover_status ON takeover_requests(status);
            CREATE INDEX IF NOT EXISTS idx_transfer_link ON transfer_requests(link_id);
            CREATE INDEX IF NOT EXISTS idx_transfer_status ON transfer_requests(status);
        """)
        default_integritet = (
            "## Vad lagrar vi?\n\n"
            "För att tjänsten ska fungera sparas följande:\n\n"
            "- **E-postadress** — används för att verifiera att du är anställd inom Svenska kyrkan "
            "och för att kunna logga in. Adressen kopplas till de kortlänkar du skapar.\n"
            "- **Kortlänkar** — kod, mål-URL och en valfri notering.\n"
            "- **Klickstatistik** — varje gång någon följer en kortlänk sparas tidpunkt och eventuell "
            "referer-header. Inga IP-adresser lagras.\n"
            "- **Inloggningstidpunkt** — senaste gången du loggade in.\n\n"
            "## Vad lagras inte?\n\n"
            "- Inga lösenord (inloggning sker via engångslänk till din e-post)\n"
            "- Inga IP-adresser\n"
            "- Inga cookies utöver den inloggningscookie som krävs för sessionen\n\n"
            "## Vilka system används?\n\n"
            "- **Server:** Hetzner Cloud, datacenter i Helsingfors, Finland (inom EU)\n"
            "- **E-post:** [Lettermint](https://lettermint.co) används för att skicka "
            "verifierings- och inloggningslänkar\n"
            "- **Databas:** SQLite-fil på samma server\n\n"
            "## Hur länge sparas uppgifterna?\n\n"
            "Uppgifter raderas inte automatiskt. Om du vill att din e-postadress eller dina "
            "kortlänkar tas bort, kontakta tjänstens administratör.\n\n"
            "## Kontakt\n\n"
            "Frågor om personuppgifter hanteras av tjänstens administratör. "
            "Tjänsten är inte en officiell tjänst från Svenska kyrkan nationellt."
        )
        conn.execute(
            "INSERT OR IGNORE INTO site_settings (key, value) VALUES ('integritet_content', ?)",
            (default_integritet,),
        )

        default_about = (
            "## Vad är det här?\n\n"
            "svky.se är en intern URL-förkortare för anställda inom Svenska kyrkan. "
            "Tjänsten gör det enkelt att skapa korta, minnesvärda länkar till sidor "
            "under svenskakyrkan.se — utan att behöva kontakta IT.\n\n"
            "## Vem driver det?\n\n"
            "Tjänsten drivs privat av **Armandur**. Den är inte en officiell "
            "tjänst från Svenska kyrkan nationellt, men är öppen för alla medarbetare med "
            "en @svenskakyrkan.se-adress.\n\n"
            "## Varför finns den?\n\n"
            "Behovet av att dela korta, snygga länkar inom organisationen är stort — oavsett "
            "om det gäller interna dokument, konfirmationsgrupper, kampanjer eller "
            "informationssidor. Det ska vara enkelt och snabbt.\n\n"
            "## Tekniken\n\n"
            "Byggt med Python och FastAPI, kör på en liten server hos Hetzner. "
            "Inga lösenord lagras — inloggning och verifiering sker via engångslänkar "
            "till din e-post.\n\n"
            "---\n\n"
            "☕ **Uppskatta tjänsten?** Tjänsten kostar en slant i månaden att driva. "
            "Om du vill bidra till kostnaderna är en tia välkommen via Swish till "
            "**070 000 00 00**. Inget krav — bara tack!"
        )
        conn.execute(
            "INSERT OR IGNORE INTO site_settings (key, value) VALUES ('about_content', ?)",
            (default_about,),
        )
        conn.commit()


def log_page_view(path: str, referer: str | None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO page_views (path, referer) VALUES (?, ?)",
            (path, referer),
        )
        conn.commit()
    finally:
        conn.close()
