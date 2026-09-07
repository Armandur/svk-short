"""Knapparna som hämtar och rullar ut driftkoden.

Två knappar, inte en. Skälet är mekaniskt: hämtningens enhet är härdad och
får inte skriva i /usr/local/bin eller /etc, och bara ETT jobb åt gången kan
vara aktivt - kedjade jobb är inget alternativ. Att de är åtskilda ger
dessutom att en fallerad utrullning inte kan dölja att koden hämtades.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPOROT = Path(__file__).resolve().parents[1]
HAMTA = REPOROT / "drift/svky-hamta-driftkod.sh"
RULLA = REPOROT / "drift/svky-rulla-ut-drift.sh"


def _kod(skript: Path) -> str:
    """Skriptet UTAN kommentarer.

    Skripten förklarar i kommentarer varför de inte använder reset --hard och
    cp. Ett prov som matchar på texten faller då på just de raderna - och att
    ta bort förklaringen för att blidka provet vore precis fel väg.
    """
    rader = []
    for rad in skript.read_text().splitlines():
        skalad = rad.split("#", 1)[0] if not rad.lstrip().startswith("#") else ""
        rader.append(skalad)
    return "\n".join(rader)


def _kor(skript: Path, arbetsyta: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(skript)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "SVKY_ARBETSKATALOG": str(arbetsyta)},
    )


@pytest.fixture
def repo(tmp_path):
    """Ett litet git-repo med en 'origin' att hämta från."""
    fjarr, lokal = tmp_path / "fjarr", tmp_path / "lokal"
    fjarr.mkdir()
    k = dict(capture_output=True, text=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(fjarr)], **k)
    subprocess.run(["git", "clone", "-q", str(fjarr), str(lokal)], **k)
    for namn, varde in (("user.email", "p@rov.se"), ("user.name", "Prov")):
        subprocess.run(["git", "-C", str(lokal), "config", namn, varde], **k)
    (lokal / "fil.txt").write_text("ett\n")
    subprocess.run(["git", "-C", str(lokal), "add", "-A"], **k)
    subprocess.run(["git", "-C", str(lokal), "commit", "-qm", "första"], **k)
    subprocess.run(["git", "-C", str(lokal), "push", "-q", "origin", "main"], **k)
    return lokal


def _ny_commit_pa_origin(lokal: Path, text: str = "två\n") -> None:
    k = dict(capture_output=True, text=True)
    annan = lokal.parent / "annan"
    subprocess.run(["git", "clone", "-q", str(lokal.parent / "fjarr"), str(annan)], **k)
    for namn, varde in (("user.email", "p@rov.se"), ("user.name", "Prov")):
        subprocess.run(["git", "-C", str(annan), "config", namn, varde], **k)
    (annan / "fil.txt").write_text(text)
    subprocess.run(["git", "-C", str(annan), "commit", "-qam", "andra commiten"], **k)
    subprocess.run(["git", "-C", str(annan), "push", "-q", "origin", "main"], **k)


def test_hamtar_nar_det_finns_nytt(repo):
    _ny_commit_pa_origin(repo)

    r = _kor(HAMTA, repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "Hämtade" in r.stdout
    assert (repo / "fil.txt").read_text() == "två\n"


def test_sager_till_nar_allt_ar_i_fas(repo):
    r = _kor(HAMTA, repo)
    assert r.returncode == 0
    assert "Redan i fas" in r.stdout


# Provet som bär hela knappen. En utcheckning man inte kan lita på är värre
# än en som ligger efter.
def test_vagrar_och_kastar_inte_lokala_commitar(repo):
    _ny_commit_pa_origin(repo)
    k = dict(capture_output=True, text=True)
    (repo / "lokalt.txt").write_text("arbete pa servern\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], **k)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "lokal commit"], **k)
    fore = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          **k).stdout.strip()

    r = _kor(HAMTA, repo)

    assert r.returncode != 0
    assert "VÄGRAR" in r.stdout
    assert "divergerat" in r.stdout
    efter = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           **k).stdout.strip()
    assert fore == efter, "flyttade HEAD trots divergens"
    assert (repo / "lokalt.txt").exists(), "kastade det lokala arbetet"


def test_hamtningen_rullar_inte_ut():
    """Egen knapp, av mekaniska skäl. Hämtningens enhet är härdad och kan
    inte skriva i /usr/local/bin eller /etc - gör skriptet det ändå faller
    jobbet på filsystemet mitt i, och en halvkörd installation är sämre än
    ingen."""
    # Mät vilka KOMMANDON skriptet kör, inte vilka ord det nämner. Raden
    # som säger "kör Rulla ut drift/ för att få kopiorna i fas" nämner
    # sökvägen utan att röra den, och ett prov som föll på den hade drivit
    # fram en sämre logg.
    kod = _kod(HAMTA)
    for kommando in ("install ", "cp ", "systemctl", "daemon-reload", "sudo"):
        assert kommando not in kod, f"hämtningen kör {kommando.strip()}"


def test_aldrig_reset_hard():
    """Finns lokala commitar ska kommandot vägra, inte kasta dem tyst."""
    for skript in (HAMTA, RULLA):
        assert "reset --hard" not in _kod(skript), skript.name
        assert "checkout --force" not in _kod(skript), skript.name


def test_utrullningen_har_filnamnen_i_koden():
    """Ett steg som läser vad det ska installera ur någon annans fil är en
    godtycklig installationsprimitiv."""
    kod = _kod(RULLA)
    assert "ENHETER=" in kod
    assert "svky-begaran-rulla-ut.service" in kod
    assert "install -m 644" in kod
    # cp bevarar källans rättigheter, och en umask på 077 ger rotägda
    # 600-filer som samlaren inte kan läsa.
    assert "cp " not in kod


@pytest.mark.parametrize("op", ["hamta-driftkod", "rulla-ut"])
def test_enheterna_finns_och_stader_markoren(op):
    p = REPOROT / f"drift/systemd/svky-begaran-{op}.path"
    s = REPOROT / f"drift/systemd/svky-begaran-{op}.service"
    assert f"PathExists=/var/lib/svky/begaran/{op}" in p.read_text()
    rader = [r for r in s.read_text().splitlines() if r.startswith("ExecStart")]
    # +-prefixet kör steget med fulla rättigheter oavsett User=. Markören ägs
    # av svky-ops i en katalog svky-ops äger, så utan det får en enhet som kör
    # som rasmus Permission denied - och fastnar i rm, med markören kvar och
    # knappen tyst död tills någon raderar filen för hand.
    assert rader[0].startswith("ExecStartPre=+/bin/rm"), \
        f"{op}: markören städas inte först, eller utan +"
    assert "StartLimitIntervalSec=0" in s.read_text()


def test_hamtningen_kor_som_agaren_inte_root():
    """Klonen hämtas över SSH med ägarens nyckel. Som root faller varje fetch
    på Permission denied (publickey), vilket ser ut som ett nätfel."""
    enhet = (REPOROT / "drift/systemd/svky-begaran-hamta-driftkod.service").read_text()
    assert "User=rasmus" in enhet
    assert "ProtectHome=read-only" in enhet, "yes döljer nyckeln"


def test_bada_knapparna_finns_pa_ytan():
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    for op in ("hamta-driftkod", "rulla-ut"):
        assert f'action="/begar/{op}"' in kod
        assert f'"{op}":' in kod, f"{op} saknas i OPERATIONER"


def test_skripten_ar_korbara():
    for s in (HAMTA, RULLA):
        assert s.stat().st_mode & stat.S_IXUSR, f"{s.name} är inte körbar"


def test_utrullningen_kor_git_med_safe_directory():
    """Jobbet kör som ROOT i en utcheckning rasmus äger. Utan safe.directory
    vägrar git med 'detected dubious ownership' och exit 128, och med set -e
    dog skriptet EFTER att filerna installerats men FÖRE omstarterna - en
    halvkörd utrullning. Hände i drift 2026-09-07."""
    kod = _kod(RULLA)
    assert 'git -c safe.directory="$ARBETSKATALOG"' in kod
    # Inga nakna git-anrop kvar
    for rad in kod.splitlines():
        assert not rad.strip().startswith("git "), f"naket git-anrop: {rad.strip()}"
        assert "$(git " not in rad, f"naket git-anrop: {rad.strip()}"


def test_utrullat_skrivs_fore_omstarterna():
    """En omstart som faller får inte ta med sig uppgiften om vad som hann
    rullas ut."""
    kod = _kod(RULLA)
    assert kod.index("/var/lib/svky/utrullat") < kod.index("systemctl restart")


def test_driftytan_startas_om_utan_att_blockera():
    """Ytan är sidan som visar att jobbet kör. En synkron omstart klipper
    anslutningen mitt i medan skriptet väntar på att den kommer upp."""
    kod = _kod(RULLA)
    assert "systemctl restart --no-block svky-driftyta.service" in kod
