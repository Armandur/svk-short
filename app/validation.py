import re
from urllib.parse import urlparse

from app.config import RESERVED_CODES


def validate_target_url(url: str) -> str | None:
    """Returns error message or None if OK."""
    try:
        p = urlparse(url)
    except Exception:
        return "Ogiltig URL."

    if p.scheme != "https":
        return "URL:en måste börja med https://."

    host = p.netloc.lower()
    if host != "svenskakyrkan.se" and host != "www.svenskakyrkan.se" and not host.endswith(".svenskakyrkan.se"):
        return "Endast URL:er under svenskakyrkan.se är tillåtna."

    if p.query:
        return "URL:en får inte innehålla frågeparametrar (?...)."

    if p.fragment:
        return "URL:en får inte innehålla fragment (#...)."

    path_parts = [seg for seg in p.path.split("/") if seg]
    for seg in path_parts:
        if not re.match(r"^[a-zA-Z0-9\-_]+$", seg):
            return f"Ogiltigt sökvägssegment: '{seg}'. Endast bokstäver, siffror, - och _ tillåts."

    return None


def validate_code(code: str) -> str | None:
    """Returns error message or None if OK."""
    if len(code) < 2 or len(code) > 60:
        return "Koden måste vara 2–60 tecken lång."

    if not re.match(r"^[a-z0-9-]+$", code):
        return "Koden får bara innehålla gemener (a–z), siffror (0–9) och bindestreck (-)."

    if code.startswith("-") or code.endswith("-"):
        return "Koden får inte börja eller sluta med ett bindestreck."

    if "--" in code:
        return "Koden får inte innehålla två bindestreck i rad."

    if code in RESERVED_CODES:
        return f"'{code}' är ett reserverat ord och kan inte användas som kod."

    return None
