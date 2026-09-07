"""Driftytan: att den säger vad den inte vet.

En panel som ser tom ut när något är trasigt säger samma sak som att allt är
bra, och det är fel svar på rätt fråga. Proven riktar sig mot de lägena, inte
mot det friska - det friska är lätt att få rätt.
"""

import datetime
import importlib.util
import json
import re
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


def _css_varde(kod: str, selektor: str, egenskap: str) -> str:
    regel = re.search(rf"{re.escape(selektor)}\s*\{{([^}}]+)\}}", kod)
    assert regel, f"CSS-regeln {selektor} saknas"
    varde = re.search(rf"{egenskap}\s*:\s*([^;]+)", regel.group(1))
    assert varde, f"{egenskap} saknas i {selektor}"
    return varde.group(1).strip()


def _kontrast(farg: str, bakgrund: str) -> float:
    def luminans(hexvarde: str) -> float:
        hexvarde = hexvarde.lstrip("#")
        if len(hexvarde) == 3:
            hexvarde = "".join(tecken * 2 for tecken in hexvarde)
        kanaler = [int(hexvarde[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linjara = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                   for v in kanaler]
        return 0.2126 * linjara[0] + 0.7152 * linjara[1] + 0.0722 * linjara[2]

    ljus, mork = sorted((luminans(farg), luminans(bakgrund)), reverse=True)
    return (ljus + 0.05) / (mork + 0.05)


# Utan live-regionen ändras aria-live från "polite" till ett saknat attribut.
def test_statusraden_meddelar_uppdateringar_till_hjalpmedel(tmp_path):
    html = _ladda_yta(_skriv(tmp_path)).sida()
    assert '<p class="status" id="status" aria-live="polite">' in html


# Vid felet saknas texten om nästa steg trots att senaste_bygge skiljer sig.
def test_nyare_bygge_sager_vad_som_hander_harnast(tmp_path):
    yta = _ladda_yta(_skriv(
        tmp_path, senaste_bygge="ghcr.io/x@sha256:" + "c" * 64
    ))
    html = yta.fragment()
    assert "Ett nyare bygge finns än det staging kör" in html
    assert "Staginguppdateraren hämtar det inom fem minuter" in html


# Utan regeln blir flex-basis "auto", vilket kan bryta mellan label och knapp.
def test_promoteringslabeln_tar_en_medveten_egen_rad():
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert _css_varde(kod, "form.farlig label", "flex-basis") == "100%"


# Vid kontrastfelet sjunker kvoterna under 4,5 mot vit respektive rosa bakgrund.
def test_saknat_varde_har_tillracklig_kontrast_pa_bada_bakgrunderna():
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    vanlig = _css_varde(kod, ".saknas", "color")
    kort = _css_varde(kod, ".kort", "background")
    i_felpiller = _css_varde(kod, ".pill .saknas", "color")
    felpiller = _css_varde(kod, ".pill.fel", "background")

    assert _kontrast(vanlig, kort) >= 4.5
    assert _kontrast(i_felpiller, felpiller) >= 4.5


# Utan den kompakta varianten återgår klassnamnet och rubrikstorleken till statuskortets.
def test_automatik_ar_kompakt_men_visar_alla_uppgifter(tmp_path):
    html = _ladda_yta(_skriv(tmp_path)).fragment()
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    statusrubrik = float(_css_varde(kod, ".kort h2", "font-size").removesuffix("rem"))
    automatikrubrik = float(
        _css_varde(kod, ".automatik h2", "font-size").removesuffix("rem")
    )

    assert '<section class="kort automatik"' in html
    assert automatikrubrik < statusrubrik
    assert _css_varde(kod, ".automatik", "box-shadow") == "none"
    for uppgift in ("Staginguppdateraren", "Uppetidssond", "Senaste körningen"):
        assert uppgift in html


def test_saknad_lagesfil_ger_besked_inte_tom_sida(tmp_path):
    yta = _ladda_yta(tmp_path / "finns-inte.json")
    html = yta.sida()
    assert "finns inte" in html
    assert "Har samlaren kört?" in html


def test_gammalt_lage_kallas_gammalt(tmp_path):
    gammal = (datetime.datetime.now(datetime.UTC)
              - datetime.timedelta(minutes=30)).isoformat()
    yta = _ladda_yta(_skriv(tmp_path, hamtad=gammal))
    html = yta.sida()
    assert "minuter gammalt" in html, "en frusen fil presenterades som färsk"


# Det viktigaste provet på hela sidan. Ett bygge som FALLER når aldrig servern,
# så utan CI-raden betyder tystnad framgång.
def test_okant_ci_sags_uttryckligen_vara_okant(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path, ci=None))
    html = yta.sida()
    assert "kunde inte hämtas" in html
    assert "INTE samma sak" in html


def test_okand_miljo_markeras_som_okand(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path, staging={"image": None, "commit": None,
                                               "status": None}))
    html = yta.sida()
    assert "okänt" in html
    assert "Kan inte jämföra" in html


def test_skillnad_mellan_miljoer_syns(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path))
    html = yta.sida()
    assert "ligger före produktionen" in html
    # Hänvisa till knappen som står på samma sida, inte till kommandot.
    # Ett besked som pekar förbi åtgärden lär en att sidan är gammal.
    assert "knappen under Åtgärder" in html
    assert "svky-promotera.sh --ja</code>.</p>" not in html

    samma = "ghcr.io/x@sha256:" + "c" * 64
    yta2 = _ladda_yta(_skriv(tmp_path,
                             produktion={"image": samma, "commit": "c" * 40,
                                         "status": "running"},
                             staging={"image": samma, "commit": "c" * 40,
                                      "status": "running"},
                             senaste_bygge=samma))
    assert "samma version som staging" in yta2.sida()


@pytest.mark.parametrize("falt", ["produktion", "staging", "ci", "uppetidssond"])
def test_ytan_kraschar_inte_pa_saknade_falt(tmp_path, falt):
    """Ett halvskrivet läge ska ge en sida med luckor, inte ett 500-fel."""
    fil = tmp_path / "lage.json"
    lage = json.loads(_skriv(tmp_path).read_text())
    del lage[falt]
    fil.write_text(json.dumps(lage))
    assert "svky.se drift" in _ladda_yta(fil).sida()


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

    # EGEN användare, inte rasmus och inte root. DynamicUser vore snävare
    # men går inte: begärankatalogen måste ägas av någon, och en efemär uid
    # kan man inte sätta ägare till.
    assert "User=svky-ops" in direktiv
    assert not any(r in ("User=root", "User=rasmus") for r in direktiv)
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


def test_pagaende_korning_sags_inte_vara_okand(tmp_path):
    """ExecMainExitTimestamp töms medan tjänsten kör, och samlaren har samma
    period som uppdateraren - de krockar regelbundet. Utan det här sa sidan
    okänt fast ingenting var okänt, och en signal som ropar varg slutar
    betyda något."""
    yta = _ladda_yta(_skriv(tmp_path, uppdaterare={
        "resultat": "success", "avslutad": None, "exitkod": "0",
        "timer": "active", "aktiv": "activating"}))
    html = yta.sida()
    assert "kör just nu" in html
    assert "senast <span" not in html, "visade en lucka i stället för läget"


def test_avslutad_korning_visar_tidpunkten(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path, uppdaterare={
        "resultat": "success", "avslutad": "Mon 2026-09-07 18:44:03 UTC",
        "exitkod": "0", "timer": "active", "aktiv": "inactive"}))
    html = yta.sida()
    assert "18:44:03" in html
    assert "kör just nu" not in html


def test_samlaren_faller_tillbaka_pa_inactive_enter():
    """ExecMainExitTimestamp ensam räcker inte - den töms under körning."""
    assert "InactiveEnterTimestamp" in SAMLARE


# --- knapparna -----------------------------------------------------------

def test_bara_kanda_operationer_kan_begaras(tmp_path, monkeypatch):
    """Ytan får inte kunna peka ut något. Operationen ÄR nyckeln."""
    yta = _ladda_yta(_skriv(tmp_path))
    monkeypatch.setattr(yta, "BEGARAN", tmp_path / "begaran")

    assert yta.begar("uppdatera") is None
    assert (tmp_path / "begaran/uppdatera").exists()

    for pahittat in ("../../etc/passwd", "starta-allt", "", "uppdatera/x"):
        assert yta.begar(pahittat) == "Okänd operation."
    assert sorted(f.name for f in (tmp_path / "begaran").iterdir()) == ["uppdatera"]


def test_dubbel_begaran_koar_inte(tmp_path, monkeypatch):
    """PathExists fyrar inte om medan filen ligger kvar. Att säga det är
    ärligare än att låtsas att ett andra tryck gjorde något."""
    yta = _ladda_yta(_skriv(tmp_path))
    monkeypatch.setattr(yta, "BEGARAN", tmp_path / "begaran")
    assert yta.begar("uppdatera") is None
    assert "redan" in (yta.begar("uppdatera") or "")


def test_get_utlöser_aldrig_nagot():
    """Samma skäl som e-postlänkarnas skannerskydd: en förhämtning eller en
    inklistrad länk får inte kunna starta ett jobb i produktionen."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    do_get = kod[kod.index("def do_GET"):kod.index("def do_POST")]
    assert "begar(" not in do_get


def test_promotering_kraver_bekraftelse_i_koden():
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert 'falt.get("bekrafta") != ["ja"]' in kod


def test_markoren_tas_bort_fore_jobbet_startar():
    """En kvarglömd markör gör knappen död för all framtid: PathExists fyrar
    inte om medan filen ligger kvar."""
    for op in ("uppdatera", "promotera"):
        enhet = (REPOROT / f"drift/systemd/svky-begaran-{op}.service").read_text()
        rader = [r for r in enhet.splitlines() if r.startswith("ExecStart")]
        forsta = rader[0]
        assert forsta.startswith("ExecStartPre=+"), \
            f"{op}: första steget kör inte med +, och får då Permission denied"
        assert f"rm -f /var/lib/svky/begaran/{op}" in forsta, \
            f"{op}: begäranmarkören tas inte bort i första steget"


@pytest.mark.parametrize("op", ["uppdatera", "promotera", "hamta-driftkod", "rulla-ut"])
def test_kormarkoren_satts_och_stadas(op):
    """Utan körmarkör kan sidan inte se att jobbet PÅGÅR.

    Begäranmarkören försvinner innan jobbet börjat, så en promotering som tog
    minuter såg avslutad ut efter en sekund och fick beskedet att
    produktionens version var oförändrad - mitt under bytet.
    """
    enhet = (REPOROT / f"drift/systemd/svky-begaran-{op}.service").read_text()
    starta = [r for r in enhet.splitlines() if r.startswith("ExecStartPre=")]
    stoppa = [r for r in enhet.splitlines() if r.startswith("ExecStopPost=")]

    assert any(f"/var/lib/svky/korande/{op}" in r for r in starta), \
        f"{op}: ingen körmarkör läggs när jobbet startar"
    assert any("-m 644" in r for r in starta), \
        f"{op}: markören får inte uttryckligt läsläge - ytan kör som annan användare"
    assert stoppa, f"{op}: ingen ExecStopPost, markören blir liggande vid krasch"
    assert any(f"rm -f /var/lib/svky/korande/{op}" in r for r in stoppa), \
        f"{op}: körmarkören städas inte när jobbet slutar"
    # Utan omläsning jämför sidan mot en lägesfil som kan vara en minut
    # gammal, och ger samma falska "oförändrad" - bara en minut senare.
    assert any("svky-samla-lage.service" in r for r in stoppa), \
        f"{op}: samlaren startas inte om, så beskedet räknas på gammalt läge"


def test_path_enheterna_pekar_pa_varsin_fil():
    for op in ("uppdatera", "promotera"):
        p = (REPOROT / f"drift/systemd/svky-begaran-{op}.path").read_text()
        assert f"PathExists=/var/lib/svky/begaran/{op}" in p


def test_ytan_far_bara_skriva_i_begarankatalogen():
    direktiv = [r.strip() for r in
                (REPOROT / "drift/systemd/svky-driftyta.service").read_text().splitlines()
                if r.strip() and not r.strip().startswith("#")]
    skriv = [r for r in direktiv if r.startswith("ReadWritePaths=")]
    assert skriv == ["ReadWritePaths=/var/lib/svky/begaran"], skriv
    assert "DynamicUser=yes" not in direktiv, "efemär uid kan inte äga katalogen"


def test_js_skickar_urlencodat_inte_multipart():
    """FormData skickar multipart, servern läser urlencoded. Kryssrutan
    försvann då på vägen och promoteringen avvisades med 400 - ett fel som
    BARA fanns i JS-vägen, eftersom formuläret utan JS kodar rätt själv."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "new URLSearchParams(new FormData(form))" in kod
    assert "body: new FormData(form)" not in kod


# --- knappens svar -------------------------------------------------------

def test_fragmentet_bar_maskinlasbart_tillstand(tmp_path):
    """JS måste kunna se om jobbet kör. Utan det kan den bara gissa, och en
    knapp som gissar fel ser ut att inte ha registrerat trycket."""
    yta = _ladda_yta(_skriv(tmp_path))
    frag = yta.fragment()
    for attr in ('id="tillstand"', "data-vantande=", "data-aktiv=", "data-staging="):
        assert attr in frag, f"{attr} saknas i fragmentet"


def test_knappens_upptagetlage_haller_pa_operationen_inte_elementet():
    """innerHTML river knappen och bygger en ny vid varje fragmentbyte. En
    sparad elementreferens pekar då på något som inte längre finns i
    dokumentet, och spinnern sitter kvar på det borttagna elementet."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "operation: form.action.split('/begar/')[1]" in kod
    assert "knapp: knapp" not in kod, "håller en elementreferens"
    # applicera() måste köras efter varje hämtning, annars försvinner läget
    hamta = kod[kod.index("async function hamta"):kod.index("document.addEventListener")]
    assert "applicera();" in hamta


def test_ingen_ny_version_ar_ett_svar():
    """Det vanligaste utfallet av knappen. Utan besked ser ett lyckat klick
    likadant ut som ett som aldrig gick fram."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "Ingen ny version" in kod


def test_knappen_slappes_aven_om_jobbet_tystnar():
    """En knapp som sitter upptagen för alltid är en trasig sida."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "Jobbet svarar inte" in kod
    assert "slutaArbeta();" in kod


def test_spinnern_stannar_vid_reducerad_rorelse():
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "prefers-reduced-motion" in kod


# --- felbesked och lankar ------------------------------------------------

def test_trasig_path_enhet_syns_pa_sidan(tmp_path):
    """En path-enhet som fallerat plockar inte upp något. Utan raden ser en
    död knapp ut som ett långsamt jobb, och enda spåret är att en markör
    ligger kvar."""
    yta = _ladda_yta(_skriv(tmp_path, begaran_trasiga="svky-begaran-uppdatera.path"))
    html = yta.sida()
    assert "Knapparna fungerar inte" in html
    assert "reset-failed" in html, "säger inte hur man lagar det"


def test_besked_har_egen_krok(tmp_path):
    """JS plockar upp beskedet efter en avvisad begäran. Sidan kan ha andra
    varningar samtidigt, och att ta den första hade visat fel mening."""
    yta = _ladda_yta(_skriv(tmp_path))
    assert 'id="besked"' in yta.fragment("Något gick fel.", "varning")
    assert "innehall.querySelector('#besked')" in \
        (REPOROT / "drift/svky-driftyta.py").read_text()


def test_avvisad_begaran_bar_serverns_egen_forklaring():
    """Servern VET varför den avvisade. Att ersätta det med en generisk
    mening skickar felsökningen till fel ställe - det gjorde den."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert 'headers: {\'X-Fragment\': \'1\'}' in kod
    assert 'self.headers.get("X-Fragment")' in kod


def test_lankar_till_alla_tre_miljoerna(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path))
    html = yta.sida()
    for namn in ("Produktion", "Staging", "Mailpit"):
        assert namn in html
    assert "https://svky.se" in html


def test_begaran_enheterna_har_ingen_startgrans():
    """Systemds förval är fem starter per tio sekunder. Slås den ut hamnar
    enheten i failed och plockar inte upp NÅGOT mer - knappen blir tyst död
    tills någon kör reset-failed. Hände i drift 2026-09-07."""
    for op in ("uppdatera", "promotera"):
        for andelse in (".path", ".service"):
            fil = REPOROT / f"drift/systemd/svky-begaran-{op}{andelse}"
            assert "StartLimitIntervalSec=0" in fil.read_text(), fil.name


# --- driftkodens lage ----------------------------------------------------

def test_efterliggande_driftkod_syns(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path, drift={
        "efter": "3", "amne": "Laga knappen", "outrullade": ""}))
    html = yta.sida()
    assert "ligger 3 commitar efter" in html
    assert "Laga knappen" in html, "säger inte VAD som hämtas"


def test_outrullade_kopior_syns_som_egen_rad(tmp_path):
    """Servern kör då annan driftkod än repot visar. Hände 2026-09-07:
    StartLimitIntervalSec-fixen kopierades aldrig, reset-failed dolde det,
    och knapparna kunde dö tyst igen medan sidan såg frisk ut."""
    yta = _ladda_yta(_skriv(tmp_path, drift={
        "efter": "0", "amne": "", "outrullade": "svky-begaran-uppdatera.path"}))
    html = yta.sida()
    assert "INTE matchar utcheckningen" in html
    assert "svky-begaran-uppdatera.path" in html


def test_de_tva_fragorna_haller_isar(tmp_path):
    """De slocknar vid olika tillfällen. I fas med GitHub men outrullad ska
    fortfarande varna - annars döljs att hämtningen gjordes men inte
    utrullningen."""
    yta = _ladda_yta(_skriv(tmp_path, drift={
        "efter": "0", "amne": "", "outrullade": "svky-driftyta"}))
    html = yta.sida()
    assert "commitar efter" not in html
    assert "INTE matchar utcheckningen" in html


def test_okant_driftlage_sags_vara_okant(tmp_path):
    """git fetch kan falla. Att då säga i fas vore en lögn."""
    yta = _ladda_yta(_skriv(tmp_path, drift={"efter": "", "amne": "", "outrullade": ""}))
    html = yta.sida()
    assert "Kunde inte jämföra driftkoden" in html
    assert "inte samma sak" in html


def test_allt_i_fas_ger_ingen_rad(tmp_path):
    """En sida som varnar om allt slutar man läsa."""
    yta = _ladda_yta(_skriv(tmp_path, drift={"efter": "0", "amne": "", "outrullade": ""}))
    html = yta.sida()
    assert "commitar efter" not in html
    assert "INTE matchar utcheckningen" not in html


def test_samlaren_har_filnamnen_i_koden():
    """Ett steg som läser vad det ska jämföra ur någon annans fil är svårare
    att lita på än en lista man kan granska."""
    assert "svky-begaran-promotera.service" in SAMLARE
    assert "/usr/local/bin/svky-driftyta" in SAMLARE


def test_misslyckad_fetch_sager_varfor(tmp_path):
    """Kunde inte jämföra utan orsak skickar felsökningen till fel ställe -
    en enhet som faller av en rättighet ser likadan ut som ett nätfel."""
    yta = _ladda_yta(_skriv(tmp_path, drift={
        "efter": "", "amne": "", "outrullade": "",
        "fel": "fatal: could not read Username for 'https://github.com'"}))
    html = yta.sida()
    assert "Kunde inte jämföra driftkoden" in html
    assert "could not read Username" in html, "orsaken kom inte med"


def test_samlaren_fangar_gits_egen_utdata():
    assert "_fetchfel=$(git fetch origin 2>&1" in SAMLARE
    assert 'drift_fel="git fetch föll utan att säga varför"' in SAMLARE


# --- att skriptet ens parsar ---------------------------------------------

def test_js_i_den_serverade_sidan_parsar(tmp_path):
    """Provet som saknades.

    SKAL var en vanlig Python-sträng, så Python åt JS:ens escapesekvenser:
    \\n blev en RIKTIG radbrytning mitt i en JS-sträng och hela skriptet föll
    på "Invalid or unexpected token". Sidan såg oförändrad ut - den slutade
    bara uppdatera sig, och knapparna gjorde ingenting.

    Alla andra prov läser Python-källan och hade godkänt det. Det här läser
    vad som FAKTISKT skickas.
    """
    import re
    import shutil
    import subprocess

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("node saknas")

    yta = _ladda_yta(_skriv(tmp_path))
    js = re.search(r"<script>(.*?)</script>", yta.sida(), re.S)
    assert js, "hittade inget skriptblock"

    fil = tmp_path / "yta.js"
    fil.write_text(js.group(1))
    r = subprocess.run([node, "--check", str(fil)], capture_output=True, text=True)
    assert r.returncode == 0, f"JS parsar inte:\n{r.stderr}"


def test_skalet_ar_en_ra_strang():
    """Utan r-prefixet tolkar Python JS:ens backslash-sekvenser, och felet
    syns först i webbläsaren."""
    assert 'SKAL = r"""' in (REPOROT / "drift/svky-driftyta.py").read_text()


def test_felsokningsdata_bar_bade_radata_och_det_som_visas():
    """Rådatan säger vad servern tyckte, meddelandena vad användaren såg.
    Skillnaden mellan dem är oftast felet."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "MEDDELANDEN PÅ SIDAN" in kod and "RÅTT LÄGE" in kod
    assert "'/lage.json'" in kod


def test_kopieringen_visar_texten_om_urklipp_nekas():
    """clipboard kräver säkert ursprung och kan nekas. Att tiga då vore
    värre än att visa texten - användaren kan markera själv."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "Kunde inte kopiera, visar texten" in kod
    assert "createElement('textarea')" in kod


def test_olasbar_fil_rapporteras_inte_som_olik(tmp_path):
    """En rotägd 600-fil går inte att jämföra som rasmus. Att rapportera det
    som en skillnad är att presentera en oförmåga att kontrollera som ett
    resultat - sidan sa att servern körde annan kod fast filerna var
    identiska."""
    yta = _ladda_yta(_skriv(tmp_path, drift={
        "efter": "0", "amne": "", "fel": "", "outrullade": "",
        "olasbara": "svky-begaran-uppdatera.path"}))
    html = yta.sida()
    assert "Kunde inte JÄMFÖRA" in html
    assert "vet inte om de är i fas" in html
    assert "INTE matchar utcheckningen" not in html, "kallade det en skillnad"
    assert "install -m 644" in html, "säger inte hur man lagar det"


def test_samlaren_skiljer_olasbar_fran_olik():
    assert 'if [ ! -r "$2" ]; then' in SAMLARE
    assert "drift_olasbara" in SAMLARE


# --- UX-granskningens fynd (doc 01M1YRTR) --------------------------------

@pytest.mark.parametrize(
    "trasigt",
    [
        {"drift": {"efter": 3, "amne": "", "fel": "", "outrullade": "", "olasbara": ""}},
        {"drift": {"efter": "0", "amne": "", "fel": "", "outrullade": ["a.path"],
                   "olasbara": ""}},
        {"uppetidssond": 1},
        {"begaran_trasiga": ["a.path"]},
        {"ci": {"utfall": None, "sha": 12345, "url": None, "tid": None}},
    ],
)
def test_fel_datatyp_kraschar_inte_sidan(tmp_path, trasigt):
    """Lägesfilen skrivs av ett skalskript. Ett fält som blir ett tal eller en
    lista fick html.escape att kasta AttributeError, och HELA sidan dog med
    tom anslutning - exakt den 'ser ut som inget hände' resten av koden är
    byggd för att undvika."""
    yta = _ladda_yta(_skriv(tmp_path, **trasigt))
    html = yta.sida()
    assert "svky.se drift" in html


def test_raderna_sorteras_efter_allvar(tmp_path):
    """Den kritiska 'knapparna fungerar inte' hamnade mitt i en stapel på sex
    rader, medan 'läget är 30 minuter gammalt' låg överst."""
    gammal = (datetime.datetime.now(datetime.UTC)
              - datetime.timedelta(minutes=30)).isoformat()
    yta = _ladda_yta(_skriv(
        tmp_path, hamtad=gammal, begaran_trasiga="svky-begaran-uppdatera.path",
        drift={"efter": "3", "amne": "Nåt", "fel": "",
               "outrullade": "svky-driftyta", "olasbara": ""}))
    html = yta.sida()

    blockerad = html.index("Knapparna fungerar inte")
    okand_kod = html.index("INTE matchar utcheckningen")
    gammalt = html.index("minuter gammalt")
    efter = html.index("commitar efter")

    assert blockerad < okand_kod < gammalt < efter, (
        "fel ordning: en blockerad knapp ska stå före ett gammalt läge")


def test_besked_star_overst(tmp_path):
    """Svaret på det man just gjorde ska inte hamna under fyra varningar."""
    yta = _ladda_yta(_skriv(tmp_path, begaran_trasiga="a.path"))
    html = yta.fragment("En begäran ligger redan och väntar.", "varning")
    assert html.index("ligger redan och väntar") < html.index("Knapparna fungerar inte")


def test_bekraftelsen_svarar_pa_svenska():
    """required ger en bubbla på webbläsarens språk som lägger sig ÖVER
    knappen på 390px. required ligger kvar för den som saknar JS."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "f.noValidate = true" in kod
    assert "Kryssa i rutan först" in kod
    assert 'required' in kod, "skyddet utan JS togs bort"


def test_driftraderna_hanvisar_till_knapparna(tmp_path):
    """Knapparna finns nu. Ett besked som pekar på ett kommando lär en att
    sidan är gammal."""
    yta = _ladda_yta(_skriv(tmp_path, drift={
        "efter": "2", "amne": "Nåt", "fel": "", "outrullade": "x", "olasbara": ""}))
    html = yta.sida()
    assert "Tryck Hämta driftkod" in html
    assert "Tryck Rulla ut drift/" in html


# --- rätt besked per operation (TASK-1698) -------------------------------

def test_varje_operation_foljer_sin_egen_storhet():
    """Att alltid jämföra stagings digest gav 'ingen ny version' även när
    Hämta driftkod just hämtat fyra commitar. Ett besked som säger fel sak
    med självförtroende är sämre än inget besked."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    for op, storhet in [("uppdatera", "t.staging"), ("promotera", "t.prod"),
                        ("hamta-driftkod", "t.efter"), ("rulla-ut", "t.outrullade")]:
        assert f"jobb.operation === '{op}'" in kod, op
        assert storhet in kod, f"{op} jämför inte {storhet}"
    assert "digestFore" not in kod, "jämför fortfarande bara en digest"


def test_tillstandet_bar_alla_fyra_storheterna(tmp_path):
    yta = _ladda_yta(_skriv(tmp_path))
    frag = yta.fragment()
    for attr in ("data-staging=", "data-prod=", "data-efter=", "data-outrullade="):
        assert attr in frag, attr


# --- driftytans eget kort (TASK-1699) ------------------------------------

@pytest.mark.parametrize(
    ("drift", "vantat"),
    [
        ({"efter": "0", "outrullade": "", "olasbara": ""}, "i fas"),
        ({"efter": "3", "outrullade": "", "olasbara": ""}, "ligger efter"),
        ({"efter": "0", "outrullade": "x.path", "olasbara": ""}, "ej utrullad"),
        ({"efter": "0", "outrullade": "", "olasbara": "x.path"}, "okänt"),
        ({"efter": "", "outrullade": "", "olasbara": ""}, "okänt"),
    ],
)
def test_driftkortets_status(tmp_path, drift, vantat):
    """Frågan 'vilken driftkod kör den här sidan' gick inte att svara på
    utan att något var trasigt - uppgifterna syntes bara som varningar."""
    fullt = {"amne": "", "fel": "", "commit": "abcdef123456", "utrullat": "abcdef123456"}
    fullt.update(drift)
    yta = _ladda_yta(_skriv(tmp_path, drift=fullt))
    html = yta.sida()
    # Rubriken, inte ordet. Ordet "Driftytan" står också i CSS-kommentarerna,
    # och en sökning på det plockade upp stilmallen i stället för kortet.
    assert "<h2>Driftytan" in html
    start = html.index("<h2>Driftytan")
    kort = html[start:start + 220]
    assert vantat in kort, f"kortet sa inte {vantat!r}"


def test_driftkortet_visar_bada_commitarna(tmp_path):
    """En filjämförelse säger bara ATT kopiorna skiljer sig - inte vad de
    kom från, vilket är den fråga man faktiskt har."""
    yta = _ladda_yta(_skriv(tmp_path, drift={
        "efter": "0", "amne": "", "fel": "", "outrullade": "", "olasbara": "",
        "commit": "aaaaaaaabbbb", "utrullat": "ccccccccdddd"}))
    html = yta.sida()
    assert "aaaaaaaa" in html and "cccccccc" in html
    assert "Utcheckning" in html and "Utrullat" in html


def test_ingen_driftvarning_nar_allt_ar_i_fas(tmp_path):
    """Kortet bär det normala läget, raderna bär undantagen. En sida som
    varnar om allt slutar man läsa."""
    yta = _ladda_yta(_skriv(tmp_path, drift={
        "efter": "0", "amne": "", "fel": "", "outrullade": "", "olasbara": "",
        "commit": "abcdef123456", "utrullat": "abcdef123456"}))
    html = yta.sida()
    assert "commitar efter" not in html
    assert "INTE matchar utcheckningen" not in html
    assert "Kunde inte JÄMFÖRA" not in html
    assert "i fas" in html


def test_avbrott_under_jobb_ar_inte_ett_fel():
    """Rulla ut drift/ startar om den här tjänsten, alltså den som ska
    rapportera. Att kalla det brutet skrämmer i onödan och döljer att jobbet
    gör sitt."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert "den startar troligen om" in kod
    assert "if (jobb) {" in kod


def test_pagaende_jobb_syns_visuellt():
    """En grå rad längst upp läser man förbi, och då ser ett klick ut som
    att inget hände."""
    kod = (REPOROT / "drift/svky-driftyta.py").read_text()
    assert ".status.arbetar::before" in kod
    assert "animation: snurr" in kod


def test_sidan_sager_att_jobbet_pagar(tmp_path):
    """Ett pågående jobb ska SYNAS, både för läsaren och för skriptet.

    Utan den här raden hade sidan bara ett svar på "väntar något på att
    starta" och inget alls på "kör något just nu" - och en promotering som
    tar minuter såg då ut som ingenting alls.
    """
    yta = _ladda_yta(_skriv(tmp_path))
    yta.KORANDE = tmp_path / "korande"
    yta.KORANDE.mkdir()
    (yta.KORANDE / "promotera").touch()

    html = yta.fragment()

    assert "Pågår just nu" in html
    assert yta.OPERATIONER["promotera"] in html
    assert 'data-korande="promotera"' in html


def test_tomt_korlage_utan_katalog(tmp_path):
    """Katalogen finns inte förrän ett jobb kört första gången."""
    yta = _ladda_yta(_skriv(tmp_path))
    yta.KORANDE = tmp_path / "finns-inte"

    html = yta.fragment()

    assert "Pågår just nu" not in html
    assert 'data-korande=""' in html


def _synlig_text(html: str) -> str:
    """Det en läsare faktiskt ser.

    Kodblock plockas bort först: enhetsnamn och kommandon SKA stå
    oöversatta, annars går de inte att klistra in i ett skal.
    """
    utan_kod = re.sub(r"<code>.*?</code>", " ", html, flags=re.S)
    return re.sub(r"<[^>]*>", " ", utan_kod)


@pytest.mark.parametrize("lage", ["friskt", "trasigt"])
def test_inget_engelskt_lage_visas(tmp_path, lage):
    """Sidan blandade svenska rubriker med docker- och systemd-engelska.

    Provet frågar ORDBOKEN i stället för att räkna upp ord för hand - läggs
    ett nytt läge till utan översättning ska provet fånga det, inte tiga.
    """
    trasigt = {
        "produktion": {"image": "ghcr.io/x@sha256:" + "a" * 64,
                       "commit": "a" * 40, "status": "exited"},
        "uppdaterare": {"resultat": "failed", "avslutad": "Mon 2026-09-07 20:54:23 UTC",
                        "exitkod": "1", "timer": "active", "aktiv": "inactive"},
        "uppetidssond": "inactive",
        "ci": {"utfall": "failure", "sha": "abc12345", "url": "https://x", "tid": "nu"},
    }
    yta = _ladda_yta(_skriv(tmp_path, **(trasigt if lage == "trasigt" else {})))

    synligt = _synlig_text(yta.fragment())

    # Förutsättningen: hittar provet ingen översatt text mäter det ingenting.
    assert "kör" in synligt or "avslutad" in synligt
    for engelskt in yta._ORDBOK:
        assert not re.search(rf"\b{re.escape(engelskt)}\b", synligt), \
            f"{engelskt!r} står oöversatt i {lage} läge"


def test_veckodagen_oversatts(tmp_path):
    """systemd skriver "Mon 2026-09-07 20:54:23 UTC". Bara veckodagen är ett
    ord, och den är den enda engelskan på raden."""
    yta = _ladda_yta(_skriv(tmp_path, uppdaterare={
        "resultat": "success", "avslutad": "Mon 2026-09-07 20:54:23 UTC",
        "exitkod": "0", "timer": "active", "aktiv": "inactive"}))

    html = yta.fragment()

    assert "mån 2026-09-07 20:54:23 UTC" in html
    assert "Mon 2026" not in html


def test_okant_lage_visas_ooversatt(tmp_path):
    """Ett ord ordboken inte känner igen ska SYNAS, inte bli "okänt".

    Verktygen får nya lägen ibland, och att svälja det ordet hade dolt just
    det som var värt att läsa.
    """
    yta = _ladda_yta(_skriv(tmp_path, uppetidssond="nagot-helt-nytt"))

    assert "nagot-helt-nytt" in yta.fragment()
