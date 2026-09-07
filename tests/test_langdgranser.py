import pytest

from app import database
from app.validation import (
    MAX_BODY_LENGTH,
    MAX_EMAIL_LENGTH,
    MAX_ICON_LENGTH,
    MAX_NAME_LENGTH,
    MAX_TEXT_LENGTH,
    MAX_URL_LENGTH,
)


def _skapa_bundle(owner_id: int) -> int:
    with database.get_db() as db:
        db.execute(
            "INSERT INTO bundles (code, name, owner_id) VALUES ('langdtest', 'Före', ?)",
            (owner_id,),
        )
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]


# Om URL-gränsen har off-by-one lagras ingen gräns-URL eller en överlång URL.
def test_url_langdgranser_mats_via_bestall_route(
    client, inloggad_anvandare, hamta_csrf_token
):
    prefix = "https://x.se/"
    at_limit = prefix + "a" * (MAX_URL_LENGTH - len(prefix))
    too_long = at_limit + "a"
    csrf_token = hamta_csrf_token(client, "/bestall")
    accepted = client.post(
        "/bestall",
        data={"target_url": at_limit, "code": "urlgrans", "csrf_token": csrf_token},
    )
    rejected = client.post(
        "/bestall",
        data={"target_url": too_long, "code": "urlforlang", "csrf_token": csrf_token},
    )
    assert accepted.status_code == 303
    assert rejected.status_code == 422
    with database.get_db() as db:
        rows = db.execute(
            "SELECT code, length(target_url) FROM links WHERE code IN ('urlgrans', 'urlforlang')"
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [("urlgrans", MAX_URL_LENGTH)]


# Om textgränsen har off-by-one lagras ingen gränsnotering eller en överlång notering.
def test_text_langdgranser_mats_via_bestall_route(
    client, inloggad_anvandare, hamta_csrf_token
):
    csrf_token = hamta_csrf_token(client, "/bestall")
    accepted = client.post(
        "/bestall",
        data={
            "target_url": "https://www.svenskakyrkan.se/grans",
            "code": "textgrans",
            "note": "a" * MAX_TEXT_LENGTH,
            "csrf_token": csrf_token,
        },
    )
    rejected = client.post(
        "/bestall",
        data={
            "target_url": "https://www.svenskakyrkan.se/forlang",
            "code": "textforlang",
            "note": "a" * (MAX_TEXT_LENGTH + 1),
            "csrf_token": csrf_token,
        },
    )
    assert accepted.status_code == 303
    assert rejected.status_code == 422
    with database.get_db() as db:
        rows = db.execute(
            "SELECT code, length(note) FROM links WHERE code IN ('textgrans', 'textforlang')"
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [("textgrans", MAX_TEXT_LENGTH)]


# Om e-postgränsen har off-by-one skapas inte gränsadressen eller den överlånga adressen.
def test_epost_langdgranser_mats_via_login_route(client, hamta_csrf_token, monkeypatch):
    monkeypatch.setattr("app.routes.auth.skicka_loginmail", lambda *args, **kwargs: None)
    suffix = "@svenskakyrkan.se"
    at_limit = "a" * (MAX_EMAIL_LENGTH - len(suffix)) + suffix
    too_long = "a" + at_limit
    accepted = client.post(
        "/login", data={"email": at_limit, "csrf_token": hamta_csrf_token(client)}
    )
    rejected = client.post(
        "/login", data={"email": too_long, "csrf_token": hamta_csrf_token(client)}
    )
    assert accepted.status_code == 200
    assert rejected.status_code == 422
    with database.get_db() as db:
        emails = [row[0] for row in db.execute("SELECT email FROM users").fetchall()]
    assert emails == [at_limit]


@pytest.mark.parametrize(
    ("field", "limit", "route_suffix", "column", "base_data"),
    [
        ("name", MAX_NAME_LENGTH, "update", "name", {"description": "", "theme": "rich"}),
        ("body_md", MAX_BODY_LENGTH, "update-body", "body_md", {}),
    ],
)
# Om gränsen har off-by-one sparas det överlånga värdet eller inte gränsvärdet.
def test_samlingsfaltens_langdgranser_mats_via_route(
    client,
    inloggad_anvandare,
    hamta_csrf_token,
    field,
    limit,
    route_suffix,
    column,
    base_data,
):
    bundle_id = _skapa_bundle(inloggad_anvandare["id"])
    csrf_token = hamta_csrf_token(client, f"/mina-samlingar/{bundle_id}")
    accepted = client.post(
        f"/mina-samlingar/{bundle_id}/{route_suffix}",
        data={**base_data, field: "a" * limit, "csrf_token": csrf_token},
    )
    rejected = client.post(
        f"/mina-samlingar/{bundle_id}/{route_suffix}",
        data={**base_data, field: "b" * (limit + 1), "csrf_token": csrf_token},
    )
    assert accepted.status_code == 303
    assert rejected.status_code == 303
    assert "item_error=fel" in rejected.headers["location"]
    with database.get_db() as db:
        value = db.execute(f"SELECT {column} FROM bundles WHERE id=?", (bundle_id,)).fetchone()[0]
    assert value == "a" * limit


# Om ikongränsen har off-by-one lagras ingen gränsikon eller den överlånga ikonen.
def test_ikonens_langdgrans_mats_via_item_route(
    client, inloggad_anvandare, hamta_csrf_token
):
    bundle_id = _skapa_bundle(inloggad_anvandare["id"])
    csrf_token = hamta_csrf_token(client, f"/mina-samlingar/{bundle_id}")
    base = {"title": "Titel", "url": "https://example.test", "csrf_token": csrf_token}
    accepted = client.post(
        f"/mina-samlingar/{bundle_id}/items", data={**base, "icon": "a" * MAX_ICON_LENGTH}
    )
    rejected = client.post(
        f"/mina-samlingar/{bundle_id}/items",
        data={**base, "title": "Andra", "icon": "b" * (MAX_ICON_LENGTH + 1)},
    )
    assert accepted.status_code == 303
    assert rejected.status_code == 303
    assert "item_error=fel" in rejected.headers["location"]
    with database.get_db() as db:
        icons = [row[0] for row in db.execute("SELECT icon FROM bundle_items").fetchall()]
    assert icons == ["a" * MAX_ICON_LENGTH]
