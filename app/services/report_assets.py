"""Small report rendering helpers and shared CSS."""

from __future__ import annotations

from html import escape


REPORT_CSS = """
        :root {
            color-scheme: dark;
            --bg: #0f0f0f;
            --panel: #141414;
            --panel-2: #1a1a1a;
            --border: #333;
            --muted: #999;
            --text: #e0e0e0;
            --accent: #00ff88;
            --accent-dim: #00ff8844;
            --danger: #ff8888;
        }
        * { box-sizing: border-box; }
        html { scroll-behavior: smooth; }
        body {
            font-family: monospace;
            background: var(--bg);
            color: var(--text);
            padding: 1.5rem;
            max-width: 1400px;
            margin: auto;
            line-height: 1.45;
        }
        h1 { color: var(--accent); font-size: clamp(1.45rem, 4vw, 2.1rem); line-height: 1.15; }
        h2 {
            color: #cfcfcf;
            border-bottom: 1px solid var(--border);
            padding-bottom: 4px;
            margin-top: 2rem;
            font-size: clamp(1.1rem, 3vw, 1.45rem);
        }
        h3 { color: #d6d6d6; margin-top: 1.25rem; }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 2rem;
        }
        th {
            background: var(--panel-2);
            color: #aaa;
            padding: 8px 12px;
            text-align: left;
            vertical-align: top;
            position: sticky;
            top: 0;
            z-index: 1;
        }
        td {
            padding: 8px 12px;
            border-bottom: 1px solid #222;
            vertical-align: top;
        }
        tr:hover td { background: var(--panel-2); }
        a { color: var(--accent); }
        pre {
            background: var(--panel-2);
            padding: 1.5rem;
            border-radius: 6px;
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 0.85rem;
            line-height: 1.5;
            overflow-x: auto;
        }
        details {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 0.8rem 1rem;
            margin: 1rem 0 1.25rem;
        }
        details summary {
            cursor: pointer;
            color: var(--accent);
            font-weight: bold;
            list-style-position: inside;
        }
        details.section-details summary {
            font-size: 1.05rem;
        }
        details.section-details[open] {
            border-color: var(--accent-dim);
        }
        .payload {
            background: #0a1a0a;
            border: 1px solid var(--accent-dim);
            color: var(--accent);
        }
        .log {
            background: #1a0a0a;
            border: 1px solid #ff444444;
            color: var(--danger);
            font-size: 0.78rem;
            max-height: 65vh;
            overflow: auto;
        }
        .copy-btn {
            background: var(--accent);
            color: #000;
            border: none;
            padding: 8px 16px;
            cursor: pointer;
            border-radius: 6px;
            font-family: monospace;
            font-weight: bold;
            margin: 1rem 0;
        }
        .copy-btn:hover { background: #00cc66; }
        .muted { color: var(--muted); font-size: 0.9rem; }
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
            margin: 1px 0;
        }
        .action-add { background: #064e3b; color: #a7f3d0; }
        .action-hold { background: #1e3a8a; color: #bfdbfe; }
        .action-watch { background: #78350f; color: #fde68a; }
        .action-risk { background: #7f1d1d; color: #fecaca; }
        ul.compact { margin: 0; padding-left: 1.2rem; }
        .yes { color: var(--accent); }
        .no { color: var(--danger); }
        .nowrap { white-space: nowrap; }
        .urgent { background: #7f1d1d; color: #fecaca; font-weight: bold; }
        .candidate { background: #064e3b; color: #a7f3d0; }
        .quick-nav {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin: 1rem 0 1.5rem;
            padding: 0.75rem;
            background: #101820;
            border: 1px solid #1f2937;
            border-radius: 12px;
        }
        .quick-nav a {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 34px;
            padding: 6px 10px;
            border: 1px solid var(--accent-dim);
            border-radius: 999px;
            text-decoration: none;
            background: #0d1f18;
            color: var(--accent);
            font-size: 0.86rem;
        }
        .quick-nav a:hover { background: #0f2a20; }
        .top-note {
            background: #101820;
            border: 1px solid #1f2937;
            border-radius: 12px;
            padding: 0.9rem 1rem;
        }
        .section-card {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1rem;
            margin: 1.25rem 0;
        }
        .section-card h2:first-child { margin-top: 0; }
        .table-scroll {
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            border-radius: 8px;
        }
        .debug-section {
            margin-top: 2rem;
            border-top: 1px solid var(--border);
            padding-top: 1rem;
        }
        @media (max-width: 760px) {
            body {
                padding: 0.85rem;
                font-size: 14px;
                max-width: 100%;
            }
            h1 { margin-top: 0.5rem; }
            h2 { margin-top: 1.25rem; }
            .top-note { padding: 0.75rem; }
            .quick-nav {
                position: sticky;
                top: 0;
                z-index: 5;
                gap: 0.35rem;
                margin: 0.75rem -0.25rem 1rem;
                padding: 0.55rem;
                max-height: 42vh;
                overflow-y: auto;
            }
            .quick-nav a {
                flex: 1 1 calc(50% - 0.35rem);
                font-size: 0.8rem;
                padding: 6px 8px;
            }
            table {
                display: block;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                white-space: normal;
                border: 1px solid #222;
                border-radius: 8px;
                margin-bottom: 1.1rem;
            }
            th, td {
                padding: 7px 8px;
                min-width: 112px;
            }
            th:first-child, td:first-child {
                min-width: 88px;
            }
            tr:hover td { background: transparent; }
            pre {
                padding: 0.85rem;
                font-size: 0.76rem;
                max-height: 60vh;
            }
            details {
                padding: 0.65rem 0.75rem;
                margin: 0.75rem 0 1rem;
            }
            .copy-btn { width: 100%; min-height: 42px; }
            .pill { white-space: normal; }
            .muted { font-size: 0.82rem; }
        }
        @media (max-width: 420px) {
            .quick-nav a { flex-basis: 100%; }
            th, td { min-width: 105px; }
        }
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
