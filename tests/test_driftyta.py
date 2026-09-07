"""Driftytan: att den säger vad den inte vet.

En panel som ser tom ut när något är trasigt säger samma sak som att allt är
bra, och det är fel svar på rätt fråga. Proven riktar sig mot de lägena, inte
mot det friska - det friska är lätt att få rätt.
"""

import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPOROT = Path(__file__).resolve().parents[1]
SAMLARE = (REPOROT / "drift/svky-samla-lage.sh").read_text()


def _ladda_yta(lagesfil: Path):
    """Importerar driftytan med lägesfilen pekad på en provfil."""
    sys.modules.pop("svky_driftyta", None)
    spec = importlib.util.spec_from_file_location(
        "svky_driftyta", REPOROT / "drift/svky-driftyta.py"
    )
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    modul.LAGESFIL = lagesfil
    return modul


def _skriv(tmp_path: Path, **overskriv) -> Path:
    nu = datetime.datetime.now(datetime.UTC).isoformat()
    lage = {
        "hamtad": nu,
        "produktion": {"image": "ghcr.io/x@sha256:" + "a" * 64, "commit": "a" * 40,
                       "status": "running"},
        "staging": {"image": "ghcr.io/x@sha256:" + "b" * 64, "commit": "b" * 40,
                    "status": "running"},
        "senaste_bygge": "ghcr.io/x@sha256:" + "b" * 64,
        "uppdaterare": {"resultat": "success", "avslutad": "nyss", "exitkod": "0",
                        "timer": "active"},
        "uppetidssond": "active",
        "ci": {"utfall": "success", "sha": "abc12345", "url": "https://x", "tid": nu},
    }
    lage.update(overskriv)
    fil = tmp_path / "lage.json"
    fil.write_text(json.dumps(lage))
    return fil


def test_saknad_lagesfil_ger_besked_inte_tom_sida(tmp_path):
    yta = _ladda_yta(tmp_path / "finns-inte.json")
    html = yta.rendera()
    assert "finns inte" in html
    assert "Har samlaren kört?" in html


def test_gammalt_lage_kallas_gammalt(tmp_path):
    gammal = (datetime.datetime.now(datetime.UTC)
              - datetime.timedelta(minutes=30)).isoformat()
    yta = _ladda_yta(_skriv(tmp_path, hamtad=gammal))
    html = yta.rendera()
    assert "minuter gammalt" in html, "en frusen fil presenterades som färsk"


# Det viktigaste provet på hela sidan. Ett bygge som FALLER når aldrig servern,
# så utan CI-raden betyder tystnad framgång.
def test_okant_ci_sags_uttryckligen_vara_okant(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path, ci=None))
    html = yta.rendera()
    assert "kunde inte hämtas" in html
    assert "INTE samma sak" in html


def test_okand_miljo_markeras_som_okand(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path, staging={"image": None, "commit": None,
                                               "status": None}))
    html = yta.rendera()
    assert "okänt" in html
    assert "Kan inte jämföra" in html


def test_skillnad_mellan_miljoer_syns(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path))
    assert "ligger före produktionen" in yta.rendera()

    samma = "ghcr.io/x@sha256:" + "c" * 64
    yta2 = _ladda_yta(_skriv(tmp_path,
                             produktion={"image": samma, "commit": "c" * 40,
                                         "status": "running"},
                             staging={"image": samma, "commit": "c" * 40,
                                      "status": "running"},
                             senaste_bygge=samma))
    assert "samma version som staging" in yta2.rendera()


@pytest.mark.parametrize("falt", ["produktion", "staging", "ci", "uppetidssond"])
def test_ytan_kraschar_inte_pa_saknade_falt(tmp_path, falt):
    """Ett halvskrivet läge ska ge en sida med luckor, inte ett 500-fel."""
    fil = tmp_path / "lage.json"
    lage = json.loads(_skriv(tmp_path).read_text())
    del lage[falt]
    fil.write_text(json.dumps(lage))
    assert "svky.se drift" in _ladda_yta(fil).rendera()


# --- ytans rättigheter ---------------------------------------------------

def test_ytan_kan_inte_kora_nagot():
    """Den som når dockersocketen kan allt med varje container. En webbyta
    ska inte kunna det - därför läser den bara filen samlaren skriver.

    Provet läser SYNTAXTRÄDET, inte texten. Orden docker och systemctl står i
    filens kommentarer, och ett prov som matchade på text hade fallit på just
    de raderna som förklarar varför de inte används.
    """
    import ast

    trad = ast.parse((REPOROT / "drift/svky-driftyta.py").read_text())

    importerade = set()
    for nod in ast.walk(trad):
        if isinstance(nod, ast.Import):
            importerade.update(a.name.split(".")[0] for a in nod.names)
        elif isinstance(nod, ast.ImportFrom) and nod.module:
            importerade.add(nod.module.split(".")[0])
    for farlig in ("subprocess", "shutil", "pty", "socketserver"):
        assert farlig not in importerade, f"ytan importerar {farlig}"

    anrop = {
        nod.func.attr if isinstance(nod.func, ast.Attribute) else
        getattr(nod.func, "id", "")
        for nod in ast.walk(trad) if isinstance(nod, ast.Call)
    }
    for farligt in ("system", "popen", "run", "check_output", "exec", "eval"):
        assert farligt not in anrop, f"ytan anropar {farligt}()"


def test_ytan_binder_bara_loopback():
    """Vägen in är tailscale serve. En öppen port vore en väg förbi den."""
    assert '("127.0.0.1", PORT)' in (REPOROT / "drift/svky-driftyta.py").read_text()


def test_enheten_ger_ytan_egen_anvandare_utan_extra_grupper():
    """Läs direktiven, inte kommentarerna. Ordet docker står i enhetens
    kommentar om varför den INTE får dockeråtkomst."""
    direktiv = [r.strip() for r in
                (REPOROT / "drift/systemd/svky-driftyta.service").read_text().splitlines()
                if r.strip() and not r.strip().startswith("#")]

    assert "DynamicUser=yes" in direktiv
    assert any(r.startswith("ProtectSystem=strict") for r in direktiv)
    for rad in direktiv:
        assert not rad.startswith("SupplementaryGroups"), f"ytan fick grupper: {rad}"
        if rad.startswith("ExecStart"):
            assert "docker" not in rad, rad


def test_samlaren_skriver_atomart_och_validerar():
    """En halvskriven lägesfil hade fått ytan att säga fel saker."""
    assert "mktemp" in SAMLARE and 'mv "$TMP"' in SAMLARE
    assert "json.load" in SAMLARE, "skriver utan att kontrollera att det är JSON"


def test_ytan_kor_inte_kod_ur_hemkatalogen():
    """ProtectHome=yes döljer /home, så en ExecStart därifrån faller med
    exit 2 - python kan inte öppna filen, och felet ser ut som ett
    syntaxfel. Dessutom: en tjänst som kör kod ur en användarskrivbar
    katalog kan bytas ut av den som äger katalogen."""
    direktiv = [r.strip() for r in
                (REPOROT / "drift/systemd/svky-driftyta.service").read_text().splitlines()
                if r.strip() and not r.strip().startswith("#")]
    hemskydd = [r for r in direktiv if r.startswith("ProtectHome=")]
    exec_rader = [r for r in direktiv if r.startswith("ExecStart=")]
    assert exec_rader, "ingen ExecStart"
    if hemskydd and hemskydd[0] == "ProtectHome=yes":
        for rad in exec_rader + [r for r in direktiv if r.startswith("ReadOnlyPaths=")]:
            assert "/home" not in rad, f"pekar in i /home trots ProtectHome=yes: {rad}"
