from urllib.parse import unquote

import pytest

from app import database
from app.auth import COOKIE_NAME, create_session_cookie
from app.config import RATE_LIMIT_PER_HOUR


def _skapa_agarobjekt(user_id: int) -> tuple[int, int]:
    with database.get_db() as db:
        db.execute(
            "INSERT INTO links (code, target_url, owner_id, status) VALUES ('minlank', ?, ?, 1)",
            ("https://www.svenskakyrkan.se/minlank", user_id),
        )
        link_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO bundles (code, name, owner_id) VALUES ('minsamling', 'Min samling', ?)",
            (user_id,),
        )
        bundle_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return link_id, bundle_id


@pytest.mark.parametrize("endpoint", ["link", "bundle", "all"])
# Om en route saknar användartaktning skapas en transfer trots fem rader i hinken.
def test_overlatelse_endpoints_taktas_per_anvandare(
    client, inloggad_anvandare, hamta_csrf_token, endpoint
):
    user_id = inloggad_anvandare["id"]
    link_id, bundle_id = _skapa_agarobjekt(user_id)
    with database.get_db() as db:
        db.executemany(
            "INSERT INTO rate_limits (ip, action) VALUES (?, 'transfer')",
            [(f"user:{user_id}",)] * RATE_LIMIT_PER_HOUR,
        )
    csrf_token = hamta_csrf_token(client, "/mina-lankar")
    routes = {
        "link": f"/mina-lankar/{link_id}/request-transfer",
        "bundle": f"/mina-samlingar/{bundle_id}/request-transfer",
        "all": "/mina-lankar/request-transfer-all",
    }
    response = client.post(
        routes[endpoint],
        data={"to_email": "mottagare@svenskakyrkan.se", "csrf_token": csrf_token},
    )
    message = response.text + unquote(response.headers.get("location", ""))
    assert response.status_code in (303, 422, 429)
    assert "För många överlåtelseförfrågningar" in message
    with database.get_db() as db:
        transfer_count = db.execute("SELECT COUNT(*) FROM transfer_requests").fetchone()[0]
        bundle_count = db.execute("SELECT COUNT(*) FROM bundle_transfers").fetchone()[0]
    assert transfer_count == 0
    assert bundle_count == 0


# Om hinken nycklas på IP nekas den andra användaren av första användarens rader.
def test_overlatelse_hink_ar_atskild_mellan_anvandare(
    client, inloggad_anvandare, hamta_csrf_token, monkeypatch
):
    first_user_id = inloggad_anvandare["id"]
    with database.get_db() as db:
        db.executemany(
            "INSERT INTO rate_limits (ip, action) VALUES (?, 'transfer')",
            [(f"user:{first_user_id}",)] * RATE_LIMIT_PER_HOUR,
        )
        db.execute("INSERT INTO users (email) VALUES ('andra@svenskakyrkan.se')")
        second_user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO links (code, target_url, owner_id, status) VALUES ('andralank', ?, ?, 1)",
            ("https://www.svenskakyrkan.se/andra", second_user_id),
        )
        link_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    client.cookies.set(COOKIE_NAME, create_session_cookie(second_user_id))
    monkeypatch.setattr(
        "app.routes.user.links.skicka_overlatelseforfragan", lambda *args, **kwargs: None
    )
    csrf_token = hamta_csrf_token(client, "/mina-lankar")
    response = client.post(
        f"/mina-lankar/{link_id}/request-transfer",
        data={"to_email": "mottagare@svenskakyrkan.se", "csrf_token": csrf_token},
    )
    assert response.status_code == 303
    with database.get_db() as db:
        row = db.execute(
            "SELECT from_user_id FROM transfer_requests WHERE link_id=?", (link_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == second_user_id
