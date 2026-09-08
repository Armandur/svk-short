import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

templates = Jinja2Templates(directory="app/templates")

mail_env = Environment(
    loader=FileSystemLoader("app/templates/mail"),
    autoescape=True,
)

_STHLM = ZoneInfo("Europe/Stockholm")


def sthlm_datetime(value) -> str:
    """Konverterar ett UTC-datum (str eller datetime) till Europe/Stockholm-zon.

    Används som Jinja2-filter: {{ link.created_at | sthlm }}
    Returnerar tom sträng om value är falsy.
    """
    if not value:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(_STHLM).strftime("%Y-%m-%d %H:%M")


templates.env.filters["sthlm"] = sthlm_datetime


def _allowed_domains() -> list[str]:
    """Jinja-global {{ allowed_domains() }} - lista över godkända måldomäner.

    Importeras lazy för att undvika importcykel vid modulladdning.
    """
    from app.domains import get_allowed_domains

    return [r["domain"] for r in get_allowed_domains()]


templates.env.globals["allowed_domains"] = _allowed_domains


def _notisbanner() -> dict | None:
    """Jinja-global {{ notisbanner() }} - adminens meddelande till alla besökare.

    Tom text betyder ingen banner. Ett separat av-och-på-fält hade skapat
    ett läge där texten finns men inget syns, och admin inte kan se varför.

    Körs på VARJE renderad sida, felsidorna inräknade. Ett fel här får
    därför aldrig fälla svaret - då hade en trasig notisuppslagning blivit
    det som gör felsidan oläsbar, precis när den behövs.
    """
    from app.database import get_db
    from app.markdown_safe import render_markdown

    try:
        with get_db() as db:
            rader = db.execute(
                "SELECT key, value FROM site_settings WHERE key IN "
                "('notice_content', 'notice_level')"
            ).fetchall()
    except Exception:
        logging.exception("Kunde inte läsa notisbannern")
        return None

    varden = {r["key"]: r["value"] for r in rader}
    text = (varden.get("notice_content") or "").strip()
    if not text:
        return None
    niva = varden.get("notice_level") or "info"
    return {"html": render_markdown(text), "niva": niva if niva in NOTISNIVAER else "info"}


# Nyckeln är CSS-klassen, värdet det admin väljer mellan. Klasserna finns
# redan i style.css - en egen uppsättning för bannern hade betytt två
# ställen att hålla i takt.
NOTISNIVAER = {"info": "Information", "varning": "Varning"}

templates.env.globals["notisbanner"] = _notisbanner
