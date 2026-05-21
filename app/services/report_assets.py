"""Small report rendering helpers and shared CSS."""

from __future__ import annotations

from html import escape


REPORT_CSS = """
        body {
            font-family: monospace;
            background: #0f0f0f;
            color: #e0e0e0;
            padding: 2rem;
            max-width: 1400px;
            margin: auto;
        }
        h1 { color: #00ff88; }
        h2 {
            color: #888;
            border-bottom: 1px solid #333;
            padding-bottom: 4px;
            margin-top: 2rem;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 2rem;
        }
        th {
            background: #1a1a1a;
            color: #aaa;
            padding: 8px 12px;
            text-align: left;
            vertical-align: top;
        }
        td {
            padding: 8px 12px;
            border-bottom: 1px solid #222;
            vertical-align: top;
        }
        tr:hover td { background: #1a1a1a; }
        a { color: #00ff88; }
        pre {
            background: #1a1a1a;
            padding: 1.5rem;
            border-radius: 6px;
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 0.85rem;
            line-height: 1.5;
        }
        details {
            background: #141414;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 0.8rem 1rem;
            margin: 1rem 0 2rem;
        }
        details summary {
            cursor: pointer;
            color: #00ff88;
            font-weight: bold;
        }
        .payload {
            background: #0a1a0a;
            border: 1px solid #00ff8844;
            color: #00ff88;
        }
        .log {
            background: #1a0a0a;
            border: 1px solid #ff444444;
            color: #ff8888;
            font-size: 0.78rem;
        }
        .copy-btn {
            background: #00ff88;
            color: #000;
            border: none;
            padding: 8px 16px;
            cursor: pointer;
            border-radius: 4px;
            font-family: monospace;
            font-weight: bold;
            margin: 1rem 0;
        }
        .copy-btn:hover { background: #00cc66; }
        .muted { color: #999; font-size: 0.9rem; }
        .score { font-weight: bold; }
        .empty { color: #777; font-style: italic; }
        .pill {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            background: #1f2937;
            color: #e5e7eb;
            font-size: 0.78rem;
            white-space: nowrap;
        }
        .action-add { background: #064e3b; color: #a7f3d0; }
        .action-hold { background: #1e3a8a; color: #bfdbfe; }
        .action-watch { background: #78350f; color: #fde68a; }
        .action-risk { background: #7f1d1d; color: #fecaca; }
        ul.compact { margin: 0; padding-left: 1.2rem; }
        .yes { color: #00ff88; }
        .no { color: #ff8888; }
        .nowrap { white-space: nowrap; }
        .urgent { background: #7f1d1d; color: #fecaca; font-weight: bold; }
        .candidate { background: #064e3b; color: #a7f3d0; }
"""


def collapsible_pre(title: str, content: str, element_id: str | None = None, css_class: str = "") -> str:
    safe_title = escape(title)
    safe_content = escape(content or "")
    id_attr = f' id="{escape(element_id)}"' if element_id else ""
    cls = f' class="{escape(css_class)}"' if css_class else ""
    return f"""
    <details>
        <summary>{safe_title}</summary>
        <pre{id_attr}{cls}>{safe_content}</pre>
    </details>"""
