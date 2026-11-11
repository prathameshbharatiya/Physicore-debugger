"""Text safety (FR-SF-*): cleaning of data text, and the separate escapers for Markdown, HTML and the JSON data block of the page.

Data text is cleaned first (control, format, bidirectional, zero-width, private-use and surrogate code points become a visible placeholder; other
space separators become a plain space), then truncated at a code-point limit, then escaped for the format it is placed in."""
from __future__ import annotations

import json
import unicodedata

TRUNCATE = 120
_REPLACE = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}
_MD_KEEP = set("/,;?'\"%^$")      # ASCII punctuation that cannot start any Markdown or autolink construct once the others are escaped


def _tokens(s: str) -> list[str]:
    out: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in _REPLACE:
            out.append(f"␣U+{ord(ch):04X}")
        elif cat == "Zs":
            out.append(" ")
        else:
            out.append(ch)
    return out


def clean_text(value: object, limit: int | None = TRUNCATE) -> str:
    """Visible, harmless text. A placeholder is never cut in half; a text over `limit` code points ends in an ellipsis (total length == limit)."""
    toks = _tokens(str(value))
    total = sum(len(t) for t in toks)
    if limit is None or total <= limit:
        return "".join(toks)
    out, n = [], 0
    for t in toks:
        if n + len(t) > limit - 1:
            break
        out.append(t)
        n += len(t)
    return "".join(out) + "…"


def md_escape(value: object, limit: int | None = TRUNCATE) -> str:
    """Cleaned text with every ASCII punctuation character that could form Markdown, HTML, an autolink or a table cell backslash-escaped."""
    s = clean_text(value, limit)
    out = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch.isascii() and not ch.isalnum() and ch != " " and ch not in _MD_KEEP:
            inner = 0 < i < n - 1 and s[i - 1].isascii() and s[i - 1].isalnum() and s[i + 1].isascii() and s[i + 1].isalnum()
            # '-', '_' and '.' between two letters or digits cannot start emphasis, a list, a rule or a heading; '.' after 'www' could start an autolink
            if inner and ch in "-_" or (inner and ch == "." and s[max(0, i - 3):i].lower() != "www"):
                out.append(ch)
            else:
                out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def html_escape(value: object, limit: int | None = TRUNCATE) -> str:
    s = clean_text(value, limit)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def _times_as_strings(o):
    if isinstance(o, dict):
        return {k: (str(v) if k.endswith("_ns") and isinstance(v, int) and not isinstance(v, bool) else _times_as_strings(v)) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_times_as_strings(v) for v in o]
    return o


def json_for_script(obj: object) -> str:
    """JSON for an inline <script type="application/json"> block: ASCII only (U+2028/9 included), no '<', '>' or '&', integer times as strings
    (a JavaScript number cannot hold a 19-digit nanosecond value)."""
    text = json.dumps(_times_as_strings(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
