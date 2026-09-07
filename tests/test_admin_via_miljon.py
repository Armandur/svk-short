"""ADMIN_EMAILS: adminrätt ur miljön, för miljöer med färsk databas."""

import pytest

from app import database


def _satt(monkeypatch, varde: str) -> None:
    monkeypatch.setattr(database, "ADMIN_EMAILS", varde)


def _ar_admin(email: str) -> int | None:
    with database.get_db() as db:
        rad = db.execute("SELECT is_admin FROM users WHERE email=?", (email,)).fetchone()
    return None if rad is None else rad[0]


# Utan detta måste en färsk stagingdatabas få en UPDATE för hand efter varje
# omstart, och kontot finns inte ens förrän någon loggat in en gång.
def test_kontot_skapas_och_blir_admin(client, monkeypatch):
    _satt(monkeypatch, "chef@svenskakyrkan.se")
    with database.get_db() as db:
        database._seed_admins(db)
    assert _ar_admin("chef@svenskakyrkan.se") == 1


@pytest.mark.parametrize(
    "varde",
    [
        "  Chef@Svenskakyrkan.SE  ",  # trimmas och gemener
        "annan@svenskakyrkan.se, chef@svenskakyrkan.se",  # flera, med mellanslag
        "chef@svenskakyrkan.se,,",  # tomma fält hoppas över
    ],
)
def test_varden_tolkas_forlatande(client, monkeypatch, varde):
    _satt(monkeypatch, varde)
    with database.get_db() as db:
        database._seed_admins(db)
    assert _ar_admin("chef@svenskakyrkan.se") == 1


# En tom variabel får inte röra något - annars hade produktionen, som inte
# sätter den, fått en skrivning vid varje uppstart.
def test_tom_variabel_ror_ingenting(client, monkeypatch):
    _satt(monkeypatch, "")
    with database.get_db() as db:
        db.execute("INSERT INTO users (email, is_admin) VALUES ('vanlig@svenskakyrkan.se', 0)")
        database._seed_admins(db)
    assert _ar_admin("vanlig@svenskakyrkan.se") == 0


# GER bara, tar aldrig ifrån. En felstavad variabel ska inte kunna låsa ute
# alla administratörer på en gång.
def test_borttagen_adress_degraderar_inte(client, monkeypatch):
    _satt(monkeypatch, "chef@svenskakyrkan.se")
    with database.get_db() as db:
        database._seed_admins(db)
    assert _ar_admin("chef@svenskakyrkan.se") == 1

    _satt(monkeypatch, "nagon.annan@svenskakyrkan.se")
    with database.get_db() as db:
        database._seed_admins(db)

    assert _ar_admin("chef@svenskakyrkan.se") == 1, "adminrätten togs ifrån"
    assert _ar_admin("nagon.annan@svenskakyrkan.se") == 1


# Körs vid varje uppstart och måste tåla att köras om.
def test_upprepad_korning_ar_ofarlig(client, monkeypatch):
    _satt(monkeypatch, "chef@svenskakyrkan.se")
    for _ in range(3):
        with database.get_db() as db:
            database._seed_admins(db)
    with database.get_db() as db:
        antal = db.execute(
            "SELECT COUNT(*) FROM users WHERE email='chef@svenskakyrkan.se'"
        ).fetchone()[0]
    assert antal == 1


# Provet som faktiskt betyder något: att rätten går att ANVÄNDA på en route.
def test_adminrutan_slapper_in_den_seedade(client, monkeypatch):
    from app.auth import COOKIE_NAME, create_session_cookie

    _satt(monkeypatch, "chef@svenskakyrkan.se")
    with database.get_db() as db:
        database._seed_admins(db)
        uid = db.execute(
            "SELECT id FROM users WHERE email='chef@svenskakyrkan.se'"
        ).fetchone()[0]
    client.cookies.set(COOKIE_NAME, create_session_cookie(uid))

    svar = client.get("/admin/links")

    assert svar.status_code == 200, "seedad admin nekades av adminspärren"
