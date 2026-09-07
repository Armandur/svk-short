"""Promoteringen: att den vägrar rätt saker, och i rätt ordning.

Skillnaden mot en vanlig deploy är kontrollerna, inte kommandona. Proven
riktar sig därför mot dem, och mot ordningen mellan dem - en backup som tas
efter bytet är ingen backup, och en signaturkontroll efter bytet är ingen
grind.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPOROT = Path(__file__).resolve().parents[1]
SKRIPT = REPOROT / "drift/svky-promotera.sh"
KOD = SKRIPT.read_text()

KANDIDAT = "ghcr.io/armandur/svky.se@sha256:" + "c" * 64
PROD = "ghcr.io/armandur/svky.se@sha256:" + "d" * 64


def _attrapp(sokvag: Path, kropp: str) -> None:
    sokvag.write_text(f"#!/usr/bin/env bash\n{kropp}\n")
    sokvag.chmod(sokvag.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def arbetsyta(tmp_path):
    (tmp_path / "drift").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / ".env").write_text(f"SECRET_KEY=prov\nSVKY_IMAGE={PROD}\n")
    _attrapp(tmp_path / "drift/svky-verifiera.sh", "exit 0")
    # docker-attrapp: ps ger ett id, inspect ger kandidaten
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _attrapp(bin_ / "docker", f'''
case "$*" in
  *" ps -q svky"*) echo "container123" ;;
  *inspect*revision*) echo "abc1234" ;;
  *inspect*) echo "{KANDIDAT}" ;;
  *) echo "docker $*" >> "$PWD/docker-anrop.log" ;;
esac
''')
    _attrapp(bin_ / "sqlite3", 'case "$*" in *integrity_check*) echo ok ;; esac')
    _attrapp(bin_ / "curl", "exit 1")  # hälsan svarar aldrig
    return tmp_path


def _kor(arbetsyta: Path, *args) -> subprocess.CompletedProcess:
    miljo = {
        **os.environ,
        "PATH": f"{arbetsyta / 'bin'}:{os.environ['PATH']}",
        "SVKY_ARBETSKATALOG": str(arbetsyta),
        "SVKY_PROD_VANTA": "1",
    }
    return subprocess.run(
        ["bash", str(SKRIPT), *args], capture_output=True, text=True,
        env=miljo, timeout=60,
    )


def test_torrkorning_andrar_ingenting(arbetsyta):
    """Utan --ja ska skriptet bara berätta. Ett verktyg som gör något innan
    man bett om det slutar man köra."""
    r = _kor(arbetsyta)

    assert r.returncode == 0
    assert "Torrkörning" in r.stdout
    assert PROD in (arbetsyta / ".env").read_text(), "bytte version utan --ja"


def test_osignerad_kandidat_stoppar_allt(arbetsyta):
    _attrapp(arbetsyta / "drift/svky-verifiera.sh", 'echo "no signatures found"; exit 10')

    r = _kor(arbetsyta, "--ja")

    assert r.returncode != 0
    assert PROD in (arbetsyta / ".env").read_text(), "bytte trots avvisad signatur"
    assert not (arbetsyta / "backups").exists(), "tog backup innan signaturen var godkänd"


def test_olasbar_backup_stoppar_bytet(arbetsyta):
    """En backup som inte går att läsa är ingen backup. Kontrollen sker före
    bytet, så ingenting ska ha ändrats när den faller."""
    _attrapp(arbetsyta / "bin/sqlite3",
             'case "$*" in *integrity_check*) echo "malformed" ;; esac')

    r = _kor(arbetsyta, "--ja")

    assert r.returncode != 0
    assert "går inte att läsa" in r.stdout
    assert PROD in (arbetsyta / ".env").read_text()


def test_rullar_tillbaka_nar_halsan_uteblir(arbetsyta):
    """Omvänd avvägning mot staging: en trasig produktion får inte stå kvar
    medan någon felsöker."""
    r = _kor(arbetsyta, "--ja")

    assert r.returncode != 0
    assert "Rullar tillbaka" in r.stdout
    assert PROD in (arbetsyta / ".env").read_text(), "lämnade produktionen på den nya"


def test_gor_ingenting_nar_versionen_redan_kors(arbetsyta):
    (arbetsyta / ".env").write_text(f"SECRET_KEY=prov\nSVKY_IMAGE={KANDIDAT}\n")

    r = _kor(arbetsyta, "--ja")

    assert r.returncode == 0
    assert "redan den versionen" in r.stdout


# --- ordningen i koden, inte bara utfallet -------------------------------

def test_signaturen_kontrolleras_fore_backupen_och_bytet():
    verifiera = KOD.index("svky-verifiera.sh")
    backup = KOD.index(".backup")
    byte = KOD.index("docker compose up -d svky")
    assert verifiera < backup < byte, "kontrollerna sker i fel ordning"


def test_foregaende_version_loggas_fore_bytet():
    """Efteråt är den borta ur env-filen, och vägen tillbaka med den."""
    loggning = KOD.index("Föregående version")
    byte = KOD.index("docker compose up -d svky")
    assert loggning < byte


def test_kandidaten_lases_ur_containern_inte_ur_env_filen():
    """En kandidat från i förrgår säger ingenting om det som körs nu."""
    assert "docker inspect --format '{{.Config.Image}}'" in KOD
    assert 'KANDIDAT=$(grep' not in KOD


def test_databasen_nedgraderas_aldrig():
    """En alembic-liknande nedgradering kan kasta data. Rollbacken rör bara
    appen, och det ska synas i koden."""
    assert "up -d svky" in KOD
    for ord_ in ("downgrade", "restore", "DROP TABLE"):
        assert ord_ not in KOD
