import pytest

from app import database
from app.auth import create_takeover_action_token


def _skapa_takeover(kind: str) -> tuple[int, int]:
    with database.get_db() as db:
        db.execute("INSERT INTO users (email) VALUES ('agare@svenskakyrkan.se')")
        owner_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        if kind == "link":
            db.execute(
                "INSERT INTO links (code, target_url, owner_id, status) VALUES (?, ?, ?, 1)",
                ("offermal", "https://www.svenskakyrkan.se/test", owner_id),
            )
            object_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO takeover_requests (link_id, requester_email) VALUES (?, ?)",
                (object_id, "ny@svenskakyrkan.se"),
            )
        else:
            db.execute(
                "INSERT INTO bundles (code, name, owner_id) VALUES (?, ?, ?)",
                ("offersamling", "Offersamling", owner_id),
            )
            object_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO bundle_takeover_requests (bundle_id, requester_email) VALUES (?, ?)",
                (object_id, "ny@svenskakyrkan.se"),
            )
        request_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return object_id, request_id


def _hamta_lage(kind: str, object_id: int, request_id: int) -> tuple[int, str]:
    object_table = "links" if kind == "link" else "bundles"
    request_table = "takeover_requests" if kind == "link" else "bundle_takeover_requests"
    with database.get_db() as db:
        owner_id = db.execute(
            f"SELECT owner_id FROM {object_table} WHERE id=?", (object_id,)
        ).fetchone()[0]
        status = db.execute(
            f"SELECT status FROM {request_table} WHERE id=?", (request_id,)
        ).fetchone()[0]
    return owner_id, status


# Om behörighetskontrollen saknas ändras owner_id och status från pending till approved.
@pytest.mark.parametrize("kind", ["link", "bundle"])
def test_takeover_utan_admin_flyttar_inte_agandet(
    client, hamta_csrf_token, monkeypatch, kind
):
    monkeypatch.setattr(
        "app.routes.admin.takeovers.skicka_overlatelse_godkand", lambda *args, **kwargs: None
    )
    object_id, request_id = _skapa_takeover(kind)
    before = _hamta_lage(kind, object_id, request_id)
    assert before[1] == "pending"
    token = create_takeover_action_token(request_id, "approve", kind)
    csrf_token = hamta_csrf_token(client)

    response = client.post(
        f"/admin/takeover-action/{token}", data={"csrf_token": csrf_token}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert _hamta_lage(kind, object_id, request_id) == before


# Om adminspärren blir för hård förblir owner_id oförändrat och status pending.
@pytest.mark.parametrize("kind", ["link", "bundle"])
def test_takeover_med_admin_flyttar_agandet(
    client, admin, hamta_csrf_token, monkeypatch, kind
):
    object_id, request_id = _skapa_takeover(kind)
    before_owner, before_status = _hamta_lage(kind, object_id, request_id)
    assert before_status == "pending"
    monkeypatch.setattr(
        "app.routes.admin.takeovers.skicka_overlatelse_godkand", lambda *args, **kwargs: None
    )
    token = create_takeover_action_token(request_id, "approve", kind)
    csrf_token = hamta_csrf_token(client, f"/admin/takeover-action/{token}")

    response = client.post(
        f"/admin/takeover-action/{token}", data={"csrf_token": csrf_token}
    )

    after_owner, after_status = _hamta_lage(kind, object_id, request_id)
    assert response.status_code == 303
    assert after_owner != before_owner
    assert after_status == "approved"
