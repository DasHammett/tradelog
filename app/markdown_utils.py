import re

import markdown as _markdown
import nh3
from markupsafe import Markup

_EXTENSIONS = ["fenced_code", "tables", "sane_lists", "nl2br"]

_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "table", "thead", "tbody", "tr", "th", "td",
}
_ATTRS = {"a": {"href", "title"}}


def render_markdown(text):
    """Convert markdown to sanitised HTML safe to output with no further escaping."""
    html = _markdown.markdown(text or "", extensions=_EXTENSIONS)
    clean = nh3.clean(
        html,
        tags=_TAGS,
        attributes=_ATTRS,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )
    return Markup(clean)


def markdown_preview(text, length=120):
    """Plain-text preview of markdown for list views."""
    if not text:
        return "No content"
    plain = nh3.clean(_markdown.markdown(text, extensions=_EXTENSIONS), tags=set())
    plain = re.sub(r"\s+", " ", plain).strip()
    # nh3 escapes entities (&amp; etc.); Jinja will re-escape, so undo that first
    import html as _html
    plain = _html.unescape(plain)
    return plain[:length] + ("\u2026" if len(plain) > length else "")
