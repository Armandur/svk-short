"""Notisbannern: adminens meddelande till alla besökare.

Bannern renderas på VARJE sida, felsidorna inräknade. Proven riktar sig
därför både mot att den syns när den ska, och mot att den inte kan fälla
ett svar när uppslagningen går sönder.
"""

import pytest

from app import database, templating


def _spara(client, hamta_csrf_token, text, niva="info"):
    token = hamta_csrf_token(client, "/admin/notis")
    return client.post(
        "/admin/notis", data={"content": text, "niva": niva, "csrf_token": token}
    )


def test_ingen_banner_nar_texten_saknas(client):
    assert "notisbanner" not in client.get("/").text


def test_utloggad_nekas_redigeraren(client):
    svar = client.get("/admin/notis")

    assert svar.status_code == 303
    assert "/login" in svar.headers["location"]


def test_vanlig_anvandare_nekas_redigeraren(client, inloggad_anvandare):
    assert client.get("/admin/notis").status_code == 303


def test_sparande_utan_csrf_nekas(client, admin):
    svar = client.post(
        "/admin/notis", data={"content": "Driftstopp", "niva": "info", "csrf_token": "fel"}
    )

    assert svar.status_code == 403
    assert "Driftstopp" not in client.get("/").text


@pytest.mark.parametrize("sida", ["/", "/bestall", "/om", "/integritet", "/nyheter"])
def test_bannern_syns_pa_alla_publika_sidor(client, admin, hamta_csrf_token, sida):
    _spara(client, hamta_csrf_token, "Driftstopp på torsdag.")

    svar = client.get(sida)

    # Utan den här raden mäter provet ingenting på en sida som svarar 303:
    # en tom kropp saknar bannern av fel skäl.
    assert svar.status_code == 200, f"{sida} svarade {svar.status_code}"
    text = svar.text
    assert "Driftstopp på torsdag." in text
    assert "notisbanner-info" in text


def test_bannern_syns_inte_pa_404(client, admin, hamta_csrf_token):
    """404 möter den som klickat någon annans kortlänk.

    Hen är mottagare, inte användare av tjänsten - ett driftmeddelande om
    verktyget säger ingenting, och sidan ska bara svara på varför länken
    inte fungerade.
    """
    _spara(client, hamta_csrf_token, "Driftstopp på torsdag.")

    svar = client.get("/en-kod-som-inte-finns")

    assert svar.status_code == 404
    assert "Driftstopp på torsdag." not in svar.text
    assert "notisbanner" not in svar.text


def test_bannern_syns_inte_pa_en_samling(client, admin, hamta_csrf_token):
    """En publicerad samling läses av mottagare, precis som en kortlänk.

    Skyddet vilar i dag på att bundle.html inte ärver base.html. Det är en
    tyst förutsättning, och provet är det som säger till om någon lägger om
    mallen att ärva.
    """
    _spara(client, hamta_csrf_token, "Driftstopp på torsdag.")
    with database.get_db() as db:
        db.execute(
            "INSERT INTO bundles (code, name, owner_id, status) VALUES "
            "('minsamling', 'Min samling', 1, 1)"
        )

    svar = client.get("/minsamling")

    assert svar.status_code == 200, "samlingen visades inte, provet mäter inget"
    assert "Min samling" in svar.text
    assert "Driftstopp på torsdag." not in svar.text


def test_nivan_styr_klassen(client, admin, hamta_csrf_token):
    _spara(client, hamta_csrf_token, "Läget är allvarligt.", niva="varning")

    assert "notisbanner-varning" in client.get("/").text


def test_okand_niva_blir_info(client, admin, hamta_csrf_token):
    """Nivån kommer från ett formulär och är indata utifrån, även om den ser
    ut som ett val mellan två knappar."""
    _spara(client, hamta_csrf_token, "Hej.", niva="<script>")

    text = client.get("/").text
    assert "notisbanner-info" in text
    assert "<script>" not in text


def test_tom_text_tar_bort_bannern(client, admin, hamta_csrf_token):
    _spara(client, hamta_csrf_token, "Driftstopp på torsdag.")
    assert "notisbanner" in client.get("/").text

    _spara(client, hamta_csrf_token, "   ")

    assert "notisbanner" not in client.get("/").text


def test_markdown_lanken_blir_klickbar(client, admin, hamta_csrf_token):
    _spara(client, hamta_csrf_token, "Se [nyheterna](/nyheter).")

    assert 'href="/nyheter"' in client.get("/").text


def test_trasig_uppslagning_falller_inte_sidan(client, monkeypatch, caplog):
    """En banner som inte går att läsa får inte bli det som gör sidan tom.

    Uppslagningen körs på varje renderad sida, felsidorna inräknade. Utan
    det fångade felet hade en trasig notis blivit det som gör felsidan
    oläsbar - precis när den behövs.
    """

    def _sprangs():
        raise RuntimeError("databasen är borta")

    monkeypatch.setattr(database, "get_db", _sprangs)

    assert templating._notisbanner() is None
    assert "notisbannern" in caplog.text.lower(), "felet loggades inte"
    assert client.get("/").status_code == 200
    assert client.get("/en-kod-som-inte-finns").status_code == 404


def test_admin_baren_lankar_till_notisen(client, admin):
    assert 'href="/admin/notis"' in client.get("/admin/links").text
