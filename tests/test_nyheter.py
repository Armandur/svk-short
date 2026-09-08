"""Nyhetssidan: publik läsning, admin skriver.

Proven anropar ROUTEN och inte bara databasen. En sida som renderar rätt
säger ingenting om vem som släpps in, och det är behörighetsspärren som är
värd att mäta här.
"""

from app.config import RESERVED_CODES


def test_publika_sidan_visar_innehallet(client):
    svar = client.get("/nyheter")

    assert svar.status_code == 200
    assert "Nyheter" in svar.text
    # Standardtexten seedas vid init. Utan den ser en tom sida likadan ut
    # som en trasig.
    assert "Så här funkar sidan" in svar.text


def test_footern_lankar_till_nyheter(client):
    """Sidan är oanvändbar om ingen hittar dit."""
    assert 'href="/nyheter"' in client.get("/").text


def test_koden_ar_reserverad():
    """/nyheter skuggar catch-all-routen GET /<kod>. Utan reservationen kan
    någon beställa kortkoden och få en länk som aldrig går att klicka på."""
    assert "nyheter" in RESERVED_CODES


def test_utloggad_nekas_redigeraren(client):
    svar = client.get("/admin/nyheter")

    assert svar.status_code == 303
    assert "/login" in svar.headers["location"]


def test_vanlig_anvandare_nekas_redigeraren(client, inloggad_anvandare):
    assert client.get("/admin/nyheter").status_code == 303


def test_admin_far_redigeraren(client, admin):
    svar = client.get("/admin/nyheter")

    assert svar.status_code == 200
    assert "/nyheter" in svar.text


def test_sparande_utan_csrf_nekas(client, admin):
    svar = client.post("/admin/nyheter", data={"content": "hej", "csrf_token": "fel"})

    assert svar.status_code == 403
    assert "hej" not in client.get("/nyheter").text


def test_admin_sparar_och_texten_syns_publikt(client, admin, hamta_csrf_token):
    token = hamta_csrf_token(client, "/admin/nyheter")

    svar = client.post(
        "/admin/nyheter",
        data={"content": "## Ny funktion\n\nQR-koder finns nu.", "csrf_token": token},
    )

    assert svar.status_code == 303
    publikt = client.get("/nyheter").text
    assert "Ny funktion" in publikt
    assert "QR-koder finns nu." in publikt


def test_admin_baren_lankar_till_nyheter(client, admin):
    assert 'href="/admin/nyheter"' in client.get("/admin/links").text
