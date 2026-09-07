#!/usr/bin/env python3
"""Driftytan för svky.se. Visar läget, kan ingenting.

Läser BARA lägesfilen som samlaren skriver. Ytan har därmed inga rättigheter
alls: ingen dockersocket, ingen systemctl, inga hemligheter. Att läsa vad som
körs kräver dockersocketen, och den som når den kan allt med varje container.

Knapparna (TASK-1689) ger INTE ytan några rättigheter. Ett tryck skriver en
tom markörfil i begärankatalogen, och en systemd path-enhet ser filen och
startar jobbet. Ytan kan alltså be, aldrig utföra.

Vilken operation som avses avgörs av VILKEN FIL som skrevs, inte av något
ytan skickar. Namnen står i OPERATIONER nedan och kan inte påverkas utifrån -
en begäran med ett okänt namn avvisas innan något skrivs.

Nås bara över tailnet, se docs/staging.md. Ingen inloggning - gränsen är
tailnetet, precis som för Mailpit.
"""

from __future__ import annotations

import html
import json
import os
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

LAGESFIL = Path(os.environ.get("SVKY_LAGESFIL", "/var/lib/svky/lage.json"))
BEGARAN = Path(os.environ.get("SVKY_BEGARAN", "/var/lib/svky/begaran"))
# Begäran försvinner när jobbet STARTAR. Körmarkören ligger kvar tills det är
# klart, och är därför det enda ytan har som svarar på "pågår det något nu".
KORANDE = Path(os.environ.get("SVKY_KORANDE", "/var/lib/svky/korande"))
PORT = int(os.environ.get("SVKY_DRIFTYTA_PORT", "8002"))

# Adresserna till miljöerna. Förvalen är serverns, men de står i miljön så
# att ett byte av tailnetnamn inte kräver en ny utrullning av skriptet.
LANKAR = [
    ("Produktion", os.environ.get("SVKY_URL_PROD", "https://svky.se")),
    ("Staging", os.environ.get(
        "SVKY_URL_STAGING", "https://svky-server.ussuri-tawny.ts.net:8443")),
    ("Mailpit (stagings post)", os.environ.get(
        "SVKY_URL_MAILPIT", "https://svky-server.ussuri-tawny.ts.net:8444")),
]

# Verb utan argument. Nyckeln ÄR operationen - ytan kan inte peka ut en
# digest, en container eller ett kommando, och vad varje operation gör står
# i systemd-enheten den startar, inte här.
OPERATIONER = {
    "uppdatera": "Kolla efter ny version nu",
    "promotera": "Befordra staging till produktionen",
    "hamta-driftkod": "Hämta driftkod",
    "rulla-ut": "Rulla ut drift/",
}

# Äldre än så och läget kallas okänt. En frusen fil som säger "allt är bra" är
# värre än ingen fil alls: den ser ut som ett svar.
MAX_ALDER_S = 300


def _alder(hamtad: str | None) -> float | None:
    if not hamtad:
        return None
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(hamtad)).total_seconds()
    except ValueError:
        return None


def las_lage() -> tuple[dict, str | None]:
    """(läge, fel). Fel är en läsbar mening, aldrig ett tomt läge som ser friskt ut."""
    try:
        lage = json.loads(LAGESFIL.read_text())
    except FileNotFoundError:
        return {}, f"Lägesfilen {LAGESFIL} finns inte. Har samlaren kört?"
    except (OSError, json.JSONDecodeError) as e:
        return {}, f"Kunde inte läsa {LAGESFIL}: {e}"

    alder = _alder(lage.get("hamtad"))
    if alder is None:
        return lage, "Lägesfilen saknar tidsstämpel, så åldern går inte att bedöma."
    if alder > MAX_ALDER_S:
        return lage, f"Läget är {int(alder // 60)} minuter gammalt. Kör samlaren?"
    return lage, None


def begar(operation: str) -> str | None:
    """Skriver markörfilen. Returnerar ett fel som mening, eller None."""
    if operation not in OPERATIONER:
        return "Okänd operation."
    try:
        BEGARAN.mkdir(parents=True, exist_ok=True)
        markor = BEGARAN / operation
        if markor.exists():
            return "En begäran ligger redan och väntar. Jobbet startar strax."
        markor.touch()
    except OSError as e:
        return f"Kunde inte lägga begäran: {e}"
    return None


def _markorer(katalog: Path) -> set[str]:
    try:
        return {f.name for f in katalog.iterdir()} & set(OPERATIONER)
    except OSError:
        return set()


def vantande() -> set[str]:
    """Begärt men ännu inte påbörjat."""
    return _markorer(BEGARAN)


def korande() -> set[str]:
    """Påbörjat och inte klart. Enheten lägger markören, ytan bara läser."""
    return _markorer(KORANDE)


def esc(x) -> str:
    """html.escape på VAD SOM HELST.

    Lägesfilen skrivs av ett skalskript. Ett fält som blir ett tal eller en
    lista i stället för en sträng fick html.escape att kasta AttributeError,
    och hela sidan dog med tom anslutning - exakt den "ser ut som inget
    hände" resten av koden är byggd för att undvika.
    """
    return html.escape(x if isinstance(x, str) else str(x))


def _v(x) -> str:
    """Ett saknat värde ska SYNAS som saknat, inte som tomrum."""
    return esc(x) if x else '<span class="saknas">okänt</span>'


def _driftkort(drift: dict) -> str:
    """Driftytans eget kort.

    Uppgifterna finns redan, men syntes bara som VARNINGAR när något var
    fel. Går allt bra sa sidan ingenting alls om sig själv, medan de andra
    två miljöerna alltid hade ett kort - och då gick frågan "vilken driftkod
    kör den här sidan" inte att svara på utan att något var trasigt.
    """
    efter = drift.get("efter")
    outrullade = drift.get("outrullade")
    olasbara = drift.get("olasbara")

    if efter in (None, ""):
        status, klass = "okänt", "fel"
    elif olasbara:
        status, klass = "okänt", "fel"
    elif outrullade:
        status, klass = "ej utrullad", "fel"
    elif str(efter) != "0":
        status, klass = "ligger efter", "fel"
    else:
        status, klass = "i fas", "ok"

    return f"""<section class="kort">
  <h2>Driftytan <span class="pill {klass}">{esc(status)}</span></h2>
  <dl>
    <dt>Utcheckning</dt><dd><code>{_v((drift.get("commit") or "")[:8])}</code></dd>
    <dt>Utrullat</dt><dd><code>{_v((drift.get("utrullat") or "")[:8])}</code></dd>
  </dl>
</section>"""


def _kort(rubrik: str, miljo: dict) -> str:
    image = miljo.get("image") or ""
    digest = image.split("@")[-1][:19] + "…" if "@" in image else image
    status = miljo.get("status")
    klass = "ok" if status == "running" else "fel"
    return f"""<section class="kort">
  <h2>{esc(rubrik)} <span class="pill {klass}">{_v(status)}</span></h2>
  <dl>
    <dt>Digest</dt><dd><code>{_v(digest)}</code></dd>
    <dt>Commit</dt><dd><code>{_v((miljo.get('commit') or '')[:8])}</code></dd>
  </dl>
</section>"""


def fragment(besked: str = "", beskedklass: str = "") -> str:
    lage, fel = las_lage()
    prod, stag = lage.get("produktion") or {}, lage.get("staging") or {}
    senaste = lage.get("senaste_bygge")
    upp = lage.get("uppdaterare") or {}
    ci = lage.get("ci")

    # Raderna sorteras efter ALLVAR, inte efter i vilken ordning de råkar
    # byggas. Den kritiska "knapparna fungerar inte" hamnade tidigare mitt i
    # en stapel på sex rader, medan "läget är 30 minuter gammalt" låg överst.
    # Lägst nummer först.
    rader: list[tuple[int, str]] = []

    def lagg(allvar: int, html_: str) -> None:
        if html_:
            rader.append((allvar, html_))

    varning = f'<p class="varning">{esc(fel)}</p>' if fel else ""
    # Egen id, inte bara en klass. JS plockar upp den efter en avvisad
    # begäran, och sidan kan ha andra varningar - att ta den första hade
    # visat fel mening, vilket är sämre än ingen.
    beskedrad = (f'<p id="besked" class="{beskedklass or "info-rad"}">'
                 f'{esc(besked)}</p>' if besked else "")

    kvar = vantande()
    if kvar:
        namn = ", ".join(sorted(OPERATIONER[o] for o in kvar))
        beskedrad += (f'<p class="info-rad">Väntar på att köras: {esc(namn)}. '
                      'Sidan uppdateras av sig själv.</p>')

    # Två olika besked, för två olika lägen. "Väntar" betyder att systemd
    # ännu inte plockat upp begäran, "pågår" att jobbet faktiskt kör - och
    # den senare kan vara i flera minuter vid en promotering.
    igang = korande()
    if igang:
        namn = ", ".join(sorted(OPERATIONER[o] for o in igang))
        beskedrad += (f'<p class="info-rad">Pågår just nu: {esc(namn)}. '
                      'Sidan uppdateras av sig själv.</p>')

    # Promoteringen byter version i drift. Den kräver att rutan kryssas i
    # samma post - ett ensamt klick ska inte kunna göra det.
    knappar = f"""<section class="kort atgarder">
  <h2>Åtgärder</h2>
  <form method="post" action="/begar/uppdatera">
    <button type="submit">{esc(OPERATIONER['uppdatera'])}</button>
    <span class="hjalp">Samma jobb som timern, utan att vänta ut de fem minuterna.</span>
  </form>
  <form method="post" action="/begar/hamta-driftkod">
    <button type="submit">{esc(OPERATIONER['hamta-driftkod'])}</button>
    <span class="hjalp">merge --ff-only. Vägrar om något divergerat - lokala
      commitar kastas aldrig. Rullar INTE ut.</span>
  </form>
  <form method="post" action="/begar/rulla-ut">
    <button type="submit">{esc(OPERATIONER['rulla-ut'])}</button>
    <span class="hjalp">Kopierar till /usr/local/bin och /etc/systemd/system,
      laddar om systemd. Bara ett jobb åt gången kan köra.</span>
  </form>
  <form onsubmit="return false" class="kopiera">
    <button type="button" id="kopiera">&#128203; Kopiera felsökningsdata</button>
    <span class="hjalp">Läget och alla synliga meddelanden som text att klistra
      in. Inga hemligheter - samma uppgifter som står på sidan.</span>
  </form>
  <form method="post" action="/begar/promotera" class="farlig">
    <label><input type="checkbox" name="bekrafta" value="ja" required>
      Jag vill byta version i <strong>produktionen</strong></label>
    <button type="submit">{esc(OPERATIONER['promotera'])}</button>
    <span class="hjalp">Tar en backup, verifierar signaturen igen och rullar
      tillbaka om hälsan uteblir. Se docs/staging.md.</span>
  </form>
</section>"""

    # Skillnaden mellan miljöerna är den fråga man oftast kommer hit med.
    if prod.get("image") and stag.get("image"):
        if prod["image"] == stag["image"]:
            diff = '<p class="ok-rad">Produktionen kör samma version som staging.</p>'
        else:
            # Hänvisa till knappen, inte till kommandot. Kommandot finns
            # kvar och fungerar, men ett besked som pekar förbi den åtgärd
            # som står längre ned på samma sida lär en att sidan är gammal.
            diff = ('<p class="info-rad">Staging ligger före produktionen. '
                    'Befordra med knappen under Åtgärder.</p>')
    else:
        diff = '<p class="varning">Kan inte jämföra miljöerna, en av dem är okänd.</p>'

    if senaste and stag.get("image"):
        nytt = ('<p class="info-rad">Ett nyare bygge finns än det staging kör. '
                'Staginguppdateraren hämtar det inom fem minuter.</p>'
                if senaste != stag["image"] else
                '<p class="ok-rad">Staging kör senaste bygget.</p>')
    else:
        nytt = '<p class="varning">Kunde inte slå upp senaste bygget i registret.</p>'

    if ci:
        kl = "ok" if ci.get("utfall") == "success" else "fel"
        ci_rad = (f'<p>Senaste körningen på main: '
                  f'<a href="{esc(ci.get("url", "#"))}">'
                  f'<span class="pill {kl}">{_v(ci.get("utfall"))}</span></a> '
                  f'<code>{_v(ci.get("sha"))}</code> {_v(ci.get("tid"))}</p>')
    else:
        ci_rad = ('<p class="varning">CI-läget kunde inte hämtas. Det är INTE '
                  'samma sak som att bygget är grönt - ett bygge som faller '
                  'når aldrig den här servern.</p>')

    # En path-enhet som fallerat plockar inte upp något. Utan den här raden
    # ser en död knapp ut som ett långsamt jobb.
    # TVÅ rader, inte en. De slocknar vid olika tillfällen: den första när
    # koden hämtats, den andra först när den rullats ut. Slås de ihop döljs
    # att en hämtning lyckades men utrullningen inte gjordes.
    drift = lage.get("drift") or {}
    efter = drift.get("efter")
    driftrad_okand = driftrad_efter = driftrad_kod = ""
    if efter in (None, ""):
        orsak = drift.get("fel")
        driftrad_okand = ('<p class="varning">Kunde inte jämföra driftkoden mot '
                          'GitHub. Det är inte samma sak som att den är i fas.'
                          + (f' Orsak: <code>{esc(orsak)}</code>' if orsak else "")
                          + "</p>")
    elif str(efter) != "0":
        driftrad_efter = (f'<p class="info-rad">Driftkoden ligger {esc(efter)} '
                          f'commitar efter: <em>{esc(drift.get("amne") or "")}</em>. '
                          'Tryck Hämta driftkod under Åtgärder.</p>')

    olasbara = drift.get("olasbara")
    if olasbara:
        driftrad_kod += (
            f'<p class="varning">Kunde inte JÄMFÖRA: '
            f'<code>{esc(olasbara)}</code>. Filerna är rotägda och '
            'oläsbara för samlaren, så vi vet inte om de är i fas. Installera '
            'dem med <code>sudo install -m 644</code> i stället för '
            '<code>cp</code> - enhetsfiler bär inga hemligheter.</p>')

    outrullade = drift.get("outrullade")
    if outrullade:
        driftrad_kod += (
            f'<p class="varning">Rotägda kopior som INTE matchar utcheckningen: '
            f'<code>{esc(outrullade)}</code>. Servern kör alltså annan '
            'driftkod än repot visar. Tryck Rulla ut drift/ under Åtgärder.</p>')

    trasiga = lage.get("begaran_trasiga")
    trasigrad = (f'<p class="varning">Knapparna fungerar inte: '
                 f'{esc(trasiga)} är inte aktiv. '
                 f'Kör <code>sudo systemctl reset-failed {esc(trasiga)}</code> '
                 f'och starta om den.</p>') if trasiga else ""

    lankrader = "".join(
        f'<li><a href="{esc(u)}">{esc(n)}</a></li>' for n, u in LANKAR)

    ures = upp.get("resultat")
    ukl = "ok" if ures == "success" else "fel"
    # "kör just nu" är ett svar, inte en lucka. Utan det här sa sidan okänt
    # varje gång samlaren råkade prova mitt i en körning.
    if upp.get("aktiv") in ("active", "activating"):
        nar = "kör just nu"
    else:
        nar = f"senast {_v(upp.get('avslutad'))}"

    # 1 svar på det man just gjorde, 2 handling blockerad, 3 kör okänd kod,
    # 4 läget osäkert, 5 något att göra, 6 allt är bra.
    lagg(1, beskedrad)
    lagg(2, trasigrad)
    lagg(3, driftrad_kod)
    lagg(4, varning)
    lagg(4, driftrad_okand)
    lagg(5, driftrad_efter)
    lagg(5, diff if "info-rad" in diff else "")
    lagg(5, nytt if "info-rad" in nytt else "")
    lagg(6, diff if "info-rad" not in diff else "")
    lagg(6, nytt if "info-rad" not in nytt else "")
    radblock = "\n".join(h for _, h in sorted(rader, key=lambda r: r[0]))

    return f"""<div class="sidgrid">
{radblock}
{_kort("Staging", stag)}
{_kort("Produktion", prod)}
{_driftkort(drift)}
{knappar}
<div class="sidopanel">
<section class="kort automatik">
  <h2>Automatik</h2>
  <p>Staginguppdateraren: <span class="pill {ukl}">{_v(ures)}</span>
     {nar}, timer {_v(upp.get('timer'))}</p>
  <p>Uppetidssond: {_v(lage.get('uppetidssond'))}</p>
  {ci_rad}
</section>
<section class="kort">
  <h2>Miljöerna</h2>
  <ul class="lankar">{lankrader}</ul>
</section>
</div>
</div>
<p class="hamtad">Läget hämtat {_v(lage.get('hamtad'))}.</p>
<span id="tillstand" hidden
      data-vantande="{' '.join(sorted(kvar))}"
      data-korande="{' '.join(sorted(igang))}"
      data-aktiv="{esc(upp.get('aktiv') or '')}"
      data-staging="{esc((stag.get("image") or "").split("@")[-1] if isinstance(stag.get("image"), str) else "")}"
      data-prod="{esc((prod.get("image") or "").split("@")[-1] if isinstance(prod.get("image"), str) else "")}"
      data-efter="{esc(drift.get("efter") or "")}"
      data-outrullade="{esc(drift.get("outrullade") or "")}"></span>"""


# RÅ sträng. Utan r-prefixet äter Python JS:ens escapesekvenser: \n blev en
# RIKTIG radbrytning mitt i en JS-sträng, hela skriptet föll på "Invalid or
# unexpected token", och sidan slutade uppdatera sig utan att något syntes.
# Ingen av mina Python-escapes behövs här - det är HTML, CSS och JS.
SKAL = r"""<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>svky.se drift</title>
<style>
 *, *::before, *::after { box-sizing: border-box; }
 body { font-family: system-ui, sans-serif; margin: 0 auto; padding: 1.5rem;
        max-width: 84rem;
        background: #f6f6f8; color: #16161a; line-height: 1.5; }
 h1 { font-size: 1.3rem; margin: 0 0 .3rem; }
 /* ETT rutnät för hela sidan, inte ett per rad. Meddelanden, kort och
    åtgärder delade förut inte kolumnkanter, och sidan såg ut som tre
    olika block ovanpå varandra i stället för en sammanhållen yta. */
 .sidgrid { display: grid; gap: 1rem; grid-template-columns: 1fr; }
 /* Rutnätets gap sätter avståndet. Styckets egen marginal låg ovanpå det
    och gav dubbelt så stort glapp mellan meddelanderaderna som mellan
    korten. */
 .sidgrid > p { margin: 0; }
 @media (min-width: 900px) {
   .sidgrid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
   /* Meddelanderaderna spänner hela bredden, åtgärderna två av tre
      kolumner - då hamnar deras högerkant på samma linje som
      Produktionskortets, och sidopanelen under Driftytan. */
   .sidgrid > .varning, .sidgrid > .ok-rad, .sidgrid > .info-rad,
   .sidgrid > p { grid-column: 1 / -1; }
   .atgarder { grid-column: span 2; }
 }
 /* Miljöerna sträcks till samma botten som Åtgärder - annars slutar
    kolumnerna på olika höjd och rektangeln får ett hack. */
 .sidopanel { display: grid; gap: 1rem; grid-template-rows: auto 1fr; }
 .kort { background: #fff; border-radius: 10px; padding: 1rem 1.2rem;
         box-shadow: 0 1px 3px rgba(0,0,0,.08); }
 .kort h2 { font-size: 1rem; margin: 0 0 .6rem; display: flex; gap: .6rem;
            align-items: center; }
 .automatik { padding: .7rem 1rem; box-shadow: none; }
 .automatik h2 { font-size: .9rem; margin-bottom: .3rem; }
 .automatik p { font-size: .82rem; margin: .2rem 0; }
 dl { display: grid; grid-template-columns: auto 1fr; gap: .3rem .8rem; margin: 0; }
 dt { color: #6b6b75; font-size: .8rem; }
 dd { margin: 0; }
 code { font-size: .82rem; word-break: break-all; }
 .pill { font-size: .72rem; padding: 2px 8px; border-radius: 999px;
         text-transform: uppercase; letter-spacing: .04em; }
 .pill.ok { background: #d8f5d8; color: #1a5c1a; }
 .pill.fel { background: #fbdcdc; color: #7a1c1c; }
 /* Mörkare än #9a9aa5: den grå texten låg på pillerns rosa
    bakgrund och nådde inte 4.5:1. */
 .saknas { color: #6b6b75; font-style: italic; }
 .pill .saknas { color: #7a1c1c; }
 .varning { background: #fff6e0; border-left: 4px solid #e0a020;
            padding: .7rem 1rem; border-radius: 6px; }
 .ok-rad, .info-rad { padding: .7rem 1rem; border-radius: 6px; }
 .ok-rad { background: #eaf7ea; border-left: 4px solid #4a9a4a; }
 .info-rad { background: #eaf0fb; border-left: 4px solid #4a72c8; }
 form { margin: 0 0 1rem; display: flex; flex-wrap: wrap; align-items: center; gap: .7rem; }
 form:last-child { margin-bottom: 0; }
 button { font: inherit; padding: .5rem 1rem; border-radius: 7px; border: 0;
          background: #24406e; color: #fff; cursor: pointer; }
 button[disabled] { opacity: .6; cursor: progress; }
 button.arbetar::before { content: ''; display: inline-block; width: .8em;
   height: .8em; margin-right: .5em; vertical-align: -.05em;
   border: 2px solid rgba(255,255,255,.35); border-top-color: #fff;
   border-radius: 50%; animation: snurr .7s linear infinite; }
 @keyframes snurr { to { transform: rotate(360deg); } }
 @media (prefers-reduced-motion: reduce) {
   button.arbetar::before { animation: none; }
 }
 /* Ett pågående jobb ska SYNAS. En grå rad längst upp läser man förbi, och
    då ser ett klick ut som att inget hände. */
 .status.arbetar { color: #24406e; background: #eaf0fb; font-weight: 600;
                   border-left: 4px solid #4a72c8; padding: .4rem .7rem;
                   border-radius: 6px; }
 .status.arbetar::before { content: ''; display: inline-block; width: .75em;
   height: .75em; margin-right: .5em; border: 2px solid rgba(36,64,110,.3);
   border-top-color: #24406e; border-radius: 50%;
   animation: snurr .7s linear infinite; }
 .status.klart { background: #eaf7ea; font-weight: 600; padding: .4rem .7rem;
                 border-left: 4px solid #4a9a4a; border-radius: 6px; }
 .status.tappad { background: #fbdcdc; font-weight: 600; padding: .4rem .7rem;
                  border-left: 4px solid #8c2b2b; border-radius: 6px; }
 @media (prefers-reduced-motion: reduce) {
   .status.arbetar::before { animation: none; }
 }
 .status.klart { color: #1a5c1a; }
 ul.lankar { margin: 0; padding-left: 1.1rem; }
 ul.lankar li { margin: .25rem 0; }
 form.farlig button { background: #8c2b2b; }
 form.kopiera button { background: #4a5568; }
 form.farlig { border-top: 1px solid #eee; padding-top: 1rem; }
 .hjalp { font-size: .8rem; color: #6b6b75; flex: 1 1 16rem; min-width: 12rem; }
 .atgarder form { margin-bottom: .6rem; }
 /* Lika breda knappar så villkorstexterna börjar på samma plats. Den
    farliga knappen hålls utanför - den ska inte linjera in i ledet. */
 @media (min-width: 700px) {
   .atgarder form:not(.farlig) button { min-width: 15.5rem; }
 }
 label { font-size: .9rem; display: flex; gap: .4rem; align-items: center; }
 form.farlig label { flex-basis: 100%; }
 .hamtad, .status { color: #6b6b75; font-size: .8rem; }
 .hamtad { margin-top: 1rem; }
 .status.tappad { color: #8c2b2b; }
 #innehall.gammalt { opacity: .55; transition: opacity .2s; }
</style></head><body>
<h1>svky.se drift</h1>
<p class="status" id="status" aria-live="polite">Läget uppdateras automatiskt.</p>
<div id="innehall">__INNEHALL__</div>
<script>
// Fragmentet renderas av SERVERN. Ett JS som byggde sidan själv hade
// behövt samma regler för okänt och degradering en gång till, och två
// uppsättningar av den logiken glider isär - just i de lägen som är svåra.
const innehall = document.getElementById('innehall');
const status = document.getElementById('status');
let missar = 0;
let jobb = null;      // {fore: {...}, operation} medan ett jobb följs
let snabbTill = 0;    // tidpunkt då den täta pollningen slutar

function tillstand() {
  const el = document.getElementById('tillstand');
  return el ? el.dataset : {vantande: '', korande: '', aktiv: '', staging: ''};
}

function sattStatus(text, klass) {
  status.textContent = text;
  status.className = 'status' + (klass ? ' ' + klass : '');
}

// Jobbet kan vara klart innan första pollningen hinner se det. Utan den här
// bokföringen ser ett lyckat klick likadant ut som ett som inte gick fram,
// och en knapp som inte svarar trycker man igen.
function knappFor(operation) {
  const form = document.querySelector('form[action="/begar/' + operation + '"]');
  return form ? form.querySelector('button') : null;
}

// Måste köras efter VARJE fragmentbyte. innerHTML river knappen och bygger
// en ny, så en sparad elementreferens pekar på något som inte längre finns i
// dokumentet - spinnern satt då på ett borttaget element och syntes aldrig.
function applicera() {
  if (!jobb) return;
  const knapp = knappFor(jobb.operation);
  if (knapp) { knapp.disabled = true; knapp.classList.add('arbetar'); }
}

function slutaArbeta() {
  if (jobb) {
    const knapp = knappFor(jobb.operation);
    if (knapp) { knapp.disabled = false; knapp.classList.remove('arbetar'); }
  }
  jobb = null;
  snabbTill = 0;
}

function jobbetKor(t) {
  // Körmarkören först: den är den enda signalen som täcker alla fyra
  // operationerna. data-aktiv mäter BARA staginguppdateraren, och sa
  // därför nej för en promotering som pågick för fullt - varpå sidan
  // avkunnade "produktionens version är oförändrad" efter en sekund.
  const namn = (jobb || {}).operation;
  if (namn && (t.korande || '').split(' ').includes(namn)) return true;
  if ((t.vantande || '').split(' ').includes(namn)) return true;
  return jobb && jobb.operation === 'uppdatera'
         && ['active', 'activating'].includes(t.aktiv);
}

function foljUppJobb() {
  if (!jobb) return;
  const t = tillstand();
  if (jobbetKor(t)) {
    // Sekunderna är svaret på "hänger den?". En promotering tar minuter,
    // och en statusrad som säger samma sak hela tiden ser stillastående ut.
    const s = Math.round((Date.now() - jobb.start) / 1000);
    sattStatus('Jobbet kör… (' + s + ' s)', 'arbetar');
    return;
  }

  // Varje operation följer SIN egen storhet. Att alltid jämföra stagings
  // digest gav 'ingen ny version' även när Hämta driftkod just hämtat fyra
  // commitar - ett besked som säger fel sak med självförtroende är sämre än
  // inget besked.
  const f = jobb.fore;
  if (jobb.operation === 'uppdatera') {
    sattStatus(t.staging && t.staging !== f.staging
      ? 'Klart. Staging bytte till ' + t.staging.slice(0, 19) + '…'
      : 'Klart. Ingen ny version - staging kör redan senaste bygget.', 'klart');
  } else if (jobb.operation === 'promotera') {
    sattStatus(t.prod && t.prod !== f.prod
      ? 'Klart. Produktionen kör nu ' + t.prod.slice(0, 19) + '…'
      : 'Klart, men produktionens version är oförändrad. Läs journalen.', 'klart');
  } else if (jobb.operation === 'hamta-driftkod') {
    sattStatus(t.efter !== f.efter
      ? 'Klart. Driftkoden hämtad. Tryck Rulla ut drift/ för att aktivera den.'
      : 'Klart. Ingen ny driftkod att hämta.', 'klart');
  } else if (jobb.operation === 'rulla-ut') {
    sattStatus(!t.outrullade
      ? 'Klart. Alla kopior matchar utcheckningen.'
      : 'Klart, men något matchar fortfarande inte: ' + t.outrullade, 'klart');
  } else {
    sattStatus('Klart.', 'klart');
  }
  slutaArbeta();
}

async function hamta(besked) {
  try {
    const svar = await fetch('/fragment' + (besked ? '?besked=' + encodeURIComponent(besked) : ''),
                             {cache: 'no-store'});
    if (!svar.ok) throw new Error('http ' + svar.status);
    innehall.innerHTML = await svar.text();
    innehall.classList.remove('gammalt');
    missar = 0;
    applicera();
    if (jobb) foljUppJobb();
    else sattStatus('Uppdaterad ' + new Date().toLocaleTimeString('sv-SE') + '.');
  } catch (e) {
    missar++;
    innehall.classList.add('gammalt');
    // Ett avbrott MEDAN ett jobb kör är oftast inte ett fel: Rulla ut drift/
    // startar om den här tjänsten, alltså den som ska rapportera. Att kalla
    // det brutet skrämmer i onödan och döljer att jobbet gör sitt.
    if (jobb) {
      sattStatus('Jobbet kör… (servern svarar inte just nu, försök ' +
                 missar + ' - den startar troligen om)', 'arbetar');
    } else {
      // Att sidan står kvar oförändrad ska SYNAS. Tyst gammal data är samma
      // fel som en tom panel: den ser ut som ett svar.
      sattStatus('Kontakten med servern bruten (' + missar +
                 ' försök). Det du ser nedan är gammalt.', 'tappad');
    }
  }
}

// Stäng av webbläsarens egen validering NÄR JS finns. required ligger kvar i
// markupen så formuläret fortfarande skyddas utan JS, men bubblan den visar
// är på webbläsarens språk och lägger sig ÖVER knappen på 390px. Med JS
// igång svarar vi på svenska i statusraden i stället.
document.querySelectorAll('form[action^="/begar/"]').forEach(f => f.noValidate = true);

document.addEventListener('submit', async (e) => {
  const form = e.target.closest('form[action^="/begar/"]');
  if (!form) return;
  e.preventDefault();

  const ruta = form.querySelector('input[type=checkbox][required]');
  if (ruta && !ruta.checked) {
    sattStatus('Kryssa i rutan först - den här knappen byter version i produktionen.', 'tappad');
    ruta.focus();
    return;
  }
  // Knappen är upptagen tills JOBBET är klart, inte tills POST:en svarat.
  // Ett jobb som tar tio sekunder ska inte se avslutat ut efter tio
  // millisekunder - då trycker man igen.
  const t0 = tillstand();
  jobb = {fore: {staging: t0.staging, prod: t0.prod,
                 efter: t0.efter, outrullade: t0.outrullade},
          operation: form.action.split('/begar/')[1],
          start: Date.now()};
  applicera();
  sattStatus('Begäran skickad…', 'arbetar');

  try {
    // URLSearchParams, inte FormData. FormData skickar multipart, och
    // servern läser urlencoded - promoteringens bekräftelseruta försvann
    // då på vägen och begäran avvisades med 400. Utan JS kodar webbläsaren
    // rätt av sig själv, så felet fanns BARA i den här vägen.
    const svar = await fetch(form.action, {
      method: 'POST',
      headers: {'X-Fragment': '1'},
      body: new URLSearchParams(new FormData(form)),
    });
    if (!svar.ok) {
      // Servern skickar tillbaka fragmentet MED sin egen förklaring.
      // Att ersätta den med "Begäran avvisades" hade dolt orsaken.
      slutaArbeta();
      innehall.innerHTML = await svar.text();
      const rad = innehall.querySelector('#besked');
      sattStatus(rad ? rad.textContent.trim() : 'Begäran avvisades.', 'tappad');
      return;
    }
    // Polla tätt en stund. Ett jobb som tar en sekund ska inte behöva
    // vänta tio på att synas.
    snabbTill = Date.now() + 120000;
    await hamta('');
  } catch (e) {
    slutaArbeta();
    sattStatus('Kunde inte skicka begäran: ' + e.message, 'tappad');
  }
});

// Sista utvägen: en knapp får inte sitta upptagen för alltid om jobbet
// aldrig rapporterar sig klart. Två minuter är samma gräns som den täta
// pollningen.
setInterval(() => {
  if (!jobb || !snabbTill || Date.now() <= snabbTill) return;
  // Kör jobbet bevisligen fortfarande får vakten inte slå till. Den gamla
  // gränsen på två minuter var kortare än promoteringens tio, så vakten
  // gav upp mitt i ett fungerande jobb och sidan tystnade.
  if (jobbetKor(tillstand())) { snabbTill = Date.now() + 120000; return; }
  sattStatus('Jobbet svarar inte. Kolla journalen på servern.', 'tappad');
  slutaArbeta();
}, 2000);

// Felsökningsdata. Samlar RÅDATA plus det sidan faktiskt visar - båda
// behövs: rådatan säger vad servern tyckte, meddelandena vad användaren såg,
// och det är skillnaden mellan dem som brukar vara felet.
document.addEventListener('click', async (e) => {
  if (e.target.id !== 'kopiera') return;
  const knapp = e.target;
  const original = knapp.textContent;
  knapp.disabled = true;
  try {
    let rad = '(kunde inte hämtas)';
    try {
      const svar = await fetch('/lage.json', {cache: 'no-store'});
      rad = JSON.stringify(await svar.json(), null, 2);
    } catch (e2) { rad = '(kunde inte hämtas: ' + e2.message + ')'; }

    const synliga = [...innehall.querySelectorAll('.varning, .info-rad, .ok-rad')]
      .map(el => '- ' + el.textContent.trim().replace(/\s+/g, ' '));

    const text =
      'svky.se driftyta, felsökningsdata\n' +
      'Kopierad: ' + new Date().toISOString() + '\n' +
      'Statusrad: ' + status.textContent.trim() + '\n\n' +
      'MEDDELANDEN PÅ SIDAN\n' +
      (synliga.length ? synliga.join('\n') : '(inga)') + '\n\n' +
      'RÅTT LÄGE\n' + rad + '\n';

    try {
      await navigator.clipboard.writeText(text);
      knapp.textContent = 'Kopierat';
    } catch (e3) {
      // clipboard kräver säkert ursprung och kan nekas. Att tiga då vore
      // värre än att visa texten - användaren kan alltid markera själv.
      knapp.textContent = 'Kunde inte kopiera, visar texten';
      const ruta = document.createElement('textarea');
      ruta.value = text;
      ruta.rows = 12;
      ruta.style.cssText = 'width:100%;margin-top:.7rem;font-family:monospace;font-size:.75rem;';
      knapp.closest('form').appendChild(ruta);
      ruta.select();
    }
  } finally {
    knapp.disabled = false;
    setTimeout(() => { knapp.textContent = original; }, 4000);
  }
});

setInterval(() => hamta(), 10000);
setInterval(() => { if (Date.now() < snabbTill) hamta(); }, 1000);
</script>
</body></html>"""


def sida(besked: str = "", beskedklass: str = "") -> str:
    """Hela sidan. Fragmentet bakas in så första laddningen visar läget
    direkt, utan att vänta på ett andra anrop."""
    return SKAL.replace("__INNEHALL__", fragment(besked, beskedklass))


class Handler(BaseHTTPRequestHandler):
    def _svara(self, kropp: bytes, typ: str, kod: int = 200) -> None:
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(kropp)))
        self.end_headers()
        self.wfile.write(kropp)

    def _nodsida(self, e: Exception) -> None:
        """Sista utvägen. En sida som dör med tom anslutning ser ut som ett
        nätfel, och då felsöker man fel sak - vilket är hela poängen med att
        den här ytan finns."""
        import traceback

        kropp = (
            "<!doctype html><meta charset=utf-8><title>svky.se drift</title>"
            "<body style='font-family:system-ui;padding:1.5rem'>"
            "<h1>Driftytan kunde inte rendera läget</h1>"
            "<p>Sidan lever, men något i lägesfilen gick inte att visa. "
            "Rådata finns på <a href='/lage.json'>/lage.json</a>.</p>"
            f"<pre style='white-space:pre-wrap;font-size:.8rem'>"
            f"{html.escape(traceback.format_exc())}</pre>"
        ).encode()
        self._svara(kropp, "text/html; charset=utf-8", 500)

    def _sida(self, besked: str = "", klass: str = "", kod: int = 200) -> None:
        """Hela sidan, eller bara fragmentet om anroparen är vårt eget JS.

        Skälet: servern VET varför en begäran avvisades - att en enhet
        fallerat, att en markör redan ligger - och JS slängde det svaret och
        skrev "Begäran avvisades". Ett besked som inte säger vad som är fel
        skickar felsökningen till fel ställe.
        """
        if self.headers.get("X-Fragment"):
            self._svara(fragment(besked, klass).encode(),
                        "text/html; charset=utf-8", kod)
        else:
            self._svara(sida(besked, klass).encode(), "text/html; charset=utf-8", kod)

    def do_GET(self):  # noqa: N802
        # GET ändrar ALDRIG något. Samma skäl som e-postlänkarnas
        # skannerskydd: en förhämtning, en historikpost eller en länk någon
        # klistrar in får inte kunna starta ett jobb i produktionen.
        vag, _, fraga = self.path.partition("?")
        vag = vag.rstrip("/")
        if vag == "/halsa":
            self._svara(b'{"ok":true}', "application/json")
        elif vag == "":
            try:
                self._sida()
            except Exception as e:  # noqa: BLE001 - sista utvägen, se _nodsida
                self._nodsida(e)
        elif vag == "/lage.json":
            # Rådata till kopieringsknappen. Innehåller digests, commitar och
            # enhetslägen - inga hemligheter. Samma fil ytan redan renderar,
            # så knappen kan inte visa något användaren inte redan ser.
            try:
                self._svara(LAGESFIL.read_bytes(), "application/json")
            except OSError as e:
                self._svara(json.dumps({"fel": str(e)}).encode(),
                            "application/json", 503)
        elif vag == "/fragment":
            besked = (parse_qs(fraga).get("besked") or [""])[0]
            try:
                self._svara(fragment(besked, "varning" if besked else "").encode(),
                            "text/html; charset=utf-8")
            except Exception as e:  # noqa: BLE001
                self._nodsida(e)
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        vag = self.path.rstrip("/")
        if not vag.startswith("/begar/"):
            self.send_error(404)
            return
        operation = vag[len("/begar/"):]

        langd = int(self.headers.get("Content-Length") or 0)
        # Ta emot en liten kropp, inte vad som helst. Formuläret bär ett fält.
        kropp = self.rfile.read(min(langd, 4096)).decode("utf-8", "replace")
        falt = parse_qs(kropp)

        if operation not in OPERATIONER:
            self._sida("Okänd åtgärd.", "varning", 404)
            return

        # Promoteringen byter version i drift och kräver en uttrycklig
        # bekräftelse i samma post. Ett ensamt klick ska inte räcka.
        if operation == "promotera" and falt.get("bekrafta") != ["ja"]:
            self._sida("Kryssa i rutan för att befordra till produktionen.",
                       "varning", 400)
            return

        fel = begar(operation)
        if fel:
            self._sida(fel, "varning", 409)
            return

        # 303 så en omladdning inte skickar begäran igen.
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        """Tyst. Journalen ska bära driftrader, inte en accesslogg."""


if __name__ == "__main__":
    # Loopback. Vägen in är tailscale serve, precis som för staging och
    # Mailpit - en öppen port hade varit en väg förbi den gränsen.
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"driftytan lyssnar på 127.0.0.1:{PORT}", file=sys.stderr, flush=True)
    srv.serve_forever()
