"""Miljömarkeringen: gul rad överst när MILJO inte är drift.

Poängen är att den syns på ALLA sidor. Ett prov som bara kollar startsidan
hade missat att en mall glömt ärva base.html, vilket är exakt det fel som gör
att någon råkar tro att staging är produktionen.
"""

import pytest

from app.templating import templates


@pytest.fixture
def i_staging(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "miljo", "staging")


@pytest.fixture
def i_drift(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "miljo", "drift")


# Sidor som en besökare faktiskt landar på, inloggad och inte. Listan hämtas
# inte för hand ur mallkatalogen - den ska spegla vägar, inte filer.
PUBLIKA = ["/", "/bestall", "/login", "/om", "/integritet"]
INLOGGADE = ["/mina-lankar"]


@pytest.mark.parametrize("vag", PUBLIKA)
def test_bannern_syns_pa_publika_sidor(client, i_staging, vag):
    svar = client.get(vag)
    assert svar.status_code == 200, f"{vag} svarade {svar.status_code}"
    assert "miljobanner" in svar.text, f"ingen miljömarkering på {vag}"
    assert "STAGING" in svar.text


@pytest.mark.parametrize("vag", INLOGGADE)
def test_bannern_syns_for_inloggade(client, inloggad_anvandare, i_staging, vag):
    svar = client.get(vag)
    assert svar.status_code == 200
    assert "miljobanner" in svar.text


def test_bannern_syns_i_adminytan(client, admin, i_staging):
    svar = client.get("/admin/links")
    assert svar.status_code == 200
    assert "miljobanner" in svar.text


# Det viktigaste provet: produktionen får INTE ha den. En banner som alltid
# står där slutar man se, och då säger den ingenting när den behövs.
@pytest.mark.parametrize("vag", PUBLIKA)
def test_ingen_banner_i_drift(client, i_drift, vag):
    svar = client.get(vag)
    assert "miljobanner" not in svar.text, f"miljömarkering läckte till drift på {vag}"


def test_drift_ar_forvalet():
    """Glöms MILJO bort vid en deploy ska det bli produktionsutseende, inte
    en banner som säger fel sak."""
    import importlib

    from app import config

    importlib.reload(config)
    assert config.MILJO == "drift"


# Texten ska stå i klartext. Färgen ensam säger ingenting för den som inte
# ser den, och ingenting alls i en skärmdump.
def test_markeringen_bar_text_och_inte_bara_farg(client, i_staging):
    svar = client.get("/")
    assert "Testmiljö" in svar.text


def test_stagingstacken_satter_miljon():
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[1] / "docker-compose.staging.yml").read_text()
    assert "MILJO: staging" in compose
