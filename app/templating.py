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
    """Jinja-global {{ allowed_domains() }} — lista över godkända måldomäner.

    Importeras lazy för att undvika importcykel vid modulladdning.
    """
    from app.domains import get_allowed_domains

    return [r["domain"] for r in get_allowed_domains()]


templates.env.globals["allowed_domains"] = _allowed_domains
