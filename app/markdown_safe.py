"""Säker Markdown-rendering med HTML-sanering via nh3.

Ersätter rå markdown.markdown() + Markup() överallt i kodbasen.
Sanitering förhindrar stored XSS när body_md eller liknande fält
innehåller <script>-taggar eller onerror-attribut.
"""

import markdown
import nh3
from markupsafe import Markup

_ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "blockquote",
    "code",
    "pre",
    "a",
    "hr",
}
# href och title tillåts på <a>; rel sätts automatiskt av nh3 för externa
# länkar (noopener). Inga event-attribut (onclick m.fl.) tillåts.
_ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title"},
}


def render_markdown(md_text: str) -> Markup:
    """Omvandla Markdown-text till sanerad HTML-markup.

    Returnerar ett Markup-objekt som Jinja2 renderar utan ytterligare
    escapning. Alla HTML-taggar utanför allowlist tas bort.
    """
    raw_html = markdown.markdown(md_text or "", extensions=["nl2br"])
    clean = nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)
    return Markup(clean)
