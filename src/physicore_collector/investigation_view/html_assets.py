"""The CSS and the JavaScript of the page, as plain strings inlined into the one HTML file (D12: no third-party code, nothing fetched)."""
from __future__ import annotations

import base64
import hashlib

CSS = r"""
:root{color-scheme:light dark;--bg:#fbfbf9;--fg:#1c1d1f;--mut:#555a61;--line:#c9ccd1;--card:#ffffff;--acc:#0b5cad;--acc-bg:#e4effa;--warn-bg:#fff3d6;--warn:#7a4b00;--bar:#2b6cb0;--sel:#c2410c;--focus:#0b5cad}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#15171a;--fg:#e6e7e9;--mut:#a3a8af;--line:#3a3e44;--card:#1c1f23;--acc:#6db1f0;--acc-bg:#1d2a38;--warn-bg:#3a2f12;--warn:#f1c76b;--bar:#6db1f0;--sel:#fb923c;--focus:#6db1f0}}
:root[data-theme="dark"]{--bg:#15171a;--fg:#e6e7e9;--mut:#a3a8af;--line:#3a3e44;--card:#1c1f23;--acc:#6db1f0;--acc-bg:#1d2a38;--warn-bg:#3a2f12;--warn:#f1c76b;--bar:#6db1f0;--sel:#fb923c;--focus:#6db1f0}
*{box-sizing:border-box}
body{margin:0;overflow-wrap:anywhere;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--acc)}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,tr:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.skip{position:absolute;left:-999px;top:0;background:var(--card);padding:8px 12px;z-index:10}.skip:focus{left:8px;top:8px}
header.top,main,footer{max-width:1180px;margin:0 auto;padding:0 16px}
header.top{padding-top:20px}
h1{font-size:1.6rem;margin:.2em 0}h2{font-size:1.25rem;margin:0;padding:.2em 0}h3{font-size:1.05rem;margin:1.2em 0 .4em}
nav.sections{display:flex;flex-wrap:wrap;gap:6px 14px;margin:12px 0;padding:8px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
section{margin:18px 0;padding:10px 14px;background:var(--card);border:1px solid var(--line);border-radius:8px}
details>summary{cursor:pointer;list-style:revert}h2.inl{display:inline}
p.note{color:var(--mut);margin:.4em 0}
p.warn{background:var(--warn-bg);color:var(--warn);padding:6px 10px;border-radius:6px}
.tablewrap{overflow-x:auto;margin:.5em 0}
table{border-collapse:collapse;width:100%;font-size:.88rem}
caption{text-align:left;font-weight:600;padding:4px 0;color:var(--mut)}
th,td{border:1px solid var(--line);padding:3px 7px;text-align:left;vertical-align:top;overflow-wrap:break-word}
th{background:var(--acc-bg)}
tr.sel td{outline:2px solid var(--sel);outline-offset:-2px;background:var(--acc-bg)}
tr.hl td{background:var(--acc-bg)}
.controls{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:end;margin:.5em 0}
.controls label{display:flex;flex-direction:column;font-size:.8rem;color:var(--mut)}
.controls select,.controls input,button{font:inherit;padding:3px 6px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:5px}
button{cursor:pointer}
.pager{display:flex;gap:8px;align-items:center;margin:.4em 0}
aside#detail{position:sticky;bottom:0;margin-top:10px;padding:10px 14px;background:var(--card);border:2px solid var(--acc);border-radius:8px;max-height:45vh;overflow:auto}
aside#detail dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:.4em 0}aside#detail dt{color:var(--mut)}aside#detail dd{margin:0;overflow-wrap:anywhere}
svg.timeline,svg.diagram{width:100%;height:auto;background:var(--bg);border:1px solid var(--line);border-radius:6px}
svg text{fill:var(--fg);font:11px system-ui,sans-serif}svg .axis{stroke:var(--mut);stroke-width:1}svg .tick{stroke:var(--mut)}
svg .bar{fill:var(--bar);stroke:var(--fg);stroke-width:1}
svg .bar.sel,svg .bar.hl{stroke:var(--sel);stroke-width:3}
svg .bar.hl{fill:var(--acc)}
svg .arrow{stroke:var(--fg);stroke-width:1.5;fill:none;marker-end:url(#head)}
svg .arrow.hl{stroke:var(--sel);stroke-width:3}
svg .l-outside_declared_window{stroke-dasharray:6 3}svg .l-inconsistent_order{stroke-dasharray:2 3}svg .l-undetermined{stroke-dasharray:8 3 2 3}
svg .node{fill:var(--card);stroke:var(--fg)}svg .edge{stroke:var(--mut);stroke-width:1.2;fill:none}
svg marker path{fill:var(--fg)}
.legend svg{vertical-align:middle;width:70px;height:12px}
ul.plain{margin:.3em 0;padding-left:1.2em}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
.zoomctl{display:flex;gap:6px;flex-wrap:wrap;margin:.3em 0}
@media print{:root{--bg:#fff;--fg:#000;--card:#fff;--mut:#333;--line:#888}.skip,nav.sections,.controls,.pager,.zoomctl,aside#detail{display:none!important}details:not([open])>*:not(summary){display:block}section{break-inside:avoid-page;border:1px solid #888}a{color:#000}}
"""


def csp_hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


try:
    from .js_source import JS
except ImportError:           # Step 6 before Step 7
    JS = ""
