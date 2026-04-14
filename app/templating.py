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
