#!/usr/bin/env python3
"""Dynamic Articles Aggregator (DAA) — markdown articles via obfuscated paths."""
from __future__ import annotations
import os
import sqlite3
import hashlib
import hmac
import secrets
import tempfile
import markdown
from datetime import datetime
from html import escape
from pathlib import Path
from flask import Flask, abort, request, send_file, Response, jsonify
from flask_limiter import Limiter

CONTENT_DIR = os.environ.get('CONTENT_DIR', '/content')
DB_PATH = os.path.join(CONTENT_DIR, '.mappings.db')
INDEX_FILE = os.path.join(CONTENT_DIR, '.index_secret')

app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024


# ── Rate limiting ─────────────────────────────────────────────────────────────

def _real_ip() -> str:
    xff = request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
    return xff or request.headers.get('X-Real-IP', '') or request.remote_addr


limiter = Limiter(
    key_func=_real_ip,
    app=app,
    default_limits=["240 per minute", "8 per second"],
    storage_uri="memory://",
    strategy="fixed-window",
)

_CSP = (
    "default-src 'self'; "
    "style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none';"
)


@app.after_request
def _security_headers(response: Response) -> Response:
    h = response.headers
    h['X-Content-Type-Options'] = 'nosniff'
    h['X-Frame-Options'] = 'DENY'
    h['Referrer-Policy'] = 'no-referrer'
    h['X-Robots-Tag'] = 'noindex, nofollow, noarchive, nosnippet'
    h['Content-Security-Policy'] = _CSP
    h['Permissions-Policy'] = 'geolocation=(), camera=(), microphone=()'
    h.pop('Server', None)
    if request.path.startswith('/api/'):
        h['Cache-Control'] = 'no-store'
    return response


@app.errorhandler(404)
def _e404(_): return Response(status=404)

@app.errorhandler(429)
def _e429(_): return Response(status=429)

@app.errorhandler(405)
def _e405(_): return Response(status=404)


# ── Database ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    os.makedirs(CONTENT_DIR, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS mappings (
                hash     TEXT PRIMARY KEY,
                rel_path TEXT NOT NULL UNIQUE
            )
        """)


def get_index_hash() -> str:
    if os.path.exists(INDEX_FILE):
        val = Path(INDEX_FILE).read_text().strip()
        if len(val) >= 20:
            return val
    val = secrets.token_hex(20)
    Path(INDEX_FILE).write_text(val)
    return val


def _article_hash(rel_path: str) -> str:
    return hashlib.sha256(rel_path.encode()).hexdigest()[:32]


def register(rel_path: str) -> str:
    h = _article_hash(rel_path)
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT OR IGNORE INTO mappings (hash, rel_path) VALUES (?, ?)",
            (h, rel_path),
        )
    return h


def lookup(h: str) -> str | None:
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT rel_path FROM mappings WHERE hash = ?", (h,)
        ).fetchone()
    return row[0] if row else None


# ── Filesystem ────────────────────────────────────────────────────────────────

def scan() -> list[dict]:
    categories = []
    try:
        top_entries = sorted(os.listdir(CONTENT_DIR))
    except OSError:
        return categories
    for entry in top_entries:
        if entry.startswith('.'):
            continue
        cat_path = os.path.join(CONTENT_DIR, entry)
        if not os.path.isdir(cat_path):
            continue
        articles = []
        try:
            files = sorted(os.listdir(cat_path))
        except OSError:
            continue
        for fname in files:
            if not fname.lower().endswith('.md'):
                continue
            fpath = os.path.join(cat_path, fname)
            rel = f"{entry}/{fname}"
            h = register(rel)
            try:
                ts = os.stat(fpath).st_mtime
            except OSError:
                continue
            dt = datetime.fromtimestamp(ts)
            articles.append({
                'path': rel,
                'title': _make_title(fname),
                'hash': h,
                'ts': ts,
                'date_disp': dt.strftime('%d %b %Y'),
                'date_iso': dt.strftime('%Y-%m-%d'),
            })
        categories.append({'name': entry, 'articles': articles})
    return categories


def _make_title(filename: str) -> str:
    return filename[:-3].replace('-', ' ').replace('_', ' ').title()


def _safe_path(rel_path: str) -> str | None:
    parts = Path(rel_path).parts
    if any(p.startswith('.') for p in parts):
        return None
    if not rel_path.lower().endswith('.md'):
        return None
    base = os.path.realpath(CONTENT_DIR)
    resolved = os.path.realpath(os.path.join(CONTENT_DIR, rel_path))
    if not resolved.startswith(base + os.sep):
        return None
    return resolved


def _managed_article(rel_path: str) -> Path | None:
    """Only a regular Markdown file one level below a real category directory."""
    parts = rel_path.split('/')
    if (len(parts) != 2 or any(not p or p in ('.', '..') or p.startswith('.')
                              or '\\' in p or '\x00' in p for p in parts)
            or not parts[1].lower().endswith('.md')):
        return None
    base = Path(CONTENT_DIR)
    category = base / parts[0]
    article = category / parts[1]
    if category.is_symlink() or article.is_symlink():
        return None
    if category.exists() and not category.is_dir():
        return None
    if article.exists() and not article.is_file():
        return None
    return article


def _api_authorized() -> bool:
    token = os.environ.get('DAA_API_TOKEN', '')
    if len(token) < 32:
        abort(503)
    supplied = request.headers.get('Authorization', '')
    if not supplied.startswith('Bearer ') or not hmac.compare_digest(
        supplied[7:].encode(), token.encode()
    ):
        abort(401)
    return True


@app.route('/api/v1/articles', methods=['GET'])
def api_list_articles():
    _api_authorized()
    articles = []
    for category in scan():
        for item in category['articles']:
            articles.append({'path': item['path'],
                             'hash': item['hash']})
    return jsonify({'articles': articles})


@app.route('/api/v1/articles/<path:rel_path>', methods=['GET', 'PUT', 'DELETE'])
def api_article(rel_path: str):
    _api_authorized()
    article = _managed_article(rel_path)
    if article is None:
        abort(400)
    if request.method == 'GET':
        if not article.is_file():
            abort(404)
        return Response(article.read_text(encoding='utf-8'), mimetype='text/markdown')
    if request.method == 'DELETE':
        if not article.is_file():
            abort(404)
        article.unlink()
        return Response(status=204)
    if request.mimetype != 'text/markdown':
        abort(415)
    try:
        body = request.get_data().decode('utf-8')
    except UnicodeDecodeError:
        abort(400)
    existed = article.is_file()
    article.parent.mkdir(mode=0o750, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=article.parent, prefix='.daa-',
                                         delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(body)
        os.chmod(tmp_path, 0o640)
        os.replace(tmp_path, article)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
    article_hash = register(rel_path)
    return jsonify({'path': rel_path, 'hash': article_hash}), 200 if existed else 201


# ── Markdown ──────────────────────────────────────────────────────────────────

def md_to_html(src: str) -> str:
    md = markdown.Markdown(
        extensions=[
            'fenced_code', 'codehilite', 'tables', 'toc',
            'nl2br', 'sane_lists', 'attr_list', 'footnotes',
        ],
        extension_configs={
            'codehilite': {'css_class': 'highlight', 'guess_lang': True},
        },
    )
    return md.convert(src)


# ── Themes ────────────────────────────────────────────────────────────────────
# Each entry: (id, display_name, type)
# CSS variables are defined below in _THEME_OVERRIDES_CSS.
# 'dark' themes use monokai code highlighting; 'light' themes use friendly.

_THEMES = [
    ('tokyo-night',    'Tokyo Night',    'dark'),
    ('dracula',        'Dracula',        'dark'),
    ('nord',           'Nord',           'dark'),
    ('monokai',        'Monokai',        'dark'),
    ('solarized-dark', 'Solarized Dark', 'dark'),
    ('cyberpunk',      'Cyberpunk',      'dark'),
    ('matrix',         'Matrix',         'dark'),
    ('synthwave',      "Synthwave '84",  'dark'),
    ('solarized-light','Solarized Light','light'),
    ('github-light',   'GitHub Light',   'light'),
]

_LIGHT_IDS = {tid for tid, _, t in _THEMES if t == 'light'}

# CSS variable overrides for each theme.
# :root (Tokyo Night defaults) are defined inside _BASE_CSS.
_THEME_OVERRIDES_CSS = """
[data-theme="dracula"] {
    --bg-dark:#191a21; --bg:#282a36; --bg-surface:#2e303e; --bg-lift:#383b4a;
    --fg:#f8f8f2; --fg-muted:#c0bcd6; --fg-faint:#6272a4;
    --accent:#bd93f9; --accent-h:#caa9fa; --purple:#ff79c6;
    --green:#50fa7b; --yellow:#f1fa8c; --red:#ff5555;
    --border:#383b4a; --code-bg:#21222c;
}
[data-theme="nord"] {
    --bg-dark:#191d24; --bg:#2e3440; --bg-surface:#3b4252; --bg-lift:#434c5e;
    --fg:#eceff4; --fg-muted:#d8dee9; --fg-faint:#7b8fa6;
    --accent:#88c0d0; --accent-h:#9ecfdf; --purple:#b48ead;
    --green:#a3be8c; --yellow:#ebcb8b; --red:#bf616a;
    --border:#434c5e; --code-bg:#272c36;
}
[data-theme="monokai"] {
    --bg-dark:#19191a; --bg:#272822; --bg-surface:#2d2e2a; --bg-lift:#3e3d32;
    --fg:#f8f8f2; --fg-muted:#cfcfc2; --fg-faint:#75715e;
    --accent:#66d9e8; --accent-h:#80e8f5; --purple:#ae81ff;
    --green:#a6e22e; --yellow:#e6db74; --red:#f92672;
    --border:#3e3d32; --code-bg:#1e1f1c;
}
[data-theme="solarized-dark"] {
    --bg-dark:#001e27; --bg:#002b36; --bg-surface:#073642; --bg-lift:#0d4052;
    --fg:#839496; --fg-muted:#657b83; --fg-faint:#4a6672;
    --accent:#268bd2; --accent-h:#3a9fe6; --purple:#6c71c4;
    --green:#859900; --yellow:#b58900; --red:#dc322f;
    --border:#0d4052; --code-bg:#01323c;
}
[data-theme="cyberpunk"] {
    --bg-dark:#060010; --bg:#0d0018; --bg-surface:#120022; --bg-lift:#1e003a;
    --fg:#e8e8ff; --fg-muted:#b4b4ff; --fg-faint:#6060aa;
    --accent:#00ffff; --accent-h:#44ffff; --purple:#ff00ff;
    --green:#00ff88; --yellow:#ffff00; --red:#ff0055;
    --border:#2a006a; --code-bg:#08000f;
}
[data-theme="matrix"] {
    --bg-dark:#000000; --bg:#001100; --bg-surface:#001a00; --bg-lift:#002800;
    --fg:#00ff41; --fg-muted:#00cc33; --fg-faint:#005514;
    --accent:#00ff41; --accent-h:#44ff77; --purple:#00cc88;
    --green:#00ff41; --yellow:#aaff00; --red:#ff4141;
    --border:#003300; --code-bg:#000a00;
}
[data-theme="synthwave"] {
    --bg-dark:#0a0010; --bg:#1b0533; --bg-surface:#220540; --bg-lift:#2e0a5a;
    --fg:#ff7edb; --fg-muted:#e896e8; --fg-faint:#7722aa;
    --accent:#f92aad; --accent-h:#ff5dc8; --purple:#c89dff;
    --green:#72f1b8; --yellow:#fede5d; --red:#fe4450;
    --border:#3c0880; --code-bg:#120228;
}
[data-theme="solarized-light"] {
    --bg-dark:#d4cdb7; --bg:#fdf6e3; --bg-surface:#eee8d5; --bg-lift:#e4ddc8;
    --fg:#4a5568; --fg-muted:#657b83; --fg-faint:#93a1a1;
    --accent:#268bd2; --accent-h:#1a7bbf; --purple:#6c71c4;
    --green:#859900; --yellow:#b58900; --red:#dc322f;
    --border:#c9c2ae; --code-bg:#eee8d5;
}
[data-theme="github-light"] {
    --bg-dark:#e8ecf0; --bg:#ffffff; --bg-surface:#f6f8fa; --bg-lift:#eaeef2;
    --fg:#1f2328; --fg-muted:#656d76; --fg-faint:#9198a1;
    --accent:#0969da; --accent-h:#0757c0; --purple:#8250df;
    --green:#1a7f37; --yellow:#7d4e00; --red:#cf222e;
    --border:#d0d7de; --code-bg:#f6f8fa;
}
"""

# Pygments code highlighting — monokai for dark themes, friendly for light
try:
    from pygments.formatters import HtmlFormatter
    _dark_pygs  = HtmlFormatter(style='monokai').get_style_defs('.highlight')
    _light_sel  = ', '.join(f'[data-theme="{t}"] .highlight' for t in _LIGHT_IDS)
    _light_pygs = HtmlFormatter(style='friendly').get_style_defs(_light_sel) if _light_sel else ''
    PYGS_CSS = _dark_pygs + '\n' + _light_pygs
except ImportError:
    PYGS_CSS = ''


# ── Shared JS ─────────────────────────────────────────────────────────────────

_THEME_JS = r"""
function _daaTheme() {
    var m = document.cookie.match(/daa-theme=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : 'tokyo-night';
}
function applyTheme(name) {
    document.documentElement.setAttribute('data-theme', name);
    document.cookie = 'daa-theme=' + encodeURIComponent(name) +
        ';path=/;max-age=31536000;SameSite=Strict';
    var sel = document.getElementById('theme-select');
    if (sel) sel.value = name;
}
document.addEventListener('DOMContentLoaded', function() {
    applyTheme(_daaTheme());
});
"""

# Inline script for <head> — applies theme before first paint to avoid flash
_THEME_EARLY = (
    '<script>(function(){var m=document.cookie.match(/daa-theme=([^;]+)/);'
    'if(m)document.documentElement.setAttribute("data-theme",decodeURIComponent(m[1]));'
    '})();</script>'
)


def _theme_select_html() -> str:
    opts = ''.join(
        f'<option value="{tid}">{name}</option>'
        for tid, name, _ in _THEMES
    )
    return (
        '<select id="theme-select" onchange="applyTheme(this.value)" '
        'title="Choose theme" aria-label="Theme">'
        + opts + '</select>'
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def root():
    return Response(status=204)


@app.route('/<string:h>', methods=['GET'])
def dispatch(h: str):
    idx = get_index_hash()
    if h == idx:
        return Response(_render_index(scan()), mimetype='text/html')
    rel_path = lookup(h)
    if rel_path is None:
        abort(404)
    fpath = _safe_path(rel_path)
    if fpath is None or not os.path.isfile(fpath):
        abort(404)
    parts = rel_path.split('/', 1)
    cat   = parts[0] if len(parts) == 2 else ''
    fname = parts[1] if len(parts) == 2 else rel_path
    src   = Path(fpath).read_text(encoding='utf-8')
    ts    = os.stat(fpath).st_mtime
    date  = datetime.fromtimestamp(ts).strftime('%d %b %Y')
    return Response(
        _render_article(_make_title(fname), cat, date, md_to_html(src), h),
        mimetype='text/html',
    )


@app.route('/<string:h>/download', methods=['GET'])
def download(h: str):
    rel_path = lookup(h)
    if rel_path is None:
        abort(404)
    fpath = _safe_path(rel_path)
    if fpath is None or not os.path.isfile(fpath):
        abort(404)
    return send_file(fpath, as_attachment=True, download_name=os.path.basename(fpath))


# ── CSS ───────────────────────────────────────────────────────────────────────

_BASE_CSS = """
:root {
    --bg-dark:#13141c; --bg:#1a1b26; --bg-surface:#24283b; --bg-lift:#292e42;
    --fg:#c0caf5; --fg-muted:#9aa5ce; --fg-faint:#565f89;
    --accent:#7aa2f7; --accent-h:#89b4fa; --purple:#bb9af7;
    --green:#9ece6a; --yellow:#e0af68; --red:#f7768e;
    --border:#292e42; --code-bg:#1f2335;
    --radius:8px; --radius-sm:4px;
}
""" + _THEME_OVERRIDES_CSS + """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 16px; scroll-behavior: smooth; }
body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg-dark);
    color: var(--fg);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    line-height: 1.65;
    transition: background 0.2s, color 0.2s;
}
a { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-h); text-decoration: underline; }

/* Theme selector */
#theme-select {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--fg-muted);
    font-size: 0.78rem;
    padding: 4px 8px;
    cursor: pointer;
    transition: border-color 0.15s;
    max-width: 150px;
}
#theme-select:focus { outline: none; border-color: var(--accent); }

/* Footer */
footer {
    margin-top: auto;
    padding: 1.5rem 2rem;
    text-align: center;
    font-size: 0.75rem;
    color: var(--fg-faint);
    border-top: 1px solid var(--border);
}
"""

_INDEX_CSS = _BASE_CSS + """
header {
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    padding: 1rem 2rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
}
.header-left .site-title {
    font-size: 1.4rem;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: -0.5px;
}
.header-left .site-sub {
    font-size: 0.78rem;
    color: var(--fg-faint);
    margin-top: 1px;
}
.header-right { display: flex; align-items: center; gap: 0.5rem; }
.header-right label { font-size: 0.75rem; color: var(--fg-faint); }

main { flex: 1; max-width: 980px; width: 100%; margin: 0 auto; padding: 1.5rem 1.5rem 3rem; }

/* Controls */
.controls {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.85rem 1.25rem;
    margin-bottom: 1.5rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.65rem 1.25rem;
    align-items: center;
}
.ctrl-group {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
}
.ctrl-sep {
    width: 1px;
    height: 24px;
    background: var(--border);
    flex-shrink: 0;
}
.ctrl-label {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--fg-faint);
}
.radio-label {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.85rem;
    color: var(--fg-muted);
    cursor: pointer;
    padding: 3px 8px;
    border-radius: var(--radius-sm);
    transition: background 0.15s;
}
.radio-label:hover { background: var(--bg-lift); color: var(--fg); }
.radio-label input { accent-color: var(--accent); cursor: pointer; }
.ctrl-select {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--fg-muted);
    font-size: 0.8rem;
    padding: 4px 8px;
    cursor: pointer;
    transition: border-color 0.15s;
}
.ctrl-select:focus { outline: none; border-color: var(--accent); }
.ctrl-date {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--fg-muted);
    font-size: 0.8rem;
    padding: 4px 8px;
    cursor: pointer;
    transition: border-color 0.15s;
    width: 130px;
}
.ctrl-date:focus { outline: none; border-color: var(--accent); }
.ctrl-date::-webkit-calendar-picker-indicator { filter: invert(0.6); cursor: pointer; }
.pbtn {
    background: none;
    border: 1px solid var(--border);
    border-radius: 20px;
    color: var(--fg-muted);
    cursor: pointer;
    font-size: 0.78rem;
    padding: 3px 12px;
    transition: all 0.15s;
}
.pbtn:hover { border-color: var(--accent); color: var(--accent); }
.pbtn.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #0d0e11;
    font-weight: 600;
}

/* Stats bar */
.stats-bar { font-size: 0.78rem; color: var(--fg-faint); margin-bottom: 1rem; }
#visible-count { color: var(--fg-muted); font-weight: 600; }

/* Categories */
.categories { display: flex; flex-direction: column; gap: 0.85rem; }
.category {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    transition: border-color 0.2s;
}
.category:hover { border-color: var(--bg-lift); }
.cat-header {
    width: 100%;
    background: none;
    border: none;
    padding: 0.8rem 1.25rem;
    display: flex;
    align-items: center;
    gap: 0.65rem;
    cursor: pointer;
    text-align: left;
    transition: background 0.15s;
    color: var(--fg);
}
.cat-header:hover { background: var(--bg-lift); }
.cat-arrow {
    color: var(--accent);
    font-size: 0.9rem;
    transition: transform 0.2s;
    width: 14px;
    text-align: center;
    flex-shrink: 0;
    display: inline-block;
}
.cat-header.collapsed .cat-arrow { transform: rotate(-90deg); }
.cat-name { font-size: 0.95rem; font-weight: 600; flex: 1; }
.cat-count {
    font-size: 0.72rem;
    background: var(--bg-lift);
    color: var(--fg-faint);
    border-radius: 20px;
    padding: 1px 10px;
    font-weight: 500;
}
.cat-articles { border-top: 1px solid var(--border); padding: 0.35rem 0; }
.cat-articles.hidden { display: none; }
.article-item { border-bottom: 1px solid rgba(41,46,66,0.4); }
.article-item:last-child { border-bottom: none; }
.article-link {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.55rem 1.25rem 0.55rem 2.4rem;
    transition: background 0.12s;
    gap: 1rem;
    color: var(--fg);
    text-decoration: none;
}
.article-link:hover { background: var(--bg-lift); color: var(--accent); text-decoration: none; }
.article-title {
    font-size: 0.875rem;
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.article-date {
    font-size: 0.72rem;
    color: var(--fg-faint);
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
}
.no-results { text-align: center; padding: 3rem 1rem; color: var(--fg-faint); font-size: 0.9rem; }
@media (max-width: 640px) {
    header { padding: 0.85rem 1rem; }
    main { padding: 1rem 1rem 2rem; }
    .ctrl-sep { display: none; }
    .ctrl-date { width: 110px; }
}
"""

_ARTICLE_CSS = _BASE_CSS + """
.page-wrap {
    flex: 1;
    max-width: 840px;
    width: 100%;
    margin: 0 auto;
    padding: 2rem 2rem 4rem;
}
.article-meta-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.75rem;
    flex-wrap: wrap;
    gap: 0.65rem;
}
.meta-left { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.meta-right { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.meta-cat {
    background: var(--bg-lift);
    color: var(--purple);
    border-radius: 20px;
    padding: 2px 12px;
    font-size: 0.75rem;
    font-weight: 500;
}
.meta-date { font-size: 0.78rem; color: var(--fg-faint); }
.dl-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--fg-muted);
    font-size: 0.78rem;
    padding: 5px 12px;
    text-decoration: none;
    transition: all 0.15s;
}
.dl-btn:hover { border-color: var(--accent); color: var(--accent); text-decoration: none; }

/* Article typography */
article {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 2.5rem 3rem;
    line-height: 1.85;
}
article h1 {
    font-size: 1.9rem; font-weight: 700; color: var(--fg);
    margin-bottom: 0.4rem; line-height: 1.25; letter-spacing: -0.5px;
}
article h2 {
    font-size: 1.35rem; font-weight: 600; color: var(--fg);
    margin: 1.75rem 0 0.6rem;
    padding-bottom: 0.35rem;
    border-bottom: 1px solid var(--border);
}
article h3 { font-size: 1.1rem; font-weight: 600; color: var(--fg); margin: 1.4rem 0 0.4rem; }
article h4, article h5, article h6 {
    font-size: 0.95rem; font-weight: 600; color: var(--fg-muted); margin: 1.1rem 0 0.35rem;
}
article p { margin-bottom: 0.9rem; }
article ul, article ol { margin: 0.6rem 0 0.9rem 1.5rem; }
article li { margin-bottom: 0.2rem; }
article li > ul, article li > ol { margin-top: 0.2rem; margin-bottom: 0.2rem; }
article blockquote {
    border-left: 3px solid var(--accent);
    margin: 1.1rem 0;
    padding: 0.5rem 1.25rem;
    background: var(--bg-surface);
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    color: var(--fg-muted);
    font-style: italic;
}
article blockquote p { margin-bottom: 0; }
article code {
    background: var(--code-bg);
    color: #e06c75;
    border-radius: 3px;
    padding: 0.15em 0.4em;
    font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 0.875em;
}
article pre {
    background: var(--code-bg) !important;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 1.1rem 1.4rem;
    overflow-x: auto;
    margin: 1.1rem 0;
    font-size: 0.875rem;
}
article pre code { background: none; color: inherit; padding: 0; border-radius: 0; }
.highlight {
    background: var(--code-bg) !important;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    overflow-x: auto;
    margin: 1.1rem 0;
}
.highlight pre {
    background: transparent !important;
    border: none;
    padding: 1.1rem 1.4rem;
    margin: 0;
    overflow-x: auto;
}
article table { width: 100%; border-collapse: collapse; margin: 1.1rem 0; font-size: 0.9rem; }
article th {
    background: var(--bg-surface); color: var(--fg-muted); font-weight: 600;
    text-align: left; padding: 0.6rem 0.9rem; border: 1px solid var(--border);
}
article td { padding: 0.55rem 0.9rem; border: 1px solid var(--border); vertical-align: top; }
article tr:nth-child(even) td { background: var(--bg-surface); opacity: 0.7; }
article hr { border: none; border-top: 1px solid var(--border); margin: 1.75rem 0; }
article img { max-width: 100%; border-radius: var(--radius-sm); margin: 0.65rem 0; }
article a { color: var(--accent); }
article a:hover { color: var(--accent-h); }
.toc {
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: var(--radius-sm); padding: 0.9rem 1.1rem;
    margin: 1rem 0 1.4rem; font-size: 0.875rem;
}
.toc .toctitle { font-weight: 600; color: var(--fg-muted); margin-bottom: 0.4rem; }
.toc ul { list-style: none; margin: 0; }
.toc li { margin-bottom: 0.15rem; }
.toc a { color: var(--fg-muted); }
.toc a:hover { color: var(--accent); }
.footnote { font-size: 0.8rem; color: var(--fg-faint); }
@media (max-width: 640px) {
    .page-wrap { padding: 1.25rem 1rem 3rem; }
    article { padding: 1.25rem 1rem; }
    article h1 { font-size: 1.4rem; }
}
"""


# ── Index JS ──────────────────────────────────────────────────────────────────

_INDEX_JS = r"""
function toggleCat(btn) {
    btn.classList.toggle('collapsed');
    btn.nextElementSibling.classList.toggle('hidden');
}

function setPreset(preset, btn) {
    document.querySelectorAll('.pbtn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    const from = document.getElementById('from-date');
    const to   = document.getElementById('to-date');
    if (preset === 'all') {
        from.value = ''; to.value = '';
    } else {
        const now  = new Date();
        const d    = new Date(now);
        if      (preset === '1d') d.setDate(d.getDate() - 1);
        else if (preset === '3d') d.setDate(d.getDate() - 3);
        else if (preset === '1w') d.setDate(d.getDate() - 7);
        const fmt  = x => x.getFullYear() + '-' +
            String(x.getMonth()+1).padStart(2,'0') + '-' +
            String(x.getDate()).padStart(2,'0');
        from.value = fmt(d);
        to.value   = fmt(now);
    }
    applyControls();
}

function clearPreset() {
    document.querySelectorAll('.pbtn').forEach(b => b.classList.remove('active'));
}

function applyControls() {
    const sortDesc   = document.querySelector('input[name="sort"]:checked').value === 'newest';
    const selCat     = document.getElementById('cat-select').value;
    const fromDate   = document.getElementById('from-date').value;
    const toDate     = document.getElementById('to-date').value;
    let totalVisible = 0;

    document.querySelectorAll('.category').forEach(catEl => {
        const catName = catEl.dataset.cat;
        if (selCat && catName !== selCat) {
            catEl.style.display = 'none';
            return;
        }
        const items = [...catEl.querySelectorAll('.article-item')];
        items.forEach(item => {
            const d = item.dataset.date;
            let show = true;
            if (fromDate && d < fromDate) show = false;
            if (toDate   && d > toDate)   show = false;
            item.style.display = show ? '' : 'none';
        });
        const container = catEl.querySelector('.cat-articles');
        const visible   = items.filter(i => i.style.display !== 'none');
        visible.sort((a, b) => {
            const diff = parseFloat(b.dataset.ts) - parseFloat(a.dataset.ts);
            return sortDesc ? diff : -diff;
        });
        visible.forEach(el => container.appendChild(el));
        const anyVisible = items.some(i => i.style.display !== 'none');
        catEl.style.display = anyVisible ? '' : 'none';
        if (anyVisible) totalVisible += visible.length;
    });

    const el = document.getElementById('visible-count');
    if (el) el.textContent = totalVisible;
}

document.addEventListener('DOMContentLoaded', () => applyControls());
"""


# ── Templates ─────────────────────────────────────────────────────────────────

def _render_index(cats: list[dict]) -> str:
    total = sum(len(c['articles']) for c in cats)

    cat_options = '<option value="">All categories</option>' + ''.join(
        f'<option value="{escape(c["name"])}">{escape(c["name"])}</option>'
        for c in cats
    )

    cat_html = ''
    for cat in cats:
        arts_html = ''
        for a in cat['articles']:
            arts_html += (
                f'<div class="article-item" data-ts="{a["ts"]}" data-date="{a["date_iso"]}">'
                f'<a class="article-link" href="/{escape(a["hash"])}" target="_blank" rel="noopener noreferrer">'
                f'<span class="article-title">{escape(a["title"])}</span>'
                f'<span class="article-date">{escape(a["date_disp"])}</span>'
                f'</a></div>'
            )
        cat_html += (
            f'<div class="category" data-cat="{escape(cat["name"])}">'
            f'<button class="cat-header" onclick="toggleCat(this)">'
            f'<span class="cat-arrow">&#9662;</span>'
            f'<span class="cat-name">{escape(cat["name"])}</span>'
            f'<span class="cat-count">{len(cat["articles"])}</span>'
            f'</button>'
            f'<div class="cat-articles">{arts_html}</div>'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<title>Dynamic Articles Aggregator (DAA)</title>
{_THEME_EARLY}
<style>{_INDEX_CSS}</style>
</head>
<body>
<header>
  <div class="header-left">
    <div class="site-title">Dynamic Articles Aggregator (DAA)</div>
    <div class="site-sub">Personal Knowledge Base</div>
  </div>
  <div class="header-right">
    <label for="theme-select">Theme</label>
    {_theme_select_html()}
  </div>
</header>
<main>
  <div class="controls">
    <div class="ctrl-group">
      <span class="ctrl-label">Sort</span>
      <label class="radio-label">
        <input type="radio" name="sort" value="newest" checked onchange="applyControls()"> Newest
      </label>
      <label class="radio-label">
        <input type="radio" name="sort" value="oldest" onchange="applyControls()"> Oldest
      </label>
    </div>
    <div class="ctrl-sep"></div>
    <div class="ctrl-group">
      <span class="ctrl-label">Category</span>
      <select id="cat-select" class="ctrl-select" onchange="applyControls()">{cat_options}</select>
    </div>
    <div class="ctrl-sep"></div>
    <div class="ctrl-group">
      <span class="ctrl-label">From</span>
      <input type="date" id="from-date" class="ctrl-date" oninput="clearPreset(); applyControls()">
      <span class="ctrl-label">To</span>
      <input type="date" id="to-date"   class="ctrl-date" oninput="clearPreset(); applyControls()">
    </div>
    <div class="ctrl-sep"></div>
    <div class="ctrl-group">
      <button class="pbtn active" onclick="setPreset('all', this)">All</button>
      <button class="pbtn"        onclick="setPreset('1d',  this)">Last 24h</button>
      <button class="pbtn"        onclick="setPreset('3d',  this)">Last 3 days</button>
      <button class="pbtn"        onclick="setPreset('1w',  this)">Last week</button>
    </div>
  </div>
  <div class="stats-bar">Showing <span id="visible-count">{total}</span> articles</div>
  <div class="categories">{cat_html}</div>
  {'<p class="no-results">No categories found. Add directories to the content folder.</p>' if not cats else ''}
</main>
<footer>By Aleks K</footer>
<script>{_INDEX_JS}{_THEME_JS}</script>
</body>
</html>"""


def _render_article(title: str, cat: str, date: str, html_body: str, article_hash: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<title>{escape(title)} — DAA</title>
{_THEME_EARLY}
<style>{_ARTICLE_CSS}
{PYGS_CSS}
</style>
</head>
<body>
<div class="page-wrap">
  <div class="article-meta-bar">
    <div class="meta-left">
      <span class="meta-cat">{escape(cat)}</span>
      <span class="meta-date">{escape(date)}</span>
    </div>
    <div class="meta-right">
      <label for="theme-select" style="font-size:0.72rem;color:var(--fg-faint)">Theme</label>
      {_theme_select_html()}
      <a class="dl-btn" href="/{escape(article_hash)}/download">
        <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24"
             fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        Download .md
      </a>
    </div>
  </div>
  <article>{html_body}</article>
</div>
<footer>By Aleks K</footer>
<script>{_THEME_JS}</script>
</body>
</html>"""


# ── Startup ───────────────────────────────────────────────────────────────────

init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
