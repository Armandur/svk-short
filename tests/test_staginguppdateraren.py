"""Timern som håller staging i fas: att den vägrar rätt saker.

Proven kör det RIKTIGA skriptet i en påhittad arbetskatalog, med attrapper
för de två skript det anropar. Det som mäts är beslutet - deploya eller
avstå - inte docker, som aldrig nås eftersom besluten faller före.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPOROT = Path(__file__).resolve().parents[1]
SKRIPT = REPOROT / "drift/svky-uppdatera-staging.sh"

SIGNERAD = "ghcr.io/armandur/svky.se@sha256:" + "a" * 64
GAMMAL = "ghcr.io/armandur/svky.se@sha256:" + "b" * 64


def _attrapp(sokvag: Path, utdata: str, exitkod: int = 0) -> None:
    sokvag.write_text(f'#!/usr/bin/env bash\necho "{utdata}"\nexit {exitkod}\n')
    sokvag.chmod(sokvag.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def arbetsyta(tmp_path):
    """Katalog som ser ut som utcheckningen, med attrapper i drift/."""
    (tmp_path / "drift").mkdir()
    (tmp_path / ".env.staging").write_text(
        f"SECRET_KEY=prov\nSVKY_IMAGE={GAMMAL}\nBASE_URL=https://exempel\n"
    )
    (tmp_path / "docker-compose.staging.yml").write_text("services: {}\n")
    return tmp_path


def _kor(arbetsyta: Path) -> subprocess.CompletedProcess:
    miljo = {
        **os.environ,
        "SVKY_ARBETSKATALOG": str(arbetsyta),
        "SVKY_STAGING_VANTA": "1",
        "NTFY_URL": "",
        "NTFY_TOPIC": "",
    }
    return subprocess.run(
        ["bash", str(SKRIPT)], capture_output=True, text=True, env=miljo, timeout=60
    )


def test_gor_ingenting_nar_digesten_ar_oforandrad(arbetsyta):
    """Timern fyrar var femte minut. Vore den pratsam i normalfallet skulle
    en rad i journalen sluta betyda något."""
    _attrapp(arbetsyta / "drift/svky-digest.sh", GAMMAL)
    _attrapp(arbetsyta / "drift/svky-verifiera.sh", "ska inte anropas")

    r = _kor(arbetsyta)

    assert r.returncode == 0
    assert r.stdout.strip() == "", f"skrev ut något i normalfallet: {r.stdout!r}"
    assert GAMMAL in (arbetsyta / ".env.staging").read_text()


# Provet som bär hela konstruktionen. Utan det är signeringen en ritual.
def test_osignerad_image_avvisas_och_rors_inte(arbetsyta):
    _attrapp(arbetsyta / "drift/svky-digest.sh", SIGNERAD)
    _attrapp(arbetsyta / "drift/svky-verifiera.sh", "no signatures found", exitkod=10)

    r = _kor(arbetsyta)

    assert r.returncode != 0, "släppte igenom en osignerad image"
    env = (arbetsyta / ".env.staging").read_text()
    assert GAMMAL in env, "bytte version trots avvisad signatur"
    assert SIGNERAD not in env
    assert "AVVISAD" in r.stdout
    # Meddelandet får inte påstå att signaturen saknas. Ett verktyg som inte
    # kunde köra ger samma röda svar, och första gången det inträffade var
    # orsaken en skrivskyddad hemkatalog - inte en osignerad image.
    assert "saknar giltig signatur" not in r.stdout


def test_verifieringen_sker_fore_bytet(arbetsyta):
    """Ordningen är det som skyddar: verifiera, sedan skriv.

    Attrappen skriver sitt fynd till en FIL, inte till stdout. Skriptet
    skickar verifierarens utdata till /dev/null, så ett prov som läste
    stdout hade inte kunnat se sin egen signal - och gått igenom även när
    ordningen var omvänd.
    """
    fynd = arbetsyta / "fynd.txt"
    (arbetsyta / "drift/svky-verifiera.sh").write_text(
        "#!/usr/bin/env bash\n"
        f'if grep -q "{SIGNERAD}" "$SVKY_ARBETSKATALOG/.env.staging"; then\n'
        f'  echo "env-filen var redan andrad" > "{fynd}"\n'
        "fi\n"
        "exit 10\n"
    )
    (arbetsyta / "drift/svky-verifiera.sh").chmod(0o755)
    _attrapp(arbetsyta / "drift/svky-digest.sh", SIGNERAD)

    r = _kor(arbetsyta)

    assert not fynd.exists(), "env-filen skrevs innan signaturen var verifierad"
    assert r.returncode != 0


def test_skriptet_ror_aldrig_produktionen():
    """Produktionens compose-fil ligger i samma katalog. Skriptet ska inte
    kunna nämna den ens av misstag."""
    kod = SKRIPT.read_text()
    assert "docker-compose.yml" not in kod
    for rad in kod.splitlines():
        if "docker compose" in rad:
            assert "COMPOSE_ARGS" in rad, f"compose-anrop utan stagingargumenten: {rad}"


def test_vantloopen_ar_tyst():
    """Loopen frågar en gång i sekunden medan appen startar. Med -S hamnar
    varje misslyckat försök i journalen och en lyckad deploy ser ut som ett
    haveri - vilket lär en att läsa förbi röda rader."""
    for rad in SKRIPT.read_text().splitlines():
        if "curl" in rad and "HALSA" in rad:
            assert "-fsS" not in rad, f"väntloopen skriver ut varje försök: {rad}"


def test_ett_jobb_at_gangen():
    """Timern kan fyra medan föregående körning väntar på health."""
    assert "flock" in SKRIPT.read_text()


def test_timern_och_enheten_finns():
    for namn in ("svky-staging-uppdatera.service", "svky-staging-uppdatera.timer"):
        assert (REPOROT / "drift/systemd" / namn).exists(), f"{namn} saknas"


def test_enheten_pekar_pa_skriptet():
    enhet = (REPOROT / "drift/systemd/svky-staging-uppdatera.service").read_text()
    assert "svky-uppdatera-staging.sh" in enhet
    assert "Type=oneshot" in enhet


def test_enheten_ger_cosign_en_skrivbar_home():
    """cosign cachar Sigstores TUF-rot under $HOME/.sigstore. Med
    ProtectHome=read-only och HOME i hemkatalogen faller den på read-only
    file system, och felet ser ut som en ogiltig signatur."""
    enhet = (REPOROT / "drift/systemd/svky-staging-uppdatera.service").read_text()
    assert "StateDirectory=" in enhet
    assert "Environment=HOME=/var/lib/" in enhet


def test_verifierarens_utdata_nar_journalen():
    """Utan den står bara att något avvisades, och orsaken är borta."""
    kod = SKRIPT.read_text()
    assert "VERIFIERING=$(drift/svky-verifiera.sh" in kod
    assert 'printf' in kod and 'VERIFIERING' in kod


def test_larmen_gar_till_den_delade_instansen_med_policyns_topics():
    """Egna topicnamn glider isär från policyn, och en lokal ntfy på samma
    server tystnar precis när den behövs."""
    kod = SKRIPT.read_text()
    assert "svc_ops" in kod and "svc_alert" in kod
    assert "NTFY_TOPIC" not in kod, "egen topic i stället för policyns"


def test_ingen_notis_vid_lyckad_uppdatering():
    """Sker vid varje push till main. En kanal som mest bär bra nyheter
    slutar man öppna."""
    kod = SKRIPT.read_text()
    lyckad = kod.index('logga "Staging kör $NY"')
    fram_till_exit = kod[lyckad:kod.index("exit 0", lyckad)]
    assert "notis " not in fram_till_exit
