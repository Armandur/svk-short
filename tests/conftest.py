import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app import database
from app.auth import COOKIE_NAME, create_session_cookie
from app.main import app


async def _app_med_testadress(scope, receive, send):
    """Låter prov ange klientadress innan routen tar emot begäran."""
    headers = dict(scope.get("headers", []))
    test_ip = headers.get(b"x-test-client-ip")
    if test_ip:
        scope = dict(scope)
        scope["client"] = (test_ip.decode(), 50000)
    await app(scope, receive, send)


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """Kör appen mot en ny SQLite-databas för varje prov."""
    database_path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(database_path))
    with TestClient(_app_med_testadress, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture
def hamta_csrf_token() -> Callable[[TestClient, str], str]:
    """Hämtar ett giltigt CSRF-token från ett formulär på angiven route."""

    def _hamta(client: TestClient, route: str = "/login") -> str:
        response = client.get(route)
        assert response.status_code == 200
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
        assert match, f"Formuläret på {route} saknar csrf_token"
        return match.group(1)

    return _hamta


def _skapa_session(client: TestClient, is_admin: bool) -> dict:
    email = "admin@svenskakyrkan.se" if is_admin else "user@svenskakyrkan.se"
    with database.get_db() as db:
        db.execute(
            "INSERT INTO users (email, is_admin, allow_external_urls) VALUES (?, ?, 1)",
            (email, int(is_admin)),
        )
        user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    client.cookies.set(COOKIE_NAME, create_session_cookie(user_id))
    return {"id": user_id, "email": email, "is_admin": int(is_admin)}


@pytest.fixture
def inloggad_anvandare(client: TestClient) -> dict:
    """Skapar en användare och loggar in TestClient som den användaren."""
    return _skapa_session(client, is_admin=False)


@pytest.fixture
def admin(client: TestClient) -> dict:
    """Skapar en administratör och loggar in TestClient som administratören."""
    return _skapa_session(client, is_admin=True)
