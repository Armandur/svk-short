#!/usr/bin/env python3
"""Driftytan för svky.se. Visar läget, kan ingenting.

Läser BARA lägesfilen som samlaren skriver. Ytan har därmed inga rättigheter
alls: ingen dockersocket, ingen systemctl, inga hemligheter. Att läsa vad som
körs kräver dockersocketen, och den som når den kan allt med varje container.

Knappar hör till TASK-1689 och TASK-1692 och finns inte här. De kräver en
sudoers-rad, och den ska läggas när det finns något att trycka på.

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

LAGESFIL = Path(os.environ.get("SVKY_LAGESFIL", "/var/lib/svky/lage.json"))
PORT = int(os.environ.get("SVKY_DRIFTYTA_PORT", "8002"))

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


def rendera() -> str:
    lage, fel = las_lage()
    prod, stag = lage.get("produktion") or {}, lage.get("staging") or {}
    senaste = lage.get("senaste_bygge")
    upp = lage.get("uppdaterare") or {}
    ci = lage.get("ci")

    varning = f'<p class="varning">{html.escape(fel)}</p>' if fel else ""

    # Skillnaden mellan miljöerna är den fråga man oftast kommer hit med.
    if prod.get("image") and stag.get("image"):
        if prod["image"] == stag["image"]:
            diff = '<p class="ok-rad">Produktionen kör samma version som staging.</p>'
        else:
            diff = ('<p class="info-rad">Staging ligger före produktionen. '
                    'Befordra med <code>drift/svky-promotera.sh --ja</code>.</p>')
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

    ures = upp.get("resultat")
    ukl = "ok" if ures == "success" else "fel"
    # "kör just nu" är ett svar, inte en lucka. Utan det här sa sidan okänt
    # varje gång samlaren råkade prova mitt i en körning.
    if upp.get("aktiv") in ("active", "activating"):
        nar = "kör just nu"
    else:
        nar = f"senast {_v(upp.get('avslutad'))}"

    return f"""<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>svky.se drift</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem;
        background: #f6f6f8; color: #16161a; line-height: 1.5; }}
 h1 {{ font-size: 1.3rem; margin: 0 0 1rem; }}
 .rutor {{ display: grid; gap: 1rem; grid-template-columns: 1fr; max-width: 60rem; }}
 @media (min-width: 700px) {{ .rutor {{ grid-template-columns: 1fr 1fr; }} }}
 .kort {{ background: #fff; border-radius: 10px; padding: 1rem 1.2rem;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
 .kort h2 {{ font-size: 1rem; margin: 0 0 .6rem; display: flex; gap: .6rem;
             align-items: center; }}
 dl {{ display: grid; grid-template-columns: auto 1fr; gap: .3rem .8rem; margin: 0; }}
 dt {{ color: #6b6b75; font-size: .8rem; }}
 dd {{ margin: 0; }}
 code {{ font-size: .82rem; word-break: break-all; }}
 .pill {{ font-size: .72rem; padding: 2px 8px; border-radius: 999px;
          text-transform: uppercase; letter-spacing: .04em; }}
 .pill.ok {{ background: #d8f5d8; color: #1a5c1a; }}
 .pill.fel {{ background: #fbdcdc; color: #7a1c1c; }}
 .saknas {{ color: #9a9aa5; font-style: italic; }}
 .varning {{ background: #fff6e0; border-left: 4px solid #e0a020;
             padding: .7rem 1rem; border-radius: 6px; max-width: 60rem; }}
 .ok-rad, .info-rad {{ max-width: 60rem; padding: .7rem 1rem; border-radius: 6px; }}
 .ok-rad {{ background: #eaf7ea; border-left: 4px solid #4a9a4a; }}
 .info-rad {{ background: #eaf0fb; border-left: 4px solid #4a72c8; }}
 footer {{ margin-top: 2rem; color: #6b6b75; font-size: .8rem; max-width: 60rem; }}
</style></head><body>
<h1>svky.se drift</h1>
{varning}
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
<footer>Läget hämtat {_v(lage.get('hamtad'))}. Sidan laddas om var 30:e sekund.
Ytan kan bara läsa - knappar hör till TASK-1689 och TASK-1692.</footer>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in ("", "/halsa"):
            kropp = (b'{"ok":true}' if self.path.rstrip("/") == "/halsa"
                     else rendera().encode())
            typ = ("application/json" if self.path.rstrip("/") == "/halsa"
                   else "text/html; charset=utf-8")
            self.send_response(200)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(kropp)))
            self.end_headers()
            self.wfile.write(kropp)
        else:
            self.send_error(404)

    def log_message(self, *args):
        """Tyst. Journalen ska bära driftrader, inte en accesslogg."""


if __name__ == "__main__":
    # Loopback. Vägen in är tailscale serve, precis som för staging och
    # Mailpit - en öppen port hade varit en väg förbi den gränsen.
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"driftytan lyssnar på 127.0.0.1:{PORT}", file=sys.stderr, flush=True)
    srv.serve_forever()
