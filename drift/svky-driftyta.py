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


def vantande() -> set[str]:
    try:
        return {f.name for f in BEGARAN.iterdir()} & set(OPERATIONER)
    except OSError:
        return set()


def _v(x) -> str:
    """Ett saknat värde ska SYNAS som saknat, inte som tomrum."""
    return html.escape(str(x)) if x else '<span class="saknas">okänt</span>'


def _kort(rubrik: str, miljo: dict) -> str:
    image = miljo.get("image") or ""
    digest = image.split("@")[-1][:19] + "…" if "@" in image else image
    status = miljo.get("status")
    klass = "ok" if status == "running" else "fel"
    return f"""<section class="kort">
  <h2>{html.escape(rubrik)} <span class="pill {klass}">{_v(status)}</span></h2>
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

    varning = f'<p class="varning">{html.escape(fel)}</p>' if fel else ""
    # Egen id, inte bara en klass. JS plockar upp den efter en avvisad
    # begäran, och sidan kan ha andra varningar - att ta den första hade
    # visat fel mening, vilket är sämre än ingen.
    beskedrad = (f'<p id="besked" class="{beskedklass or "info-rad"}">'
                 f'{html.escape(besked)}</p>' if besked else "")

    kvar = vantande()
    if kvar:
        namn = ", ".join(sorted(OPERATIONER[o] for o in kvar))
        beskedrad += (f'<p class="info-rad">Väntar på att köras: {html.escape(namn)}. '
                      'Sidan uppdateras av sig själv.</p>')

    # Promoteringen byter version i drift. Den kräver att rutan kryssas i
    # samma post - ett ensamt klick ska inte kunna göra det.
    knappar = f"""<section class="kort" style="margin-top:1rem;max-width:60rem">
  <h2>Åtgärder</h2>
  <form method="post" action="/begar/uppdatera">
    <button type="submit">{html.escape(OPERATIONER['uppdatera'])}</button>
    <span class="hjalp">Startar samma jobb som timern, utan att vänta ut de fem minuterna.</span>
  </form>
  <form method="post" action="/begar/promotera" class="farlig">
    <label><input type="checkbox" name="bekrafta" value="ja" required>
      Jag vill byta version i <strong>produktionen</strong></label>
    <button type="submit">{html.escape(OPERATIONER['promotera'])}</button>
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
        nytt = ('<p class="info-rad">Ett nyare bygge finns än det staging kör.</p>'
                if senaste != stag["image"] else
                '<p class="ok-rad">Staging kör senaste bygget.</p>')
    else:
        nytt = '<p class="varning">Kunde inte slå upp senaste bygget i registret.</p>'

    if ci:
        kl = "ok" if ci.get("utfall") == "success" else "fel"
        ci_rad = (f'<p>Senaste körningen på main: '
                  f'<a href="{html.escape(ci.get("url", "#"))}">'
                  f'<span class="pill {kl}">{_v(ci.get("utfall"))}</span></a> '
                  f'<code>{_v(ci.get("sha"))}</code> {_v(ci.get("tid"))}</p>')
    else:
        ci_rad = ('<p class="varning">CI-läget kunde inte hämtas. Det är INTE '
                  'samma sak som att bygget är grönt - ett bygge som faller '
                  'når aldrig den här servern.</p>')

    # En path-enhet som fallerat plockar inte upp något. Utan den här raden
    # ser en död knapp ut som ett långsamt jobb.
    trasiga = lage.get("begaran_trasiga")
    trasigrad = (f'<p class="varning">Knapparna fungerar inte: '
                 f'{html.escape(trasiga)} är inte aktiv. '
                 f'Kör <code>sudo systemctl reset-failed {html.escape(trasiga)}</code> '
                 f'och starta om den.</p>') if trasiga else ""

    lankrader = "".join(
        f'<li><a href="{html.escape(u)}">{html.escape(n)}</a></li>' for n, u in LANKAR)

    ures = upp.get("resultat")
    ukl = "ok" if ures == "success" else "fel"
    # "kör just nu" är ett svar, inte en lucka. Utan det här sa sidan okänt
    # varje gång samlaren råkade prova mitt i en körning.
    if upp.get("aktiv") in ("active", "activating"):
        nar = "kör just nu"
    else:
        nar = f"senast {_v(upp.get('avslutad'))}"

    return f"""{varning}
{trasigrad}
{beskedrad}
{diff}
{nytt}
<div class="rutor">
{_kort("Produktion", prod)}
{_kort("Staging", stag)}
</div>
<section class="kort" style="margin-top:1rem;max-width:60rem">
  <h2>Automatik</h2>
  <p>Staginguppdateraren: <span class="pill {ukl}">{_v(ures)}</span>
     {nar}, timer {_v(upp.get('timer'))}</p>
  <p>Uppetidssond: {_v(lage.get('uppetidssond'))}</p>
  {ci_rad}
</section>
{knappar}
<section class="kort" style="margin-top:1rem;max-width:60rem">
  <h2>Miljöerna</h2>
  <ul class="lankar">{lankrader}</ul>
</section>
<p class="hamtad">Läget hämtat {_v(lage.get('hamtad'))}.</p>
<span id="tillstand" hidden
      data-vantande="{' '.join(sorted(kvar))}"
      data-aktiv="{html.escape(str(upp.get('aktiv') or ''))}"
      data-staging="{html.escape((stag.get('image') or '').split('@')[-1])}"></span>"""


SKAL = """<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>svky.se drift</title>
<style>
 body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem;
        background: #f6f6f8; color: #16161a; line-height: 1.5; }
 h1 { font-size: 1.3rem; margin: 0 0 .3rem; }
 .rutor { display: grid; gap: 1rem; grid-template-columns: 1fr; max-width: 60rem; }
 @media (min-width: 700px) { .rutor { grid-template-columns: 1fr 1fr; } }
 .kort { background: #fff; border-radius: 10px; padding: 1rem 1.2rem;
         box-shadow: 0 1px 3px rgba(0,0,0,.08); }
 .kort h2 { font-size: 1rem; margin: 0 0 .6rem; display: flex; gap: .6rem;
            align-items: center; }
 dl { display: grid; grid-template-columns: auto 1fr; gap: .3rem .8rem; margin: 0; }
 dt { color: #6b6b75; font-size: .8rem; }
 dd { margin: 0; }
 code { font-size: .82rem; word-break: break-all; }
 .pill { font-size: .72rem; padding: 2px 8px; border-radius: 999px;
         text-transform: uppercase; letter-spacing: .04em; }
 .pill.ok { background: #d8f5d8; color: #1a5c1a; }
 .pill.fel { background: #fbdcdc; color: #7a1c1c; }
 .saknas { color: #9a9aa5; font-style: italic; }
 .varning { background: #fff6e0; border-left: 4px solid #e0a020;
            padding: .7rem 1rem; border-radius: 6px; max-width: 60rem; }
 .ok-rad, .info-rad { max-width: 60rem; padding: .7rem 1rem; border-radius: 6px; }
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
 .status.arbetar { color: #24406e; }
 .status.klart { color: #1a5c1a; }
 ul.lankar { margin: 0; padding-left: 1.1rem; }
 ul.lankar li { margin: .25rem 0; }
 form.farlig button { background: #8c2b2b; }
 form.farlig { border-top: 1px solid #eee; padding-top: 1rem; }
 .hjalp { font-size: .8rem; color: #6b6b75; flex-basis: 100%; }
 label { font-size: .9rem; display: flex; gap: .4rem; align-items: center; }
 .hamtad, .status { color: #6b6b75; font-size: .8rem; max-width: 60rem; }
 .status.tappad { color: #8c2b2b; }
 #innehall.gammalt { opacity: .55; transition: opacity .2s; }
</style></head><body>
<h1>svky.se drift</h1>
<p class="status" id="status">Läget uppdateras automatiskt.</p>
<div id="innehall">__INNEHALL__</div>
<script>
// Fragmentet renderas av SERVERN. Ett JS som byggde sidan själv hade
// behövt samma regler för okänt och degradering en gång till, och två
// uppsättningar av den logiken glider isär - just i de lägen som är svåra.
const innehall = document.getElementById('innehall');
const status = document.getElementById('status');
let missar = 0;
let jobb = null;      // {digestFore, operation} medan ett jobb följs
let snabbTill = 0;    // tidpunkt då den täta pollningen slutar

function tillstand() {
  const el = document.getElementById('tillstand');
  return el ? el.dataset : {vantande: '', aktiv: '', staging: ''};
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

function foljUppJobb() {
  if (!jobb) return;
  const t = tillstand();
  const kor = t.vantande.length > 0 || ['active', 'activating'].includes(t.aktiv);
  if (kor) { sattStatus('Jobbet kör…', 'arbetar'); return; }

  if (t.staging && t.staging !== jobb.digestFore) {
    sattStatus('Klart. Staging bytte till ' + t.staging.slice(0, 19) + '…', 'klart');
  } else {
    // DET viktiga fallet. Ingen ny version är ett SVAR, inte tystnad.
    sattStatus('Klart. Ingen ny version - staging kör redan senaste bygget.', 'klart');
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
    // Att sidan står kvar oförändrad ska SYNAS. Tyst gammal data är samma
    // fel som en tom panel: den ser ut som ett svar.
    missar++;
    innehall.classList.add('gammalt');
    sattStatus('Kontakten med servern bruten (' + missar +
               ' försök). Det du ser nedan är gammalt.', 'tappad');
  }
}

document.addEventListener('submit', async (e) => {
  const form = e.target.closest('form[action^="/begar/"]');
  if (!form) return;
  e.preventDefault();
  // Knappen är upptagen tills JOBBET är klart, inte tills POST:en svarat.
  // Ett jobb som tar tio sekunder ska inte se avslutat ut efter tio
  // millisekunder - då trycker man igen.
  jobb = {digestFore: tillstand().staging,
          operation: form.action.split('/begar/')[1]};
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
  if (jobb && snabbTill && Date.now() > snabbTill) {
    sattStatus('Jobbet svarar inte. Kolla journalen på servern.', 'tappad');
    slutaArbeta();
  }
}, 2000);

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
            self._sida()
        elif vag == "/fragment":
            besked = (parse_qs(fraga).get("besked") or [""])[0]
            self._svara(fragment(besked, "varning" if besked else "").encode(),
                        "text/html; charset=utf-8")
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
