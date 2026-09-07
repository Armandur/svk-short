"""Att formulär går att skicka in, både utloggad och inloggad.

Två buggar av samma familj (TASK-1679, TASK-1685). GET-handlern signerade
formulärets token med anon-hemligheten, medan POST validerade med
get_csrf_secret() som prioriterar sessionen. Var besökaren inloggad jämfördes
två olika hemligheter och knappen gav 403. I takeovers.py saknades anropet
helt, så token renderades tomt och flödet var obrukbart för utloggade.
"""

import re
import secrets
from datetime import UTC, datetime, timedelta

import pytest

from app import database


def _token_ur(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match, "formuläret saknar csrf_token-fältet helt"
    return match.group(1)


def _skapa_pending_lank() -> str:
    """Länk som väntar på verifiering, plus dess verify-token."""
    with database.get_db() as db:
        db.execute("INSERT INTO users (email) VALUES ('agare@svenskakyrkan.se')")
        uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO links (code, target_url, owner_id, status) VALUES (?,?,?,0)",
            ("provkod", "https://www.svenskakyrkan.se/prov", uid),
        )
        lid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        token = secrets.token_hex(32)
        db.execute(
            "INSERT INTO tokens (token, user_id, link_id, purpose, expires_at) "
            "VALUES (?,?,?,'verify',?)",
            (
                token,
                uid,
                lid,
                (datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)).isoformat(),
            ),
        )
    return token


# Utan fixen renderas token med anon-hemligheten medan POST validerar mot
# sessionens - statusen blir 403 i stället för 200 och länken förblir pending.
def test_inloggad_kan_verifiera_kortlank(client, inloggad_anvandare):
    token = _skapa_pending_lank()
    csrf = _token_ur(client.get(f"/verify/{token}").text)
    assert csrf, "inloggad besökare fick tomt csrf_token på bekräftelsesidan"

    svar = client.post(f"/verify/{token}", data={"csrf_token": csrf})

    assert svar.status_code != 403
    with database.get_db() as db:
        status = db.execute("SELECT status FROM links WHERE code='provkod'").fetchone()[0]
    assert status == 1, "länken aktiverades inte"


def test_utloggad_kan_verifiera_kortlank(client):
    token = _skapa_pending_lank()
    csrf = _token_ur(client.get(f"/verify/{token}").text)
    assert csrf

    svar = client.post(f"/verify/{token}", data={"csrf_token": csrf})

    assert svar.status_code != 403
    with database.get_db() as db:
        status = db.execute("SELECT status FROM links WHERE code='provkod'").fetchone()[0]
    assert status == 1


# Utan fixen saknar takeovers.py anropet helt: value blir tom sträng och
# POST:en faller på 403 för alla utloggade.
@pytest.mark.parametrize("route", ["/request/takeover", "/request/bundle-takeover"])
def test_utloggad_far_anvandbart_token_pa_takeoverformularet(client, route):
    svar = client.get(f"{route}?code=nagot")
    assert svar.status_code == 200
    csrf = _token_ur(svar.text)

    assert csrf, f"{route} renderade ett tomt csrf_token"

    post = client.post(
        route,
        data={
            "csrf_token": csrf,
            "code": "nagot",
            "email": "ny@svenskakyrkan.se",
            "reason": "prov",
        },
    )
    assert post.status_code != 403, "formuläret gick inte att skicka in"


def test_inloggad_far_anvandbart_token_pa_takeoverformularet(client, inloggad_anvandare):
    svar = client.get("/request/takeover?code=nagot")
    csrf = _token_ur(svar.text)
    assert csrf

    post = client.post(
        "/request/takeover",
        data={
            "csrf_token": csrf,
            "code": "nagot",
            "email": "ny@svenskakyrkan.se",
            "reason": "prov",
        },
    )
    assert post.status_code != 403
