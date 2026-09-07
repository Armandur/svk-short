"""Att stagingens gränser står kvar i konfigurationen.

Proven läser filerna som text, precis som test_proxykonfiguration.py. De
bevisar inte att stacken fungerar - de fångar att en rad försvinner obemärkt,
och varje rad här är en gräns någon annars kan ta bort utan att märka det.
"""

import os
import re
from pathlib import Path

import pytest

REPOROT = Path(__file__).resolve().parents[1]
STAGING = (REPOROT / "docker-compose.staging.yml").read_text()
PRODUKTION = (REPOROT / "docker-compose.yml").read_text()


# Utan :? startar stacken tyst på :latest, och då vet ingen vilken version
# som provades.
def test_staging_vagrar_starta_utan_uttrycklig_digest():
    assert "${SVKY_IMAGE:?" in STAGING


# Utan 127.0.0.1 binds porten på alla gränssnitt, tailnet OCH publikt, och
# tailscale serve blir en gräns man kan gå runt.
@pytest.mark.parametrize("tjanst", ["8000", "8025"])
def test_staging_publicerar_bara_pa_loopback(tjanst):
    rader = [r.strip() for r in STAGING.splitlines() if f":{tjanst}\"" in r]
    assert rader, f"hittade ingen portrad för {tjanst}"
    for rad in rader:
        assert rad.lstrip("- ").startswith('"127.0.0.1:'), rad


# Utan detta kan staging nå Lettermint och skicka riktig post till anställda.
def test_staging_skickar_posten_till_mailpit():
    assert "SMTP_HOST: mailpit" in STAGING
    assert "SMTP_SECURITY: none" in STAGING
    assert 'SMTP_USER: ""' in STAGING
    assert 'SMTP_PASS: ""' in STAGING


# En env-fil vinner över compose-filens environment-block, så stagingens
# SMTP-inställningar får inte kunna sättas där.
def test_env_mallen_varnar_for_smtp_i_env_filen():
    mall = (REPOROT / ".env.staging.example").read_text()
    assert "SMTP_" in mall
    assert "compose" in mall.lower()
    for nyckel in ("SMTP_HOST=", "SMTP_PORT=", "SMTP_USER=", "SMTP_PASS="):
        assert nyckel not in mall, f"{nyckel} ska inte stå i mallen"


# En stagingdatabas eller en hemlighetsfil som råkar committas är svår att
# ta tillbaka.
@pytest.mark.parametrize("monster", [".env.staging", "data-staging/"])
def test_stagingens_filer_ar_ignorerade(monster):
    assert monster in (REPOROT / ".gitignore").read_text()


# Produktionen ska gå att pinna, men får inte sluta fungera för den som inte
# sätter något.
def test_produktionen_kan_pinnas_men_har_kvar_sitt_forval():
    assert re.search(r"image: \$\{SVKY_IMAGE:-[^}]+:latest\}", PRODUKTION)


class _FalskSMTP:
    """Registrerar vad app.mail._send faktiskt anropar."""

    def __init__(self, *args, **kwargs):
        self.anrop: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.anrop.append("starttls")

    def login(self, user, pwd):
        self.anrop.append("login")

    def sendmail(self, *args):
        self.anrop.append("sendmail")


@pytest.mark.parametrize(
    ("sakerhet", "anvandare", "vantat"),
    [
        # Produktionen: oförändrad.
        ("starttls", "user", ["starttls", "login", "sendmail"]),
        # Staging mot Mailpit: varken TLS eller inloggning.
        ("none", "", ["sendmail"]),
        # Halvvägs: en lokal relä som talar TLS men inte kräver konto.
        ("starttls", "", ["starttls", "sendmail"]),
    ],
)
def test_smtp_sakerheten_styrs_av_miljon(monkeypatch, sakerhet, anvandare, vantat):
    import app.mail as mail

    monkeypatch.setattr(mail, "SMTP_SECURITY", sakerhet)
    monkeypatch.setattr(mail, "SMTP_USER", anvandare)
    falsk = _FalskSMTP()
    monkeypatch.setattr(mail.smtplib, "SMTP", lambda *a, **k: falsk)

    mail._send("nagon@svenskakyrkan.se", "Prov", "<p>Prov</p>")

    assert falsk.anrop == vantat


def test_ingen_riktig_smtp_vard_i_stagingfilerna():
    """En kvarglömd riktig SMTP-värd i stagingkonfigurationen är hela felet.

    Provet letar efter VÄRDNAMNET, inte efter ordet Lettermint. Mallen nämner
    leverantören i en varning om att inte sätta SMTP_ i env-filen, och ett
    prov som föll på den texten hade lärt oss att ta bort varningen.
    """
    mall = (REPOROT / ".env.staging.example").read_text()
    for text in (STAGING, mall):
        assert "lettermint.net" not in text.lower()
