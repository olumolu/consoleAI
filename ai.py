#!/usr/bin/env python3
"""
Universal Chat CLI
Pure stdlib, Python 3.9+. Zero pip installs required.

Features:
  - Markdown rendering (Bold, Italic, Code, Fenced code blocks)
  - LaTeX rendering (Greek letters, superscripts, fractions → Unicode)
  - Image attachment support (Vision models)
  - Multi-line input (backslash continuation + /paste mode)
  - Multi-provider support (Gemini, OpenRouter, Groq, Together, etc.)
  - Tool/Function calling (Web search, fetch, Calculator, Time, Wikipedia)
  - Deep web research via Startpage HTML scraping
  - Live tool progress spinner
  - Live model switching (/model command)
  - History compaction (tool messages auto-collapsed after each exchange)
  - SSRF protection with DNS-pinning
  - Interactive UI for settings and model selection

Usage:
    python ai.py <provider> [filter]...

Providers: gemini, openrouter, groq, together, cerebras, novita, ollama

Chat commands:
    /history            Show conversation history
    /model              Switch to a different model mid-chat
    /save <name>        Save session to ~/.chat_sessions/<name>.json
    /load <name>        Load a saved session
    /clear              Delete all saved sessions
    /upload <path>      Attach an image to your next message
    /image              Show currently attached image
    /clearimage         Remove the attached image
    /paste[text]       Multi-line paste mode (end with ---)
    /togglethinking     Toggle reasoning/thinking output display
    /toggletools        Toggle tool calling on/off
    /help               Show available commands
    quit / exit         End the session
"""

import sys
import os
import json
import re
import ast
import math
import operator
import base64
import signal
import socket
import ipaddress
import datetime
import gzip
import zlib
import threading
import time
import shutil
import http.cookiejar
import urllib.request
import urllib.error
import urllib.parse
import html as _html
import mimetypes
import atexit
import select as _select
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

if sys.version_info < (3, 9):
    sys.exit("This script requires Python 3.9 or newer.")

Message = dict[str, Any]
History = list[Message]

try:
    import readline
    _READLINE_AVAILABLE = True
except ImportError:
    _READLINE_AVAILABLE = False

try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

##############################################################################
#                   !!! EDIT YOUR API KEYS HERE !!!                          #
##############################################################################
API_KEYS: dict[str, str] = {
    "gemini":     "",   # https://aistudio.google.com/app/apikey
    "openrouter": "",   # https://openrouter.ai/keys
    "groq":       "",   # https://console.groq.com/keys
    "together":   "",   # https://api.together.ai/settings/api-keys
    "cerebras":   "",   # https://cloud.cerebras.ai/
    "novita":     "",   # https://novita.ai/
    "ollama":     "",   # https://ollama.com/ (leave blank for local)
}

MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_LENGTH = 50_000
DEFAULT_TEMPERATURE = 0.9
DEFAULT_MAX_TOKENS = 3000
DEFAULT_TOP_P = 1.0

SESSION_DIR = Path.home() / ".chat_sessions"
HISTORY_FILE = Path.home() / ".ai_cli_history"

MAX_IMAGE_SIZE_MB = 20
SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

FETCH_MAX_CHARS = 30_000
FETCH_MAX_BYTES = 5 * 1024 * 1024
SEARCH_MAX_RESULTS = 6

MAX_TOOL_ITERATIONS = 10
TOOL_EXEC_TIMEOUT = 60

REQUEST_TIMEOUT = 300
MODEL_FETCH_TIMEOUT = 30

MAX_RETRIES = 3
RETRYABLE_HTTP_CODES = (429, 500, 502, 503, 504)

USER_AGENT = "PythonChatCLI/2.0"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

RESEARCH_MIN_SOURCES = 4
RESEARCH_TARGET_SOURCES = 5
RESEARCH_MAX_SOURCES = 8
RESEARCH_BATCH_SIZE = 6
RESEARCH_MAX_CANDIDATES = 30

SYSTEM_PROMPT = """You are a highly helpful assistant running in a command-line interface.

For factual, current, version, release, pricing, documentation, troubleshooting, benchmark, or comparison questions:
- Do not give a final answer until you have reviewed at least 4 independent sources when possible.
- If fewer than 4 sources are reachable, say so explicitly.
- Use fetch_url for specific pages when needed.
"""


# ─────────────────────────────────────────────────────────────────────────────
# ANSI
# ─────────────────────────────────────────────────────────────────────────────

class C:
    RESET  = "\033[0m"
    USER   = "\033[38;5;199m"
    AI     = "\033[38;5;40m"
    THINK  = "\033[38;5;214m"
    ERROR  = "\033[38;5;203m"
    WARN   = "\033[38;5;221m"
    INFO   = "\033[38;5;75m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    ITALIC = "\033[3m"
    IMAGE  = "\033[38;5;208m"
    CODE   = "\033[38;5;229m"
    TOOL   = "\033[38;5;141m"
    CLR    = "\033[2K\r"


def _rl(code: str) -> str:
    return f"\001{code}\002"


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

_STDOUT_LOCK = threading.Lock()


def cprint(msg: str, end: str = "\n") -> None:
    with _STDOUT_LOCK:
        sys.stdout.write(msg + end)
        sys.stdout.flush()


def eprint(msg: str) -> None:
    with _STDOUT_LOCK:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()


def _stdout_write(text: str) -> None:
    with _STDOUT_LOCK:
        sys.stdout.write(text)
        sys.stdout.flush()


# ─────────────────────────────────────────────────────────────────────────────
# SSRF PROTECTION
# ─────────────────────────────────────────────────────────────────────────────

def _is_ip_blocked(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if (
        addr.is_private or addr.is_loopback or addr.is_reserved
        or addr.is_link_local or addr.is_multicast
    ):
        return True
    if isinstance(addr, ipaddress.IPv4Address):
        if ip_str.startswith("169.254.") or ip_str == "0.0.0.0":
            return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.is_site_local:
        return True
    return False


def _resolve_and_validate(hostname: str) -> str:
    blocked_hosts = {
        "localhost", "127.0.0.1", "0.0.0.0", "::1",
        "metadata.google.internal", "metadata.google.com",
    }
    if hostname.lower() in blocked_hosts:
        raise ValueError(f"Blocked hostname: {hostname}")

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, socket.herror, OSError) as exc:
        raise ValueError(f"DNS resolution failed for {hostname}: {exc}")

    if not infos:
        raise ValueError(f"No DNS results for {hostname}")

    for _family, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        if not _is_ip_blocked(ip):
            return ip

    raise ValueError(f"All IPs for {hostname} resolve to private/reserved addresses")


def _validate_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "Invalid URL format"
    if parsed.scheme not in ("http", "https"):
        return False, f"Blocked protocol: {parsed.scheme}://"
    if not parsed.hostname:
        return False, "No hostname in URL"
    try:
        _resolve_and_validate(parsed.hostname)
    except ValueError as exc:
        return False, str(exc)
    return True, ""


class _SSRFSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        ok, err = _validate_url(newurl)
        if not ok:
            raise urllib.error.URLError(f"Redirect blocked: {err}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_COOKIE_JAR = http.cookiejar.CookieJar()
_URL_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_COOKIE_JAR),
    _SSRFSafeRedirectHandler(),
)


# ─────────────────────────────────────────────────────────────────────────────
# TERMINAL MATH (CACHED FOR PERFORMANCE)
# ─────────────────────────────────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))

_LAST_TERM_COLS = 80
_LAST_COLS_CHECK = 0.0

def _get_term_cols() -> int:
    global _LAST_TERM_COLS, _LAST_COLS_CHECK
    now = time.monotonic()
    # Update terminal size at most every 0.2 seconds to prevent OS spam
    if now - _LAST_COLS_CHECK > 0.2:
        _LAST_TERM_COLS = max(shutil.get_terminal_size((80, 24)).columns, 20)
        _LAST_COLS_CHECK = now
    return _LAST_TERM_COLS

def _wrapped_rows(text: str) -> int:
    rows = 0
    term_cols = _get_term_cols()
    # Safely account for any \n characters that sneak into the renderer
    for line in text.split("\n"):
        vis = max(_visible_len(line), 1)
        rows += (vis - 1) // term_cols + 1
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# RENDERERS
# ─────────────────────────────────────────────────────────────────────────────

class LatexRenderer:
    def __init__(self) -> None:
        self.greek = {
            "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
            "epsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ",
            "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ",
            "nu": "ν", "xi": "ξ", "omicron": "ο", "pi": "π",
            "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ",
            "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
            "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
            "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ",
            "Psi": "Ψ", "Omega": "Ω",
        }
        self.sup_map = str.maketrans(
            "0123456789+-=()nabcdefghijklmoprstuvwx",
            "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣ"
        )
        self.sub_map = str.maketrans(
            "0123456789+-=()aehijklmnoprstuvx",
            "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"
        )
        self._display_pat = re.compile(r'\$\$(.+?)\$\$|\\\[(.+?)\\\]', re.DOTALL)
        self._inline_pat = re.compile(r'\$(.+?)\$')
        self._frac_inner = re.compile(r'\\frac\{([^{}]+)\}\{([^{}]+)\}')
        self._frac_outer = re.compile(r'\\frac\{([^}]+)\}\{([^}]+)\}')
        self._sup_brace = re.compile(r'\^\{([^}]+)\}')
        self._sup_single = re.compile(r'\^([a-zA-Z0-9])')
        self._sub_brace = re.compile(r'_\{([^}]+)\}')
        self._sub_single = re.compile(r'_([a-zA-Z0-9])')

    def render(self, text: str) -> str:
        text = self._display_pat.sub(lambda m: self._convert(m.group(1) or m.group(2)), text)
        text = self._inline_pat.sub(lambda m: self._convert(m.group(1)), text)
        return text

    def _convert(self, tex: str) -> str:
        tex = (tex or "").strip()
        for name, uni in self.greek.items():
            tex = tex.replace(f"\\{name}", uni)
        prev = None
        while tex != prev:
            prev = tex
            tex = self._frac_inner.sub(r'(\1/\2)', tex)
        tex = self._frac_outer.sub(r'(\1/\2)', tex)
        tex = self._sup_brace.sub(lambda m: m.group(1).translate(self.sup_map), tex)
        tex = self._sup_single.sub(lambda m: m.group(1).translate(self.sup_map), tex)
        tex = self._sub_brace.sub(lambda m: m.group(1).translate(self.sub_map), tex)
        tex = self._sub_single.sub(lambda m: m.group(1).translate(self.sub_map), tex)
        for cmd, sym in {
            "\\cdot": "·", "\\times": "×", "\\div": "÷", "\\sqrt": "√",
            "\\infty": "∞", "\\pm": "±", "\\neq": "≠", "\\leq": "≤",
            "\\geq": "≥", "\\approx": "≈", "\\sum": "Σ", "\\prod": "Π",
            "\\int": "∫",
        }.items():
            tex = tex.replace(cmd, sym)
        return tex


class MarkdownRenderer:
    def __init__(self) -> None:
        self.bold_pat = re.compile(r'\*\*(.+?)\*\*')
        self.italic_pat = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)')
        self.code_pat = re.compile(r'`([^`]+)`')
        self.header_pat = re.compile(r'^(#{1,6})\s+(.*)')
        self.in_code_block = False

    def _render_line_impl(self, line: str, in_code_block: bool) -> tuple[str, bool]:
        stripped = line.strip()
        if stripped.startswith("```"):
            new_state = not in_code_block
            lang = stripped[3:].strip()
            
            # Use dynamic cached width to respect terminal resizes
            term_cols = _get_term_cols()
            bar_len = min(term_cols - 4, 60)
            
            if new_state: # Opening a code block
                lbl = lang.upper() or 'CODE'
                dashes = max(1, bar_len - 17 - len(lbl))
                return f"{C.DIM}╭{'─' * 15} {lbl} {'─' * dashes}{C.RESET}", new_state
            else:         # Closing a code block
                return f"{C.DIM}╰{'─' * bar_len}{C.RESET}", new_state

        if in_code_block:
            return f"{C.CODE}{line}{C.RESET}", in_code_block

        h = self.header_pat.match(line)
        if h:
            return f"{C.BOLD}{C.INFO}{h.group(2)}{C.RESET}", in_code_block

        line = LATEX_RENDERER.render(line)
        line = self.code_pat.sub(rf'{C.CODE}`\1`{C.RESET}{C.AI}', line)
        line = self.bold_pat.sub(rf'{C.BOLD}\1{C.RESET}{C.AI}', line)
        line = self.italic_pat.sub(rf'{C.ITALIC}\1{C.RESET}{C.AI}', line)
        return line, in_code_block

    def render_line(self, line: str) -> str:
        rendered, state = self._render_line_impl(line, self.in_code_block)
        self.in_code_block = state
        return rendered

    def preview_line(self, line: str) -> str:
        rendered, _ = self._render_line_impl(line, self.in_code_block)
        return rendered


LATEX_RENDERER = LatexRenderer()
MD_RENDERER = MarkdownRenderer()


# ─────────────────────────────────────────────────────────────────────────────
# PROVIDERS
# ─────────────────────────────────────────────────────────────────────────────

ENDPOINTS: dict[str, dict[str, str]] = {
    "gemini": {
        "chat_base": "https://generativelanguage.googleapis.com/v1beta/models/",
        "models":    "https://generativelanguage.googleapis.com/v1beta/models",
    },
    "openrouter": {
        "chat":   "https://openrouter.ai/api/v1/chat/completions",
        "models": "https://openrouter.ai/api/v1/models",
    },
    "groq": {
        "chat":   "https://api.groq.com/openai/v1/chat/completions",
        "models": "https://api.groq.com/openai/v1/models",
    },
    "together": {
        "chat":   "https://api.together.ai/v1/chat/completions",
        "models": "https://api.together.ai/v1/models",
    },
    "cerebras": {
        "chat":   "https://api.cerebras.ai/v1/chat/completions",
        "models": "https://api.cerebras.ai/v1/models",
    },
    "novita": {
        "chat":   "https://api.novita.ai/v3/openai/chat/completions",
        "models": "https://api.novita.ai/v3/openai/models",
    },
    "ollama": {
        "chat":   "https://ollama.com/api/chat",
        "models": "https://ollama.com/api/tags",
    },
}

VALID_PROVIDERS = list(ENDPOINTS.keys())


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def truncate(s: str, n: int) -> str:
    if not s:
        return ""
    return s[:n - 3] + "..." if len(s) > n else s


def validate_session_name(name: str) -> bool:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        eprint(f"{C.ERROR}Error: Session name may only contain letters, numbers, dash, underscore.{C.RESET}")
        return False
    if len(name) > 100:
        eprint(f"{C.ERROR}Error: Session name too long (max 100 chars).{C.RESET}")
        return False
    return True


def check_placeholder_key(key: str, provider: str) -> bool:
    if provider == "ollama":
        return True
    bad = ""
    if not key:
        bad = "is empty"
    elif key.startswith("YOUR_") or key.endswith("-HERE") or "..." in key:
        bad = "appears to be a placeholder"
    elif provider == "gemini" and key == "-":
        bad = "is the default placeholder '-'"
    elif provider == "openrouter" and key == "sk-or-v1-":
        bad = "is an incomplete OpenRouter key"
    elif provider == "groq" and key.startswith("gsk_") and len(key) < 10:
        bad = "looks incomplete"
    if bad:
        eprint(f"{C.WARN}WARNING: API key for {provider.upper()} {bad}.{C.RESET}")
        return False
    return True


def strip_think_tags(text: str) -> str:
    result, remaining = "", text
    while remaining:
        start = remaining.find("<think")
        if start == -1:
            result += remaining
            break
        result += remaining[:start]
        after_open = remaining[start + 6:]
        bracket = after_open.find(">")
        remaining = after_open[bracket + 1:] if bracket != -1 else ""
        close = remaining.find("</think")
        if close == -1:
            break
        after_close = remaining[close + 7:]
        bracket2 = after_close.find(">")
        remaining = after_close[bracket2 + 1:] if bracket2 != -1 else ""
    return result


def filter_models(models: list[str], filters: list[str]) -> list[str]:
    if not filters:
        return models
    out: list[str] =[]
    for model in models:
        ml = model.lower()
        if all(re.search(r"(?:^|[^a-z0-9])" + re.escape(f.lower()) + r"(?:[^a-z0-9]|$)", ml) for f in filters):
            out.append(model)
    return out


def _read_chunk(resp: Any, size: int = 8192) -> bytes:
    try:
        return resp.read1(size)
    except AttributeError:
        return resp.read(size)


def _is_length_finish(reason: str) -> bool:
    return (reason or "").strip() in {"length", "max_tokens", "MAX_TOKENS", "max_output_tokens"}


def _save_readline_history() -> None:
    if not _READLINE_AVAILABLE:
        return
    try:
        readline.write_history_file(str(HISTORY_FILE))
    except OSError:
        pass


def _args_to_obj(arguments: str) -> dict[str, Any]:
    if not arguments or arguments.isspace():
        return {}
    try:
        obj = json.loads(arguments)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _args_display(arguments: str) -> str:
    obj = _args_to_obj(arguments)
    if not obj:
        return ""
    return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))


def _decompress(data: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(data)
        if "deflate" in enc:
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
    except Exception:
        pass
    return data


def _make_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        _SSRFSafeRedirectHandler(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# HTML CLEANING
# ─────────────────────────────────────────────────────────────────────────────

class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] =[]
        self.skip_tags = {'script', 'style', 'noscript', 'svg', 'iframe', 'template'}
        self.block_tags = {
            'div', 'p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'li', 'tr', 'article', 'section', 'main', 'pre',
            'blockquote', 'ul', 'ol', 'hr', 'table'
        }
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in self.skip_tags:
            self.skip_depth += 1
        elif self.skip_depth == 0 and tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.skip_tags:
            if self.skip_depth > 0:
                self.skip_depth -= 1
        elif self.skip_depth == 0 and tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0:
            self.parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.parts)
        lines =[re.sub(r"[ \t]+", " ", line.strip()) for line in raw.split("\n")]
        lines =[line for line in lines if line]
        return "\n\n".join(lines)


def _clean_html(raw: str) -> str:
    if not raw:
        return ""
    parser = TextExtractor()
    try:
        parser.feed(raw)
        return parser.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
        text = _html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()


# ─────────────────────────────────────────────────────────────────────────────
# TOOL SPINNER
# ─────────────────────────────────────────────────────────────────────────────

class _ToolProgress:
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._status = ""
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._start = 0.0

    def start(self, label: str) -> None:
        self.stop()
        self._stop.clear()
        with self._lock:
            self._status = label
        self._start = time.monotonic()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def update(self, status: str) -> None:
        with self._lock:
            self._status = status

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        _stdout_write(C.CLR)

    def _animate(self) -> None:
        i = 0
        while not self._stop.is_set():
            with self._lock:
                status = self._status
            elapsed = time.monotonic() - self._start
            frame = self._FRAMES[i % len(self._FRAMES)]
            _stdout_write(f"{C.CLR}{C.TOOL}   {frame} {status}{C.RESET} {C.DIM}({elapsed:.1f}s){C.RESET}")
            self._stop.wait(0.08)
            i += 1


_PROGRESS = _ToolProgress()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _request_with_retry(
    req: urllib.request.Request,
    timeout: int = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    opener: Optional[urllib.request.OpenerDirector] = None,
) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            if opener:
                return opener.open(req, timeout=timeout)
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in RETRYABLE_HTTP_CODES or attempt >= max_retries - 1:
                raise
            wait = 2 ** attempt
            ra = exc.headers.get("Retry-After") if exc.headers else None
            if ra and ra.isdigit():
                wait = min(int(ra), 30)
            eprint(f"{C.WARN}HTTP {exc.code} — retrying in {wait}s…{C.RESET}")
            time.sleep(wait)
        except (urllib.error.URLError, OSError) as exc:
            last_exc = exc
            if attempt >= max_retries - 1:
                raise
            wait = 2 ** attempt
            eprint(f"{C.WARN}Network error — retrying in {wait}s…{C.RESET}")
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# WEB FETCH
# ─────────────────────────────────────────────────────────────────────────────

_FETCH_HEADERS_PRIMARY = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, identity",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}

_FETCH_HEADERS_FALLBACK = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Encoding": "identity",
}


def _fetch_page(url: str, timeout: int = 20) -> str:
    ok, err = _validate_url(url)
    if not ok:
        raise ValueError(f"Blocked: {err}")

    last_error: Optional[Exception] = None

    for headers in (_FETCH_HEADERS_PRIMARY, _FETCH_HEADERS_FALLBACK):
        req = urllib.request.Request(url, headers=headers)
        try:
            with _URL_OPENER.open(req, timeout=timeout) as resp:
                cl = resp.headers.get("Content-Length")
                if cl:
                    try:
                        if int(cl) > FETCH_MAX_BYTES:
                            raise ValueError("Response too large")
                    except ValueError:
                        pass
                raw = resp.read(FETCH_MAX_BYTES + 1)
                if len(raw) > FETCH_MAX_BYTES:
                    raw = raw[:FETCH_MAX_BYTES]
                raw = _decompress(raw, resp.headers.get("Content-Encoding", ""))
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (401, 403, 429):
                continue
            raise
        except ValueError:
            raise
        except Exception as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to fetch page")


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE
# ─────────────────────────────────────────────────────────────────────────────

class ImageAttachment:
    def __init__(self) -> None:
        self.path = ""
        self.base64 = ""
        self.mime = ""

    def clear(self) -> None:
        self.path = ""
        self.base64 = ""
        self.mime = ""

    @property
    def attached(self) -> bool:
        return bool(self.base64)

    def load(self, raw_path: str) -> bool:
        path = Path(raw_path.strip("'\""))
        if not path.is_file():
            eprint(f"{C.ERROR}Error: File not found: {path}{C.RESET}")
            return False

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_IMAGE_SIZE_MB:
            eprint(f"{C.ERROR}Error: Image too large ({size_mb:.1f} MB). Max {MAX_IMAGE_SIZE_MB} MB.{C.RESET}")
            return False

        mime, _ = mimetypes.guess_type(str(path))
        if not mime:
            mime = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
            }.get(path.suffix.lower(), "")

        if mime not in SUPPORTED_MIME_TYPES:
            eprint(f"{C.ERROR}Error: Unsupported type '{mime}'.{C.RESET}")
            return False

        eprint(f"{C.IMAGE}Encoding image…{C.RESET}")
        try:
            raw = path.read_bytes()
            self.base64 = base64.b64encode(raw).decode("ascii")
            self.mime = mime
            self.path = str(path)
            eprint(f"{C.IMAGE}✓ Attached: {path.name} ({mime}, {len(raw)//1024} KB){C.RESET}")
            return True
        except OSError as exc:
            eprint(f"{C.ERROR}Error reading image: {exc}{C.RESET}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
# RESEARCH ENGINE
# ─────────────────────────────────────────────────────────────────────────────

SEARCH_CACHE: dict[str, list[dict[str, Any]]] = {}
PAGE_CACHE: dict[str, tuple[str, str]] = {}

_STOPWORDS = {
    "the", "and", "for", "are", "with", "that", "this", "from", "was", "were",
    "have", "has", "had", "into", "about", "what", "when", "where", "which",
    "their", "they", "them", "then", "than", "your", "you", "how", "why",
    "can", "could", "should", "would", "will", "may", "might", "also",
    "there", "here", "some", "such", "just", "like", "onto", "upon", "each",
    "many", "much", "very",
}


def _tokenize(text: str) -> list[str]:
    raw = (text or "").strip()
    tokens = re.findall(r"[a-z0-9]{2,}", raw.lower())
    if len(tokens) <= 3 or '"' in raw:
        return tokens
    filtered =[t for t in tokens if t not in _STOPWORDS]
    return filtered or tokens


def _domain_of(url: str) -> str:
    try:
        host = (urllib.parse.urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_url(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return url
    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path or "/"
    if path != "/":
        path = path.rstrip("/")
    qs = urllib.parse.parse_qsl(p.query, keep_blank_values=False)
    qs =[
        (k, v) for k, v in qs
        if not (
            k.lower().startswith("utm_")
            or k.lower() in {"gclid", "fbclid", "ref", "ref_src", "source"}
        )
    ]
    query = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def _unwrap_result_url(url: str) -> str:
    url = _html.unescape(url or "").strip()
    if not url:
        return ""
    if url.startswith("/"):
        url = urllib.parse.urljoin("https://www.startpage.com", url)

    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return url

    host = (p.netloc or "").lower()
    if p.scheme in ("http", "https") and "startpage.com" not in host:
        return url

    qs = urllib.parse.parse_qs(p.query)
    for key in ("url", "u", "to", "target"):
        vals = qs.get(key)
        if vals:
            cand = urllib.parse.unquote(vals[0])
            if cand.startswith("http://") or cand.startswith("https://"):
                return cand
    return url


def _should_skip_result(url: str) -> bool:
    host = _domain_of(url)
    if not host:
        return True
    if "startpage.com" in host:
        return True
    if any(bad in host for bad in ("facebook.com", "instagram.com", "pinterest.", "tiktok.com")):
        return True
    return False


def _domain_quality_bonus(url: str) -> float:
    host = _domain_of(url)
    score = 0.0
    if host.startswith("docs.") or ".docs." in host:
        score += 5.0
    if host.startswith("developer.") or ".developer." in host:
        score += 4.0
    if host.endswith(".gov"):
        score += 4.0
    if host.endswith(".edu"):
        score += 3.0
    if "wikipedia.org" in host:
        score += 2.0
    if "github.com" in host or host.endswith(".github.io"):
        score += 2.0
    if "stackoverflow.com" in host or "stackexchange.com" in host:
        score += 2.0
    if "medium.com" in host:
        score -= 1.0
    if "quora.com" in host:
        score -= 2.0
    return score


def _score_text(query: str, title: str = "", snippet: str = "", url: str = "", body: str = "") -> float:
    q_tokens = _tokenize(query)
    phrase = (query or "").strip().lower()

    title_l = (title or "").lower()
    snippet_l = (snippet or "").lower()
    url_l = (url or "").lower()
    body_l = (body or "").lower()

    full = f"{title_l} {snippet_l} {url_l} {body_l[:6000]}"
    score = 0.0

    if phrase:
        if phrase in title_l:
            score += 8.0
        if phrase in full:
            score += 6.0

    for tok in q_tokens:
        if tok in title_l:
            score += 4.0
        if tok in snippet_l:
            score += 2.0
        score += min(full.count(tok), 5) * 0.8

    score += _domain_quality_bonus(url)
    return score


def _query_variants(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return[]

    year = datetime.datetime.now().year
    ql = q.lower()
    out = [q]

    if any(k in ql for k in ("latest", "current", "new", "recent", "today", "now", "version", "release", "pricing", "price", "updated", "update")):
        out.append(f"{q} {year}")

    if any(k in ql for k in ("api", "sdk", "docs", "documentation", "install", "cli", "python", "javascript", "typescript", "error", "traceback", "library", "package", "pip", "docker", "fastapi")):
        out.append(f"{q} official documentation")

    if len(q.split()) >= 3:
        out.append(f"\"{q}\"")

    dedup: list[str] =[]
    seen: set[str] = set()
    for item in out:
        k = item.lower()
        if k not in seen:
            seen.add(k)
            dedup.append(item)
    return dedup


def _chunk_text(text: str, chunk_size: int = 1400, overlap: int = 200) -> list[str]:
    paras =[p.strip() for p in re.split(r"\n{2,}", text or "") if p.strip()]
    chunks: list[str] =[]
    cur = ""

    def push(buf: str) -> None:
        if buf.strip():
            chunks.append(buf.strip())

    for p in paras:
        if len(p) > chunk_size:
            if cur:
                push(cur)
                cur = ""
            start = 0
            while start < len(p):
                end = min(start + chunk_size, len(p))
                piece = p[start:end].strip()
                if piece:
                    chunks.append(piece)
                if end >= len(p):
                    break
                start = max(end - overlap, start + 1)
            continue

        if not cur:
            cur = p
        elif len(cur) + 2 + len(p) <= chunk_size:
            cur += "\n\n" + p
        else:
            push(cur)
            cur = p
    if cur:
        push(cur)
    return chunks


def _best_excerpts(query: str, title: str, url: str, text: str, max_chunks: int = 4) -> list[str]:
    chunks = _chunk_text(text)
    if not chunks:
        return[]
    ranked = sorted(chunks, key=lambda ch: _score_text(query, title=title, url=url, body=ch), reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for ch in ranked:
        sig = ch[:120]
        if sig in seen:
            continue
        seen.add(sig)
        out.append(ch)
        if len(out) >= max_chunks:
            break
    return out


def _extract_title_and_text(raw_html: str) -> tuple[str, str]:
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", _clean_html(m.group(1))).strip()

    candidates: list[str] =[]
    patterns =[
        r"<article[^>]*>(.*?)</article>",
        r"<main[^>]*>(.*?)</main>",
        r"<section[^>]*>(.*?)</section>",
        r'<div[^>]+(?:id|class)=["\'][^"\']*(?:article|content|post|entry|body|main|markdown|docs?|story)[^"\']*["\'][^>]*>(.*?)</div>',
    ]
    for pat in patterns:
        for mm in re.finditer(pat, raw_html, flags=re.I | re.S):
            cleaned = _clean_html(mm.group(1))
            if len(cleaned) > 200:
                candidates.append(cleaned)

    whole = _clean_html(raw_html)
    if whole:
        candidates.append(whole)

    best = max(candidates, key=len) if candidates else ""
    return title, best


def _fetch_page_text(url: str) -> tuple[str, str]:
    url = _normalize_url(url)
    cached = PAGE_CACHE.get(url)
    if cached is not None:
        return cached
    raw = _fetch_page(url)
    title, text = _extract_title_and_text(raw)
    PAGE_CACHE[url] = (title, text)
    return title, text


def _startpage_search_structured(query: str, limit: int = 10) -> list[dict[str, Any]]:
    cache_key = f"sp::{query}::{limit}"
    cached = SEARCH_CACHE.get(cache_key)
    if cached is not None:
        return cached[:limit]

    opener = _make_opener()

    try:
        _PROGRESS.update("Connecting to Startpage…")
        req = urllib.request.Request(
            "https://www.startpage.com/",
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, identity",
                "DNT": "1",
            },
        )
        with opener.open(req, timeout=10) as resp:
            resp.read(300_000)
    except Exception:
        pass

    _PROGRESS.update(f"Searching: {truncate(query, 40)}")
    post_data = urllib.parse.urlencode({
        "q": query,
        "cat": "web",
        "cmd": "process_search",
        "language": "english",
        "engine0": "v1all",
    }).encode()

    try:
        req = urllib.request.Request(
            "https://www.startpage.com/do/search",
            data=post_data,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, identity",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.startpage.com",
                "Referer": "https://www.startpage.com/",
                "DNT": "1",
            },
            method="POST",
        )
        with opener.open(req, timeout=15) as resp:
            raw = resp.read(FETCH_MAX_BYTES)
            raw = _decompress(raw, resp.headers.get("Content-Encoding", ""))
            html_text = raw.decode("utf-8", errors="replace")
    except Exception:
        return[]

    if "captcha" in html_text.lower():
        return[]

    _PROGRESS.update("Parsing results…")
    found: list[tuple[str, str, str]] =[]

    for m in re.finditer(
        r'<a[^>]+class="[^"]*result-title[^"]*"[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        html_text, re.I | re.S,
    ):
        href = m.group(1)
        title = re.sub(r"\s+", " ", _clean_html(m.group(2))).strip()
        snippet = ""
        after = html_text[m.end():m.end() + 2500]
        sm = re.search(
            r'class="[^"]*(?:result-description|w-gl__description)[^"]*"[^>]*>(.*?)</(?:p|div|span)>',
            after, re.I | re.S,
        )
        if sm:
            snippet = re.sub(r"\s+", " ", _clean_html(sm.group(1))).strip()
        found.append((href, title, snippet))

    if not found:
        for m in re.finditer(
            r'<h[23][^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html_text, re.I | re.S,
        ):
            found.append((m.group(1), re.sub(r"\s+", " ", _clean_html(m.group(2))).strip(), ""))

    out: list[dict[str, Any]] =[]
    seen_urls: set[str] = set()
    for href, title, snippet in found:
        url = _normalize_url(_unwrap_result_url(href))
        if not url.startswith(("http://", "https://")):
            continue
        if _should_skip_result(url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        if not title:
            title = url
        out.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "score": _score_text(query, title=title, snippet=snippet, url=url),
        })
        if len(out) >= limit * 3:
            break

    out.sort(key=lambda x: x["score"], reverse=True)
    SEARCH_CACHE[cache_key] = out
    return out[:limit]


def _search_candidates(query: str, max_sources: int) -> list[dict[str, Any]]:
    variants = _query_variants(query)[:4]
    all_rows: list[dict[str, Any]] =[]
    seen_urls: set[str] = set()

    per_variant = max(10, max_sources * 5)

    for variant_index, variant in enumerate(variants):
        rows = _startpage_search_structured(variant, limit=per_variant)
        for rank, row in enumerate(rows, 1):
            url = _normalize_url(row["url"])
            if url in seen_urls:
                continue
            seen_urls.add(url)
            item = dict(row)
            item["query_used"] = variant
            item["score"] = float(item.get("score", 0.0))
            if variant_index == 0:
                item["score"] += 3.0
            item["score"] += max(0.0, 2.0 - 0.15 * (rank - 1))
            all_rows.append(item)

    all_rows.sort(key=lambda x: x["score"], reverse=True)

    primary: list[dict[str, Any]] =[]
    fallback: list[dict[str, Any]] =[]
    seen_domains: set[str] = set()

    for item in all_rows:
        dom = _domain_of(item["url"])
        if dom and dom not in seen_domains:
            primary.append(item)
            seen_domains.add(dom)
        else:
            fallback.append(item)

    return (primary + fallback)[:RESEARCH_MAX_CANDIDATES]


def _fetch_source_batch(batch: list[dict[str, Any]], query: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    ok_rows: list[dict[str, Any]] =[]
    bad_rows: list[dict[str, str]] =[]
    lock = threading.Lock()
    threads: list[threading.Thread] =[]

    def worker(candidate: dict[str, Any]) -> None:
        url = candidate["url"]
        try:
            title, text = _fetch_page_text(url)
            if len((text or "").strip()) < 200:
                raise ValueError("not enough readable text")

            final_title = title or candidate.get("title") or url
            excerpts = _best_excerpts(query, final_title, url, text, max_chunks=4)
            if not excerpts:
                excerpts = [text[:1800]]

            score = float(candidate.get("score", 0.0)) + _score_text(
                query,
                title=final_title,
                url=url,
                body="\n\n".join(excerpts),
            )

            with lock:
                ok_rows.append({
                    "title": final_title,
                    "url": url,
                    "snippet": candidate.get("snippet", ""),
                    "query_used": candidate.get("query_used", query),
                    "score": score,
                    "excerpts": excerpts[:4],
                })
        except Exception as exc:
            with lock:
                bad_rows.append({"url": url, "error": str(exc)})

    for item in batch:
        t = threading.Thread(target=worker, args=(item,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=22)

    return ok_rows, bad_rows


def _research_sources(query: str, max_sources: int) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    max_sources = max(1, min(max_sources, RESEARCH_MAX_SOURCES))
    candidates = _search_candidates(query, max_sources=max_sources)
    if not candidates:
        return [], [],[]

    fetched_raw: list[dict[str, Any]] =[]
    failures: list[dict[str, str]] =[]
    cursor = 0

    while cursor < len(candidates):
        batch = candidates[cursor:cursor + RESEARCH_BATCH_SIZE]
        cursor += RESEARCH_BATCH_SIZE
        _PROGRESS.update(f"Fetching sources… {min(cursor, len(candidates))}/{len(candidates)} candidates")
        ok_rows, bad_rows = _fetch_source_batch(batch, query)
        fetched_raw.extend(ok_rows)
        failures.extend(bad_rows)

        unique_domains = {_domain_of(item["url"]) for item in fetched_raw if _domain_of(item["url"])}
        if len(unique_domains) >= max_sources:
            break

    fetched_raw.sort(key=lambda x: x["score"], reverse=True)

    selected: list[dict[str, Any]] =[]
    leftovers: list[dict[str, Any]] =[]
    seen_domains: set[str] = set()

    for item in fetched_raw:
        dom = _domain_of(item["url"])
        if dom and dom not in seen_domains:
            selected.append(item)
            seen_domains.add(dom)
        else:
            leftovers.append(item)

    if len(selected) < max_sources:
        for item in leftovers:
            selected.append(item)
            if len(selected) >= max_sources:
                break

    return selected[:max_sources], failures, candidates


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS
# ─────────────────────────────────────────────────────────────────────────────

def tool_get_time(**kwargs: Any) -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_CALC_MAX_EXPONENT = 10_000
_CALC_MAX_RESULT = 1e308

_CALC_ALLOWED_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt, "abs": abs, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "log": math.log, "log2": math.log2, "log10": math.log10,
    "exp": math.exp, "floor": math.floor, "ceil": math.ceil,
    "factorial": math.factorial, "gcd": math.gcd,
}
_CALC_ALLOWED_CONSTS: dict[str, float] = {
    "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf,
}
_CALC_BIN_OPS: dict[type, Any] = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.BitXor: operator.xor,
}
_CALC_UNARY_OPS: dict[type, Any] = {
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def tool_calculator(expression: str = "", **kwargs: Any) -> str:
    def _eval_node(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in _CALC_ALLOWED_CONSTS:
            return _CALC_ALLOWED_CONSTS[node.id]
        if isinstance(node, ast.BinOp):
            fn = _CALC_BIN_OPS.get(type(node.op))
            if fn is None:
                raise TypeError(f"Unsupported operator: {type(node.op).__name__}")
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            if fn is operator.pow and isinstance(right, (int, float)) and abs(right) > _CALC_MAX_EXPONENT:
                raise ValueError(f"Exponent too large: {right}")
            result = fn(left, right)
            if isinstance(result, float):
                if result != result:
                    raise ValueError("Result is NaN")
                if abs(result) > _CALC_MAX_RESULT and not math.isinf(result):
                    raise OverflowError("Result too large")
            return result
        if isinstance(node, ast.UnaryOp):
            fn = _CALC_UNARY_OPS.get(type(node.op))
            if fn is None:
                raise TypeError(f"Unsupported unary operator: {type(node.op).__name__}")
            return fn(_eval_node(node.operand))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise TypeError("Only simple function names allowed")
            name = node.func.id
            fn = _CALC_ALLOWED_FUNCS.get(name)
            if fn is None:
                raise TypeError(f"Unknown function '{name}'")
            if node.keywords:
                raise TypeError("Keyword arguments not supported")
            args =[_eval_node(a) for a in node.args]
            if name == "factorial":
                if len(args) != 1 or not isinstance(args[0], int) or args[0] < 0:
                    raise ValueError("factorial() requires a non-negative integer")
                if args[0] > 1000:
                    raise ValueError("factorial() input too large")
            return fn(*args)
        raise TypeError(f"Unsupported expression element: {type(node).__name__}")

    if not expression.strip():
        return "Error: No expression provided."
    try:
        expression = expression.replace("^", "**")
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree.body)
        if isinstance(result, float) and result == int(result) and not math.isinf(result):
            return str(int(result))
        return str(result)
    except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        return f"Error: {exc}"
    except SyntaxError:
        return f"Error: Invalid expression syntax: {expression}"


def tool_web_search(query: str = "", num_results: int = 0, **kwargs: Any) -> str:
    if not query:
        return "Error: No query provided."
    try:
        limit = int(num_results)
    except Exception:
        limit = 0
    limit = limit if 1 <= limit <= 10 else SEARCH_MAX_RESULTS

    rows = _search_candidates(query, max_sources=max(limit, 6))[:limit]
    if not rows:
        return f"No results found for: {query}"

    lines =[
        f"Web search results for: {query}",
        "(Quick lookup only. For final factual/current answers, prefer web_research.)",
        "",
    ]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {row['title']}")
        lines.append(f"   URL: {row['url']}")
        if row.get("snippet"):
            lines.append(f"   {row['snippet']}")
        if row.get("query_used") and row["query_used"] != query:
            lines.append(f"   matched via query variant: {row['query_used']}")
        lines.append("")
    return "\n".join(lines).strip()[:FETCH_MAX_CHARS]


def tool_fetch_url(url: str = "", focus_query: str = "", **kwargs: Any) -> str:
    if not url:
        return "Error: No URL provided."
    if not url.startswith("http"):
        url = "https://" + url
    url = _normalize_url(url)

    ok, err = _validate_url(url)
    if not ok:
        return f"Error: {err}"

    try:
        _PROGRESS.update(f"Fetching {truncate(_domain_of(url) or url, 35)}…")
        title, text = _fetch_page_text(url)
        if not text:
            return f"Error: No readable text found at {url}"

        focus_query = str(focus_query or "").strip()
        if focus_query:
            excerpts = _best_excerpts(focus_query, title or url, url, text, max_chunks=4)
            if not excerpts:
                excerpts = [text[:1800]]

            lines =[
                f"Page: {title or url}",
                f"URL: {url}",
                f"Focus query: {focus_query}",
                "",
            ]
            for i, ex in enumerate(excerpts, 1):
                lines.append(f"[Excerpt {i}]")
                lines.append(ex)
                lines.append("")
            return "\n".join(lines).strip()[:FETCH_MAX_CHARS]

        header = f"Page: {title}\nURL: {url}\n\n" if title else f"URL: {url}\n\n"
        full = header + text
        if len(full) > FETCH_MAX_CHARS:
            return full[:FETCH_MAX_CHARS] + "\n\n[Content truncated]"
        return full

    except urllib.error.HTTPError as exc:
        return f"Error fetching URL: HTTP {exc.code} ({exc.reason})"
    except Exception as exc:
        return f"Error fetching URL: {exc}"


def tool_web_research(query: str = "", max_sources: int = RESEARCH_TARGET_SOURCES, **kwargs: Any) -> str:
    if not query:
        return "Error: No query provided."

    try:
        max_sources = int(max_sources)
    except Exception:
        max_sources = RESEARCH_TARGET_SOURCES
    max_sources = max(1, min(max_sources, RESEARCH_MAX_SOURCES))

    _PROGRESS.update("Building candidate list…")
    selected, failures, candidates = _research_sources(query, max_sources=max_sources)
    if not selected:
        return f"No research sources could be fetched for: {query}"

    lines =[
        f"Research results for: {query}",
        f"RESEARCH_SOURCES_REVIEWED: {len(selected)}",
        f"RESEARCH_TARGET_SOURCES: {max_sources}",
        f"RESEARCH_FAILED_FETCHES: {len(failures)}",
        f"Candidate URLs considered: {len(candidates)}",
        "",
    ]

    for i, item in enumerate(selected, 1):
        lines.append(f"[{i}] {item['title']}")
        lines.append(f"URL: {item['url']}")
        lines.append(f"Domain: {_domain_of(item['url'])}")
        if item.get("snippet"):
            lines.append(f"Search snippet: {truncate(item['snippet'], 280)}")
        if item.get("query_used") and item["query_used"] != query:
            lines.append(f"Matched via query variant: {item['query_used']}")
        lines.append("Relevant excerpts:")
        for ex in item.get("excerpts", [])[:4]:
            lines.append(f"- {truncate(ex, 1800)}")
        lines.append("")

    if failures:
        lines.append("Fetch failures:")
        for row in failures[:10]:
            lines.append(f"- {row['url']} :: {truncate(row['error'], 160)}")
        lines.append("")

    if len(selected) < RESEARCH_MIN_SOURCES:
        lines.append(
            f"WARNING: Only {len(selected)} independent sources could be reviewed. "
            f"If possible, continue researching before finalizing the answer."
        )
    else:
        lines.append(
            "You have enough sources to answer. Prefer claims supported by multiple numbered sources above."
        )

    return "\n".join(lines).strip()[:FETCH_MAX_CHARS]


def tool_wikipedia(query: str = "", lang: str = "en", **kwargs: Any) -> str:
    if not query:
        return "Error: No query provided."
    if not re.fullmatch(r"[a-z]{2,5}", lang):
        lang = "en"

    try:
        _PROGRESS.update(f"Searching Wikipedia: {truncate(query, 30)}")
        search_url = (
            f"https://{lang}.wikipedia.org/w/api.php?"
            f"action=query&list=search"
            f"&srsearch={urllib.parse.quote(query)}"
            f"&srlimit=1&format=json"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        rows = data.get("query", {}).get("search",[])
        if not rows:
            return f"No Wikipedia results for: {query}"

        title = rows[0]["title"]
        page_url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        return tool_fetch_url(url=page_url, focus_query=query)
    except Exception as exc:
        return f"Error: {exc}"


TOOLS_REGISTRY: dict[str, Any] = {
    "get_time":      tool_get_time,
    "calculator":    tool_calculator,
    "web_search":    tool_web_search,
    "web_research":  tool_web_research,
    "fetch_url":     tool_fetch_url,
    "wikipedia":     tool_wikipedia,
}


OPENAI_TOOLS_SCHEMA: list[dict[str, Any]] =[
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current local time and date.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a mathematical expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Quick web lookup via Startpage. Returns candidate results only. "
                "Do not rely on this alone for final factual/current answers; prefer web_research."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_research",
            "description": (
                "Deep web research. Searches multiple query variants, fetches multiple pages from "
                "different domains, and returns numbered sources with relevant excerpts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_sources": {"type": "integer"},
                },
                "required":["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a specific page and extract readable text. Use focus_query for focused excerpts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "focus_query": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia",
            "description": "Search Wikipedia and return the top article's relevant plaintext excerpts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "lang": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
]

GEMINI_TOOLS_SCHEMA: list[dict[str, Any]] =[
    {
        "functionDeclarations":[
            {"name": "get_time", "description": "Get the current local time and date."},
            {
                "name": "calculator",
                "description": "Evaluate a mathematical expression.",
                "parameters": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            },
            {
                "name": "web_search",
                "description": (
                    "Quick web lookup via Startpage. Returns candidate results only. "
                    "Do not rely on this alone for final factual/current answers; prefer web_research."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "num_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "web_research",
                "description": (
                    "Deep web research. Searches multiple query variants, fetches multiple pages from "
                    "different domains, and returns numbered sources with relevant excerpts."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_sources": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "fetch_url",
                "description": "Fetch a specific page and extract readable text. Use focus_query for focused excerpts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "focus_query": {"type": "string"},
                    },
                    "required":["url"],
                },
            },
            {
                "name": "wikipedia",
                "description": "Search Wikipedia and return the top article's relevant plaintext excerpts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "lang": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        ],
    },
]


def execute_tool(name: str, arguments_json: str) -> str:
    fn = TOOLS_REGISTRY.get(name)
    if fn is None:
        return f"Error: unknown tool '{name}'"

    args = _args_to_obj(arguments_json)
    args = {k: v for k, v in args.items() if k}

    _PROGRESS.start(f"{name}…")
    result_box: list[Optional[str]] = [None]
    error_box: list[Optional[Exception]] =[None]

    def _run() -> None:
        try:
            result_box[0] = str(fn(**args))
        except Exception as exc:
            error_box[0] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout=TOOL_EXEC_TIMEOUT)
    _PROGRESS.stop()

    if worker.is_alive():
        return f"Error: tool '{name}' timed out after {TOOL_EXEC_TIMEOUT}s"
    if error_box[0] is not None:
        return f"Error executing '{name}': {error_box[0]}"
    return result_box[0] or ""


# ─────────────────────────────────────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def _session_path(name: str) -> Path:
    return SESSION_DIR / f"{name}.json"


def save_session(name: str, history: History) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _session_path(name)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        cprint(f"{C.INFO}Session saved → {path}{C.RESET}")
    except OSError as exc:
        eprint(f"{C.ERROR}Error saving session: {exc}{C.RESET}")


def _validate_session_data(data: Any) -> str:
    if not isinstance(data, list):
        return "not a JSON array"
    for msg in data:
        if not isinstance(msg, dict):
            return "element is not an object"
        role = msg.get("role")
        if not role:
            return "missing role"
    return ""


def load_session(name: str) -> Optional[History]:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = _session_path(name)
    if not path.exists():
        eprint(f"{C.ERROR}Error: Session not found: {path}{C.RESET}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        eprint(f"{C.ERROR}Error reading session: {exc}{C.RESET}")
        return None
    err = _validate_session_data(data)
    if err:
        eprint(f"{C.ERROR}Session is corrupt ({err}).{C.RESET}")
        return None
    cprint(f"{C.INFO}Session loaded ← {path} ({len(data)} messages){C.RESET}")
    return data


def clear_sessions() -> None:
    if not SESSION_DIR.exists():
        cprint(f"{C.INFO}No saved sessions to clear.{C.RESET}")
        return
    files = sorted(SESSION_DIR.glob("*.json"))
    if not files:
        cprint(f"{C.INFO}No saved sessions to clear.{C.RESET}")
        return
    cprint(f"{C.WARN}This will permanently delete all sessions in {SESSION_DIR}:{C.RESET}")
    for f in files:
        cprint(f"  {f.stem}")
    try:
        ans = input(f"{_rl(C.WARN)}Continue? (y/N): {_rl(C.RESET)}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        cprint(f"\n{C.INFO}Cancelled.{C.RESET}")
        return
    if ans == "y":
        for f in files:
            f.unlink(missing_ok=True)
        cprint(f"{C.INFO}All sessions cleared.{C.RESET}")
    else:
        cprint(f"{C.INFO}Cancelled.{C.RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY
# ─────────────────────────────────────────────────────────────────────────────

def init_history(is_openai_compat: bool) -> History:
    if SYSTEM_PROMPT and is_openai_compat:
        return[{"role": "system", "content": SYSTEM_PROMPT}]
    return[]


def truncate_history(history: History, is_openai_compat: bool) -> History:
    system_offset = 1 if is_openai_compat and history and history[0].get("role") == "system" else 0
    max_total = MAX_HISTORY_MESSAGES + system_offset
    if len(history) <= max_total:
        return history
    to_remove = len(history) - max_total
    if not is_openai_compat and to_remove % 2 == 1:
        to_remove += 1
    if system_offset:
        return[history[0]] + history[1 + to_remove:]
    return history[to_remove:]


# ─────────────────────────────────────────────────────────────────────────────
# MODELS INTERACTIVE UI
# ─────────────────────────────────────────────────────────────────────────────

def _build_request(url: str, api_key: str, provider: str, data: Optional[bytes] = None) -> urllib.request.Request:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/python-chat-cli"
        headers["X-Title"] = "PythonChatCLI"
    method = "POST" if data is not None else "GET"
    return urllib.request.Request(url, data=data, headers=headers, method=method)


def fetch_models(provider: str, api_key: str) -> Optional[list[str]]:
    ep = ENDPOINTS[provider]
    if provider == "gemini":
        url = f"{ep['models']}?key={api_key}"
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    else:
        req = _build_request(ep["models"], api_key, provider)

    try:
        with _request_with_retry(req, timeout=MODEL_FETCH_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        eprint(f"{C.ERROR}HTTP {exc.code} fetching models: {truncate(body, 200)}{C.RESET}")
        return None
    except OSError as exc:
        eprint(f"{C.ERROR}Network error fetching models: {exc}{C.RESET}")
        return None

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        eprint(f"{C.ERROR}Invalid JSON from model endpoint.{C.RESET}")
        return None

    try:
        if provider == "gemini":
            models = [
                m["name"].replace("models/", "")
                for m in data.get("models",[])
                if (
                    any("generateContent" in method for method in m.get("supportedGenerationMethods",[]))
                    and not m["name"].startswith("models/embedding")
                )
            ]
        elif provider == "ollama":
            models = [m["name"] for m in data.get("models",[])]
        elif provider == "together":
            arr = data if isinstance(data, list) else data.get("data", [])
            models = sorted(m["id"] for m in arr)
        else:
            models = sorted(m["id"] for m in data.get("data",[]))
    except Exception as exc:
        eprint(f"{C.ERROR}Could not parse model list: {exc}{C.RESET}")
        return None

    return[m for m in models if m]


_PICK_SEL_BG = "\033[48;5;215m"
_PICK_SEL_FG = "\033[38;5;16m"


class _RawTerminal:
    def __init__(self) -> None:
        self.fd: Optional[int] = None
        self.old: Any = None

    def __enter__(self) -> "_RawTerminal":
        if os.name != "nt" and termios and tty and sys.stdin.isatty():
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.fd is not None and self.old is not None and termios:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)


def _truncate_plain(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[:width - 1] + "…"


def _picker_filter_models(models: list[str], query: str) -> list[str]:
    q = (query or "").strip().lower()
    if not q:
        return list(models)

    parts =[p for p in q.split() if p]
    if not parts:
        return list(models)

    out: list[str] =[]
    for model in models:
        ml = model.lower()
        if all(part in ml for part in parts):
            out.append(model)
    return out


def _tty_join(lines: list[str]) -> str:
    return "\r\n".join(lines)


def _ansi_pad(text: str, width: int) -> str:
    pad = max(0, width - _visible_len(text))
    return text + (" " * pad)


def _panel_line(content: str, inner_width: int) -> str:
    return f"│{_ansi_pad(content, inner_width)}│"


def _center_block(lines: list[str], term_cols: int, term_rows: int) -> str:
    block_h = len(lines)
    block_w = max((_visible_len(line) for line in lines), default=0)

    top = max(0, (term_rows - block_h) // 2)
    left = max(0, (term_cols - block_w) // 2)

    out: list[str] = []
    out.extend([""] * top)

    prefix = " " * left
    for line in lines:
        out.append(prefix + line)

    return "\033[H\033[2J" + _tty_join(out)


def _read_picker_key() -> str:
    if os.name == "nt" and msvcrt is not None:
        ch = msvcrt.getwch()

        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return {
                "H": "UP",
                "P": "DOWN",
                "K": "LEFT",
                "M": "RIGHT",
                "G": "HOME",
                "O": "END",
                "I": "PGUP",
                "Q": "PGDN",
            }.get(ch2, "")

        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\x1b":
            return "ESC"
        if ch in ("\x08", "\x7f"):
            return "BACKSPACE"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch

    # Unbuffered posix read (avoids crashing on arrow keys when python buffers inputs)
    try:
        fd = sys.stdin.fileno()
        b = os.read(fd, 1)
    except OSError:
        return ""

    if not b:
        return ""
    if b == b"\x03":
        raise KeyboardInterrupt
    if b in (b"\r", b"\n"):
        return "ENTER"
    if b in (b"\x7f", b"\x08"):
        return "BACKSPACE"

    if b == b"\x1b":
        if _select.select([fd], [], [], 0.05)[0]:
            nxt = os.read(fd, 1)
            if nxt in (b"[", b"O"):
                if _select.select([fd], [], [], 0.05)[0]:
                    nxt2 = os.read(fd, 1)
                    if nxt == b"[" and nxt2.isdigit():
                        if _select.select([fd], [], [], 0.05)[0]:
                            nxt3 = os.read(fd, 1)
                            seq = nxt2 + nxt3
                            return {
                                b"5~": "PGUP",
                                b"6~": "PGDN",
                            }.get(seq, "ESC")
                    return {
                        b"A": "UP",
                        b"B": "DOWN",
                        b"C": "RIGHT",
                        b"D": "LEFT",
                        b"H": "HOME",
                        b"F": "END",
                    }.get(nxt2, "ESC")
            return "ESC"
        return "ESC"

    buf = b
    while True:
        try:
            return buf.decode("utf-8")
        except UnicodeDecodeError:
            if len(buf) >= 4:
                return ""
            if _select.select([fd], [],[], 0.05)[0]:
                buf += os.read(fd, 1)
            else:
                return ""


def _interactive_confirm(prompt: str, default: bool = True, color: str = C.INFO) -> bool:
    """Inline interactive Yes/No selector."""
    use_picker = (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and (
            (os.name == "nt" and msvcrt is not None)
            or (os.name != "nt" and termios is not None and tty is not None)
        )
    )
    if not use_picker:
        while True:
            try:
                ans = input(f"{_rl(color)}{prompt} (Y/n): {_rl(C.RESET)}").strip().lower()
                if ans in ("", "y", "yes"): return True
                if ans in ("n", "no"): return False
            except (EOFError, KeyboardInterrupt):
                sys.exit(0)

    selected = default
    _stdout_write("\033[?25l")  # Hide cursor safely
    
    try:
        with _RawTerminal():
            while True:
                yes_str = f"{_PICK_SEL_BG}{_PICK_SEL_FG} Yes {C.RESET}" if selected else " Yes "
                no_str  = f"{_PICK_SEL_BG}{_PICK_SEL_FG} No {C.RESET}" if not selected else " No "
                
                # Write inline prompt
                _stdout_write(f"\r{C.CLR}{color}✦ {prompt}{C.RESET}  {yes_str} {no_str}")
                
                key = _read_picker_key()
                if key in ("LEFT", "UP", "RIGHT", "DOWN", "h", "l", "j", "k"):
                    selected = not selected
                elif key in ("y", "Y"):
                    selected = True
                    break
                elif key in ("n", "N"):
                    selected = False
                    break
                elif key == "ENTER":
                    break
                elif key == "ESC":
                    sys.exit(0)
            
            ans_str = "Yes" if selected else "No"
            _stdout_write(f"\r{C.CLR}{color}✓ {prompt}{C.RESET} {C.BOLD}{ans_str}{C.RESET}\n")
            return selected
            
    except KeyboardInterrupt:
        _stdout_write("\r")
        sys.exit(0)
    finally:
        _stdout_write("\033[?25h")  # ALWAYS restore cursor


def _render_model_picker(
    provider: str,
    all_models: list[str],
    visible: list[str],
    selected: int,
    query: str,
    current_model: str,
    number_buffer: str,
) -> None:
    term_cols, term_rows = shutil.get_terminal_size((100, 30))
    term_cols = max(term_cols, 60)
    term_rows = max(term_rows, 16)

    panel_width = min(100, max(60, int(term_cols * 0.72)))
    inner_width = panel_width - 2

    max_needed_height = 9 + len(visible)
    panel_height = min(term_rows - 4, max_needed_height)
    panel_height = max(panel_height, 12)

    fixed_rows = 9
    list_rows = max(4, panel_height - fixed_rows)

    if visible:
        selected = max(0, min(selected, len(visible) - 1))
        start = max(0, selected - (list_rows // 2))
        start = min(start, max(0, len(visible) - list_rows))
        end = min(len(visible), start + list_rows)
    else:
        start = 0
        end = 0

    title_left = f"{C.BOLD}{provider.upper()}{C.RESET} {C.DIM}({provider}){C.RESET}"
    title_right = f"{C.DIM}esc cancel{C.RESET}"
    title_gap = max(1, inner_width - _visible_len(title_left) - _visible_len(title_right))
    title_line = title_left + (" " * title_gap) + title_right

    search_plain = query if query else "type to filter models..."
    search_plain = _truncate_plain(search_plain, max(8, inner_width - 10))
    if query:
        search_body = search_plain
    else:
        search_body = f"{C.DIM}{search_plain}{C.RESET}"
    search_line = f"{C.WARN}Search:{C.RESET} {search_body}{C.DIM}█{C.RESET}"

    hint_plain = "↑/↓ move • Enter select • digits jump • Backspace delete"
    if number_buffer:
        hint_plain += f"[jump: {number_buffer}]"
    hint = f"{C.DIM}{_truncate_plain(hint_plain, inner_width)}{C.RESET}"

    header = (
        f"{C.TOOL}{provider.upper()} models{C.RESET} "
        f"{C.DIM}({len(visible)} shown / {len(all_models)} total){C.RESET}"
    )

    lines: list[str] =[]
    lines.append("┌" + "─" * inner_width + "┐")
    lines.append(_panel_line(title_line, inner_width))
    lines.append(_panel_line("", inner_width))
    lines.append(_panel_line(search_line, inner_width))
    lines.append(_panel_line(hint, inner_width))
    lines.append(_panel_line("", inner_width))
    lines.append(_panel_line(header, inner_width))

    if not visible:
        lines.append(_panel_line(f"{C.ERROR}No models match your search.{C.RESET}", inner_width))
        for _ in range(list_rows - 1):
            lines.append(_panel_line("", inner_width))
    else:
        for idx in range(start, end):
            num = idx + 1
            is_current = visible[idx] == current_model
            current_suffix_plain = " [current]" if is_current else ""
            current_suffix_col = f" {C.DIM}[current]{C.RESET}" if is_current else ""

            name_space = max(8, inner_width - 6 - len(current_suffix_plain))
            model_name = _truncate_plain(visible[idx], name_space)
            row = f"{num:>3}. {model_name}{current_suffix_col}"

            scroll_char = "│"
            if len(visible) > list_rows:
                thumb_pos = int((start / max(1, len(visible) - list_rows)) * (list_rows - 1))
                current_pos = idx - start
                scroll_char = f"{C.DIM}█{C.RESET}" if current_pos == thumb_pos else f"{C.DIM}│{C.RESET}"

            if idx == selected:
                lines.append(f"│{_PICK_SEL_BG}{_PICK_SEL_FG}{_ansi_pad(row, inner_width)}{C.RESET}{scroll_char}")
            else:
                lines.append(f"│{_ansi_pad(row, inner_width)}{scroll_char}")

        for _ in range(list_rows - (end - start)):
            lines.append(_panel_line("", inner_width))

    footer_left = f"{C.DIM}Enter select{C.RESET}"
    footer_right = f"{C.DIM}{selected + 1 if visible else 0}/{len(visible)}{C.RESET}"
    footer_gap = max(1, inner_width - _visible_len(footer_left) - _visible_len(footer_right))
    footer = footer_left + (" " * footer_gap) + footer_right

    lines.append(_panel_line("", inner_width))
    lines.append(_panel_line(footer, inner_width))
    lines.append("└" + "─" * inner_width + "┘")

    screen = _center_block(lines, term_cols, term_rows)
    _stdout_write(screen)


def _select_model_numeric_fallback(
    provider: str,
    models: list[str],
    current_model: str = "",
) -> Optional[str]:
    if len(models) == 1:
        cprint(f"{C.INFO}Auto-selected:{C.RESET} {models[0]}")
        return models[0]

    cprint(f"{C.INFO}Available models for {provider.upper()}:{C.RESET}")
    for i, m in enumerate(models, 1):
        marker = "[current]" if m == current_model else ""
        cprint(f"  {i:3}. {m}{marker}")

    while True:
        try:
            choice = input(f"{_rl(C.INFO)}Select model number (or 'c' to cancel): {_rl(C.RESET)}").strip()
        except (EOFError, KeyboardInterrupt):
            cprint(f"\n{C.INFO}Cancelled.{C.RESET}")
            return None

        if choice.lower() in {"c", "cancel", "q"}:
            cprint(f"{C.INFO}Cancelled.{C.RESET}")
            return None

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(models):
                return models[idx - 1]

        eprint(f"{C.WARN}Enter a number between 1 and {len(models)}.{C.RESET}")


def select_model_interactive(
    provider: str,
    api_key: str,
    filters: list[str],
    current_model: str = "",
) -> Optional[str]:
    cprint(f"{C.INFO}Fetching models for {provider.upper()}…{C.RESET}")
    models = fetch_models(provider, api_key)
    if not models:
        eprint(f"{C.ERROR}No models returned by {provider.upper()}.{C.RESET}")
        return None

    initial_query = " ".join(filters).strip()
    initially_filtered = _picker_filter_models(models, initial_query)

    use_picker = (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and (
            (os.name == "nt" and msvcrt is not None)
            or (os.name != "nt" and termios is not None and tty is not None)
        )
    )

    if not use_picker:
        if initial_query and not initially_filtered:
            eprint(f"{C.ERROR}No models matched filter: {' '.join(filters)}{C.RESET}")
            return None
        return _select_model_numeric_fallback(
            provider,
            initially_filtered if initial_query else models,
            current_model=current_model,
        )

    if len(initially_filtered) == 1:
        cprint(f"{C.INFO}Auto-selected:{C.RESET} {initially_filtered[0]}")
        return initially_filtered[0]

    query = initial_query
    visible = _picker_filter_models(models, query)

    if visible and current_model in visible:
        selected = visible.index(current_model)
    elif not query and current_model in models:
        selected = models.index(current_model)
    else:
        selected = 0

    number_buffer = ""

    try:
        _stdout_write("\033[?1049h\033[?25l")

        with _RawTerminal():
            while True:
                visible = _picker_filter_models(models, query)

                if visible:
                    selected = max(0, min(selected, len(visible) - 1))
                    if number_buffer:
                        try:
                            idx = int(number_buffer) - 1
                            if 0 <= idx < len(visible):
                                selected = idx
                        except ValueError:
                            number_buffer = ""
                else:
                    selected = 0

                _render_model_picker(
                    provider=provider,
                    all_models=models,
                    visible=visible,
                    selected=selected,
                    query=query,
                    current_model=current_model,
                    number_buffer=number_buffer,
                )

                key = _read_picker_key()
                if not key:
                    continue

                if key == "UP":
                    number_buffer = ""
                    if visible:
                        selected = (selected - 1) % len(visible)

                elif key == "DOWN":
                    number_buffer = ""
                    if visible:
                        selected = (selected + 1) % len(visible)

                elif key == "HOME":
                    number_buffer = ""
                    if visible:
                        selected = 0

                elif key == "END":
                    number_buffer = ""
                    if visible:
                        selected = len(visible) - 1

                elif key == "PGUP":
                    number_buffer = ""
                    if visible:
                        selected = max(0, selected - 10)

                elif key == "PGDN":
                    number_buffer = ""
                    if visible:
                        selected = min(len(visible) - 1, selected + 10)

                elif key == "BACKSPACE":
                    if number_buffer:
                        number_buffer = number_buffer[:-1]
                    elif query:
                        query = query[:-1]
                        selected = 0

                elif key == "ENTER":
                    if not visible:
                        continue

                    if number_buffer:
                        try:
                            idx = int(number_buffer) - 1
                        except ValueError:
                            idx = -1
                        number_buffer = ""
                        if 0 <= idx < len(visible):
                            return visible[idx]
                        continue

                    return visible[selected]

                elif key == "ESC":
                    return None

                elif len(key) == 1 and key.isdigit():
                    if query:
                        number_buffer = ""
                        query += key
                        selected = 0
                    else:
                        if len(visible) <= 9:
                            idx = int(key) - 1
                            if 0 <= idx < len(visible):
                                return visible[idx]
                        if len(number_buffer) < 4:
                            number_buffer += key
                        try:
                            idx = int(number_buffer) - 1
                            if 0 <= idx < len(visible):
                                selected = idx
                        except ValueError:
                            number_buffer = ""

                elif len(key) == 1 and key.isprintable():
                    number_buffer = ""
                    query += key
                    selected = 0

    except KeyboardInterrupt:
        return None
    finally:
        _stdout_write("\033[?25h\033[?1049l")


# ─────────────────────────────────────────────────────────────────────────────
# PAYLOADS
# ─────────────────────────────────────────────────────────────────────────────

def build_user_message(text: str, image: ImageAttachment, provider: str, is_openai_compat: bool) -> Message:
    if image.attached:
        if not is_openai_compat:
            return {
                "role": "user",
                "parts":[
                    {"text": text},
                    {"inlineData": {"mimeType": image.mime, "data": image.base64}},
                ],
            }
        if provider == "ollama":
            return {"role": "user", "content": text, "images": [image.base64]}
        return {
            "role": "user",
            "content":[
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:{image.mime};base64,{image.base64}"}},
            ],
        }

    if not is_openai_compat:
        return {"role": "user", "parts":[{"text": text}]}
    return {"role": "user", "content": text}


def build_payload(
    provider: str,
    model_id: str,
    history: History,
    is_openai_compat: bool,
    enable_tools: bool,
    enable_thinking: bool,
) -> dict[str, Any]:
    if not is_openai_compat:
        contents =[m for m in history if m.get("role") != "system"]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": DEFAULT_TEMPERATURE,
                "maxOutputTokens": DEFAULT_MAX_TOKENS,
                "topP": DEFAULT_TOP_P,
            },
        }
        if SYSTEM_PROMPT:
            payload["systemInstruction"] = {"parts": [{"text": SYSTEM_PROMPT}]}
        if enable_tools:
            payload["tools"] = GEMINI_TOOLS_SCHEMA
        return payload

    out: dict[str, Any] = {
        "model": model_id,
        "messages": history,
        "temperature": DEFAULT_TEMPERATURE,
        "stream": True,
    }
    if enable_tools:
        out["tools"] = OPENAI_TOOLS_SCHEMA

    if provider == "ollama":
        out["think"] = enable_thinking
        out["options"] = {
            "num_predict": DEFAULT_MAX_TOKENS,
            "top_p": DEFAULT_TOP_P,
        }
    elif provider == "cerebras":
        out["max_completion_tokens"] = DEFAULT_MAX_TOKENS
        out["top_p"] = DEFAULT_TOP_P
    elif provider != "together":
        out["max_tokens"] = DEFAULT_MAX_TOKENS
        out["top_p"] = DEFAULT_TOP_P

    return out


# ─────────────────────────────────────────────────────────────────────────────
# STREAM PARSING
# ─────────────────────────────────────────────────────────────────────────────

class _ChunkResult:
    __slots__ = ("text", "think", "finish", "error", "tool_chunks")

    def __init__(self) -> None:
        self.text = ""
        self.think = ""
        self.finish = ""
        self.error = ""
        self.tool_chunks: list[dict[str, Any]] =[]


def _parse_openai_chunk(obj: dict[str, Any], provider: str) -> _ChunkResult:
    r = _ChunkResult()

    if provider == "ollama":
        msg_obj = obj.get("message", {})
        r.text = msg_obj.get("content") or ""
        r.think = msg_obj.get("thinking") or ""
        if obj.get("done") is True:
            r.finish = obj.get("done_reason") or "stop"
        for tc in msg_obj.get("tool_calls",[]):
            fn = tc.get("function", {})
            args_raw = fn.get("arguments", "")
            if isinstance(args_raw, dict):
                args_raw = json.dumps(args_raw)
            elif not isinstance(args_raw, str):
                args_raw = "{}"
            r.tool_chunks.append({
                "index": -1,
                "id": None,
                "function": {"name": fn.get("name", ""), "arguments": args_raw},
            })
        return r

    choice = (obj.get("choices") or [{}])[0]
    delta = choice.get("delta", {})
    r.text = delta.get("content") or ""
    r.think = delta.get("reasoning") or ""
    r.finish = choice.get("finish_reason") or ""
    for tc_chunk in delta.get("tool_calls",[]):
        r.tool_chunks.append({
            "index": tc_chunk.get("index", 0),
            "id": tc_chunk.get("id"),
            "function": {
                "name": tc_chunk.get("function", {}).get("name", ""),
                "arguments": tc_chunk.get("function", {}).get("arguments", ""),
            },
        })
    return r


def _parse_gemini_chunk(obj: dict[str, Any]) -> _ChunkResult:
    r = _ChunkResult()
    candidate = (obj.get("candidates") or [{}])[0]

    pf = obj.get("promptFeedback")
    if pf and pf.get("blockReason"):
        r.error = f"Content blocked (reason: {pf['blockReason']})"
        return r

    content_obj = candidate.get("content", {})
    for part in content_obj.get("parts",[]):
        if "text" in part:
            r.text += part["text"]
        if "functionCall" in part:
            fc = part["functionCall"]
            r.tool_chunks.append({"name": fc.get("name", ""), "args": fc.get("args", {})})

    r.finish = candidate.get("finishReason", "")
    return r


# ─────────────────────────────────────────────────────────────────────────────
# STREAM RENDERER
# ─────────────────────────────────────────────────────────────────────────────

class StreamRenderer:
    def __init__(self, enable_thinking: bool) -> None:
        self.full_text = ""
        self.enable_thinking = enable_thinking
        self.is_thinking = False
        self._in_think_display = False
        self._line_buffer = ""
        self._line_prefix = ""
        self._current_rows = 0
        self._used_ai_prefix = False
        self.first_chunk = True

    def _clear_current_block(self) -> None:
        if self._current_rows <= 0:
            return
        _stdout_write("\r")
        for _ in range(self._current_rows - 1):
            _stdout_write("\x1b[1A")
        for i in range(self._current_rows):
            _stdout_write("\033[2K")
            if i < self._current_rows - 1:
                _stdout_write("\n")
        for _ in range(self._current_rows - 1):
            _stdout_write("\x1b[1A")
        _stdout_write("\r")

    def _draw_current_line(self, final: bool) -> None:
        if not self._used_ai_prefix:
            self._line_prefix = f"{C.AI}AI:{C.RESET}  "
            self._used_ai_prefix = True
        rendered = MD_RENDERER.render_line(self._line_buffer) if final else MD_RENDERER.preview_line(self._line_buffer)
        display = f"{self._line_prefix}{C.AI}{rendered}{C.RESET}"
        self._clear_current_block()
        _stdout_write(display)
        self._current_rows = _wrapped_rows(display)

    def _commit_current_line(self) -> None:
        self._draw_current_line(final=True)
        _stdout_write("\n")
        self._line_buffer = ""
        self._line_prefix = ""
        self._current_rows = 0

    def _flush_text(self, text: str) -> None:
        while text:
            idx = text.find("\n")
            if idx != -1:
                self._line_buffer += text[:idx]
                self._commit_current_line()
                text = text[idx + 1:]
            else:
                self._line_buffer += text
                self._draw_current_line(final=False)
                text = ""

    def feed_thinking(self, think_tok: str) -> None:
        if not think_tok or not self.enable_thinking:
            return
        if self.first_chunk:
            _stdout_write(C.CLR)
            _stdout_write(f"{C.AI}AI:{C.RESET}  ")
            self._used_ai_prefix = True
            self.first_chunk = False
        if not self._in_think_display:
            _stdout_write(f"{C.THINK}[Thinking]\n┃ {C.RESET}{C.THINK}")
            self._in_think_display = True
            
        indented_think = think_tok.replace("\n", f"\n{C.THINK}┃ {C.RESET}{C.THINK}")
        _stdout_write(f"{C.THINK}{indented_think}{C.RESET}")

    def _write_think(self, text: str) -> None:
        if not self.enable_thinking:
            return
        indented = text.replace("\n", f"\n{C.THINK}┃ {C.RESET}{C.THINK}")
        _stdout_write(f"{C.THINK}{indented}{C.RESET}")

    def feed_text(self, text_tok: str) -> None:
        if not text_tok:
            return
        if self.first_chunk:
            _stdout_write(C.CLR)
            self.first_chunk = False
        if self._in_think_display:
            _stdout_write(f"{C.RESET}\n\n")
            self._in_think_display = False

        self.full_text += text_tok
        remaining = text_tok

        while remaining:
            if self.is_thinking:
                close = remaining.find("</think")
                if close != -1:
                    self._write_think(remaining[:close])
                    _stdout_write(f"{C.RESET}\n\n")
                    self.is_thinking = False
                    self._in_think_display = False
                    after = remaining[close + 7:]
                    b = after.find(">")
                    remaining = after[b + 1:] if b != -1 else ""
                else:
                    self._write_think(remaining)
                    remaining = ""
            else:
                open_idx = remaining.find("<think")
                if open_idx != -1:
                    before = remaining[:open_idx]
                    if before:
                        self._flush_text(before)
                    if self._line_buffer:
                        self._commit_current_line()
                    if self.enable_thinking:
                        _stdout_write(f"{C.THINK}[Thinking]\n┃ {C.RESET}{C.THINK}")
                        self._in_think_display = True
                    self.is_thinking = True
                    after = remaining[open_idx + 6:]
                    b = after.find(">")
                    remaining = after[b + 1:] if b != -1 else ""
                else:
                    self._flush_text(remaining)
                    remaining = ""

    def finalize(self) -> None:
        if self._line_buffer:
            self._commit_current_line()
        MD_RENDERER.in_code_block = False


# ─────────────────────────────────────────────────────────────────────────────
# STREAM RESPONSE
# ─────────────────────────────────────────────────────────────────────────────

def stream_response(
    provider: str,
    model_id: str,
    history: History,
    is_openai_compat: bool,
    api_key: str,
    enable_tools: bool,
    enable_thinking: bool,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    payload = build_payload(provider, model_id, history, is_openai_compat, enable_tools, enable_thinking)
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ep = ENDPOINTS[provider]

    if not is_openai_compat:
        url = f"{ep['chat_base']}{model_id}:streamGenerateContent?key={api_key}&alt=sse"
        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
    else:
        req = _build_request(ep["chat"], api_key, provider, data=payload_bytes)

    _stdout_write(f"{C.AI}AI:{C.RESET} {C.INFO}(💬 Waiting…){C.RESET}")

    renderer = StreamRenderer(enable_thinking)
    finish_reason = ""
    error_msg = ""
    interrupted = False
    done_received = False

    oai_tool_calls: dict[int, dict[str, Any]] = {}
    gem_tool_calls: list[dict[str, Any]] =[]

    try:
        with _request_with_retry(req, timeout=REQUEST_TIMEOUT) as resp:
            buffer = ""
            while not done_received:
                try:
                    raw = _read_chunk(resp)
                except KeyboardInterrupt:
                    interrupted = True
                    cprint(f"\n{C.WARN}(Stream interrupted){C.RESET}")
                    break

                if not raw:
                    break

                buffer += raw.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")

                    json_chunk = ""
                    if line.startswith("data: "):
                        json_chunk = line[6:].strip()
                        if json_chunk == "[DONE]":
                            done_received = True
                            break
                    elif line.startswith("{"):
                        json_chunk = line

                    if not json_chunk:
                        continue

                    try:
                        obj = json.loads(json_chunk)
                    except json.JSONDecodeError:
                        continue

                    chunk_err = None
                    if isinstance(obj.get("error"), dict):
                        chunk_err = obj["error"].get("message", str(obj["error"]))
                    elif isinstance(obj.get("error"), str):
                        chunk_err = obj["error"]
                    elif isinstance(obj.get("detail"), str):
                        chunk_err = obj["detail"]

                    if chunk_err:
                        error_msg = f"API error: {chunk_err}"
                        done_received = True
                        break

                    if is_openai_compat:
                        cr = _parse_openai_chunk(obj, provider)
                        for tc in cr.tool_chunks:
                            idx = tc["index"]
                            if idx == -1:
                                real_idx = len(oai_tool_calls)
                                oai_tool_calls[real_idx] = {
                                    "id": f"call_{real_idx}",
                                    "type": "function",
                                    "function": tc["function"],
                                }
                            elif idx not in oai_tool_calls:
                                oai_tool_calls[idx] = {
                                    "id": tc.get("id") or f"call_{idx}",
                                    "type": "function",
                                    "function": {
                                        "name": tc["function"]["name"],
                                        "arguments": tc["function"]["arguments"],
                                    },
                                }
                            else:
                                entry = oai_tool_calls[idx]["function"]
                                if tc["function"]["name"]:
                                    entry["name"] += tc["function"]["name"]
                                if tc["function"]["arguments"]:
                                    entry["arguments"] += tc["function"]["arguments"]
                    else:
                        cr = _parse_gemini_chunk(obj)
                        if cr.error:
                            error_msg = cr.error
                            done_received = True
                            break
                        for gtc in cr.tool_chunks:
                            gem_tool_calls.append(gtc)

                    if cr.finish and not finish_reason:
                        finish_reason = cr.finish

                    renderer.feed_thinking(cr.think)
                    renderer.feed_text(cr.text)

                    if provider == "ollama" and finish_reason:
                        done_received = True
                        break

    except KeyboardInterrupt:
        interrupted = True
        if renderer.first_chunk:
            _stdout_write(C.CLR)
        cprint(f"\n{C.WARN}(Request interrupted){C.RESET}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if renderer.first_chunk:
            _stdout_write(C.CLR)
        error_msg = f"HTTP {exc.code}: {truncate(body, 200)}"
    except urllib.error.URLError as exc:
        if renderer.first_chunk:
            _stdout_write(C.CLR)
        error_msg = f"Network error: {exc.reason}"
    except OSError as exc:
        if renderer.first_chunk:
            _stdout_write(C.CLR)
        error_msg = f"Connection error: {exc}"

    renderer.finalize()

    tool_calls_out: list[dict[str, Any]] =[]
    if is_openai_compat:
        for idx in sorted(oai_tool_calls):
            tool_calls_out.append(oai_tool_calls[idx])
    else:
        for gtc in gem_tool_calls:
            tool_calls_out.append({
                "id": gtc.get("name", "call_gemini"),
                "type": "function",
                "function": {
                    "name": gtc.get("name", ""),
                    "arguments": json.dumps(gtc.get("args", {})),
                },
            })

    if renderer.first_chunk and not error_msg and not interrupted:
        _stdout_write(C.CLR)
        if tool_calls_out:
            cprint(f"{C.AI}AI:{C.RESET} {C.TOOL}🛠️  [Calling tools…]{C.RESET}")
        else:
            cprint(f"{C.AI}AI:{C.RESET} {C.INFO}(empty response){C.RESET}")
    elif not renderer.first_chunk:
        _stdout_write(f"{C.RESET}\n")

    if _is_length_finish(finish_reason):
        eprint(f"{C.WARN}(Response truncated: output limit reached: {finish_reason}){C.RESET}")

    if error_msg:
        if renderer.full_text or tool_calls_out:
            eprint(f"{C.WARN}(Stream ended after partial output: {error_msg}){C.RESET}")
            full_text = renderer.full_text[:MAX_MESSAGE_LENGTH]
            clean = strip_think_tags(full_text)
            return (clean if clean else ""), tool_calls_out
        cprint(f"{C.ERROR}{error_msg}{C.RESET}")
        return None,[]

    full_text = renderer.full_text[:MAX_MESSAGE_LENGTH]
    clean = strip_think_tags(full_text)
    if not clean and not tool_calls_out and not interrupted:
        return None,[]
    return (clean if clean else ""), tool_calls_out


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _append_assistant_turn(
    history: History,
    ai_text: Optional[str],
    tool_calls: list[dict[str, Any]],
    provider: str,
    is_openai_compat: bool,
) -> None:
    if not is_openai_compat:
        parts: list[dict[str, Any]] =[]
        if ai_text:
            parts.append({"text": ai_text})
        for tc in tool_calls:
            parts.append({
                "functionCall": {
                    "name": tc["function"]["name"],
                    "args": _args_to_obj(tc["function"]["arguments"]),
                }
            })
        if parts:
            history.append({"role": "model", "parts": parts})
        return

    if provider == "ollama":
        asst_msg: Message = {"role": "assistant", "content": ai_text or ""}
        if tool_calls:
            asst_msg["tool_calls"] =[
                {
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": _args_to_obj(tc["function"]["arguments"]),
                    }
                }
                for tc in tool_calls
            ]
        history.append(asst_msg)
        return

    asst_msg = {"role": "assistant", "content": ai_text or ""}
    if tool_calls:
        asst_msg["tool_calls"] = tool_calls
    history.append(asst_msg)


def _append_tool_results(
    history: History,
    tool_calls: list[dict[str, Any]],
    results: list[str],
    provider: str,
    is_openai_compat: bool,
) -> None:
    if not is_openai_compat:
        parts =[
            {
                "functionResponse": {
                    "name": tc["function"]["name"],
                    "response": {"result": result},
                }
            }
            for tc, result in zip(tool_calls, results)
        ]
        history.append({"role": "user", "parts": parts})
        return

    if provider == "ollama":
        for result in results:
            history.append({"role": "tool", "content": result})
        return

    for tc, result in zip(tool_calls, results):
        history.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "name": tc["function"]["name"],
            "content": result,
        })


def _extract_display_text(msg: Message) -> str:
    role = msg.get("role", "")
    if role == "tool":
        return f"[🛠️ {msg.get('name', 'tool')}] {truncate(msg.get('content', ''), 120)}"
    if "tool_calls" in msg:
        names =[tc.get("function", tc).get("name", "?") for tc in msg["tool_calls"]]
        return ((msg.get("content") or "") + f"[🛠️ → {', '.join(names)}]").strip()

    raw = msg.get("content") or msg.get("parts", [{}])
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and raw:
        if isinstance(raw[0], dict):
            if "text" in raw[0]:
                return raw[0]["text"]
            if "functionCall" in raw[0]:
                return f"[🛠️ → {', '.join(p['functionCall'].get('name', '?') for p in raw if 'functionCall' in p)}]"
            if "functionResponse" in raw[0]:
                return "[🛠️ results]"
            for p in raw:
                if isinstance(p, dict) and p.get("type") == "text":
                    return p.get("text", "")
            has_image = any(isinstance(p, dict) and (p.get("type") == "image_url" or "inlineData" in p) for p in raw)
            return "[📎 image]" if has_image else "[content]"
    return "[empty]"


# ─────────────────────────────────────────────────────────────────────────────
# ENFORCEMENT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_urls_from_tool_output(text: str) -> list[str]:
    return re.findall(r"^URL:\s*(https?://\S+)", text or "", flags=re.M)


def _extract_domains_from_tool_output(text: str) -> set[str]:
    out: set[str] = set()
    for url in _extract_urls_from_tool_output(text):
        dom = _domain_of(url)
        if dom:
            out.add(dom)
    return out


def _question_needs_web_research(text: str) -> bool:
    q = (text or "").strip().lower()
    if not q:
        return False
    triggers = (
        "latest", "current", "today", "recent", "now",
        "version", "release", "pricing", "price", "cost",
        "compare", "comparison", "vs ", "versus",
        "benchmark", "docs", "documentation", "api",
        "install", "error", "traceback", "issue",
        "news", "announced", "official",
    )
    if any(t in q for t in triggers):
        return True
    if q.endswith("?") and len(q.split()) >= 6:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# INPUT
# ─────────────────────────────────────────────────────────────────────────────

def read_multiline_input(initial_prompt: str, cont_prompt: str = "") -> Optional[str]:
    if not cont_prompt:
        cont_prompt = f"  {_rl(C.DIM)}..{_rl(C.RESET)} "
    try:
        line = input(initial_prompt)
    except (EOFError, KeyboardInterrupt):
        return None

    lines: list[str] =[]
    while line.endswith("\\"):
        lines.append(line[:-1])
        try:
            line = input(cont_prompt)
        except (EOFError, KeyboardInterrupt):
            lines.append("")
            return "\n".join(lines).strip()
    lines.append(line)
    return "\n".join(lines).strip()


def read_paste_input(prefix: str = "") -> Optional[str]:
    cprint(f"{C.INFO}Paste mode — end with {C.BOLD}---{C.RESET}{C.INFO} on its own line to send:{C.RESET}")
    lines: list[str] =[]
    prompt = f"  {_rl(C.DIM)}│{_rl(C.RESET)} "
    while True:
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            cprint(f"\n{C.INFO}(Paste cancelled){C.RESET}")
            return "\n".join(lines).strip() if lines else None
        if line.strip() == "---":
            break
        lines.append(line)
    body = "\n".join(lines).strip()
    if not body:
        return None
    return f"{prefix}\n\n{body}" if prefix else body


# ─────────────────────────────────────────────────────────────────────────────
# HELP
# ─────────────────────────────────────────────────────────────────────────────

def print_usage() -> None:
    me = Path(sys.argv[0]).name
    cprint(f"""
{C.INFO}Usage:{C.RESET}
  python {me} <provider>[filter]...

{C.INFO}Providers:{C.RESET}
  gemini  openrouter  groq  together  cerebras  novita  ollama

{C.INFO}Commands:{C.RESET}
  /history
  /model [filter]
  /save <name>
  /load <name>
  /clear
  /upload <path>
  /image
  /clearimage
  /paste [text]
  /togglethinking
  /toggletools
  /help
  quit / exit
""")


def print_chat_help() -> None:
    cprint(f"""{C.INFO}Commands:{C.RESET}
  {C.BOLD}/history{C.RESET}
  {C.BOLD}/model [filter]{C.RESET}
  {C.BOLD}/save <name>{C.RESET}
  {C.BOLD}/load <name>{C.RESET}
  {C.BOLD}/clear{C.RESET}
  {C.BOLD}/upload <path>{C.RESET}
  {C.BOLD}/image{C.RESET}
  {C.BOLD}/clearimage{C.RESET}
  {C.BOLD}/paste [text]{C.RESET}
  {C.BOLD}/togglethinking{C.RESET}
  {C.BOLD}/toggletools{C.RESET}
  {C.BOLD}/help{C.RESET}
  {C.BOLD}quit{C.RESET} / {C.BOLD}exit{C.RESET}

{C.TOOL}Tools:{C.RESET}
  get_time · calculator · web_search · web_research · fetch_url · wikipedia
""")


# ─────────────────────────────────────────────────────────────────────────────
# CHAT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def chat_loop(
    provider: str,
    model_id: str,
    is_openai_compat: bool,
    api_key: str,
    enable_tools: bool,
    enable_thinking: bool,
    filters: list[str],
) -> None:
    if _READLINE_AVAILABLE:
        readline.set_history_length(1000)
        if HISTORY_FILE.exists():
            try:
                readline.read_history_file(str(HISTORY_FILE))
            except OSError:
                pass
        atexit.register(_save_readline_history)

    history: History = init_history(is_openai_compat)
    image = ImageAttachment()
    thinking_on = enable_thinking
    tools_on = enable_tools

    def _banner() -> None:
        term_cols, _ = shutil.get_terminal_size((85, 24))
        width = min(term_cols - 2, 90)

        def _line(left: str, right: str = "") -> str:
            vis_len = _visible_len(left) + _visible_len(right)
            pad = max(1, width - vis_len - 4)
            return f"│ {left}{' ' * pad}{right} │"

        cprint(f"\n╭{'─' * (width - 2)}╮")
        cprint(_line(f"{C.BOLD}AI Chat CLI{C.RESET}"))
        cprint(f"├{'─' * (width - 2)}┤")

        cprint(_line(f"{C.INFO}Provider:{C.RESET} {provider.upper()}", f"{C.INFO}Model:{C.RESET} {model_id}"))
        cprint(_line(f"{C.INFO}History:{C.RESET}  last {MAX_HISTORY_MESSAGES} turns", f"{C.INFO}Tokens:{C.RESET} {DEFAULT_MAX_TOKENS}  {C.INFO}Temp:{C.RESET} {DEFAULT_TEMPERATURE}"))

        status = f"{C.AI}active{C.RESET}" if SYSTEM_PROMPT else "inactive"
        cprint(_line(f"{C.INFO}System prompt:{C.RESET} {status}"))

        think_st = f"{C.BOLD}{C.THINK}enabled{C.RESET}" if thinking_on else "disabled"
        cprint(_line(f"{C.INFO}Thinking output:{C.RESET} {think_st}", f"{C.DIM}/togglethinking{C.RESET}"))

        tool_st = f"{C.BOLD}{C.TOOL}enabled{C.RESET}" if tools_on else "disabled"
        cprint(_line(f"{C.INFO}Tool calling:{C.RESET}    {tool_st}", f"{C.DIM}/toggletools{C.RESET}"))

        cprint(f"├{'─' * (width - 2)}┤")
        cprint(_line(f"{C.DIM}Type {C.BOLD}quit{C.RESET}{C.DIM} to exit • {C.BOLD}/model{C.RESET}{C.DIM} to switch • {C.BOLD}/help{C.RESET}{C.DIM} for commands{C.RESET}"))
        cprint(f"╰{'─' * (width - 2)}╯\n")

    _banner()

    while True:
        img_tag = f"[{_rl(C.IMAGE)}📎 {Path(image.path).name}{_rl(C.RESET)}] " if image.attached else ""
        prompt = f"{img_tag}{_rl(C.BOLD)}{_rl(C.USER)}You:{_rl(C.RESET)} "
        raw = read_multiline_input(prompt)
        if raw is None:
            cprint(f"\n{C.INFO}Ending session.{C.RESET}")
            break

        user_input = raw
        if user_input.lower() in ("quit", "exit"):
            break

        if user_input.startswith("/"):
            parts = user_input.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1].strip() if len(parts) > 1 else ""
            handled = True

            if cmd == "/paste":
                pasted = read_paste_input(prefix=args)
                if pasted:
                    user_input = pasted
                    handled = False
                else:
                    cprint(f"{C.INFO}Nothing to send.{C.RESET}")

            elif cmd == "/help":
                print_chat_help()

            elif cmd == "/model":
                inline_filters = args.split() if args else filters
                new_model = select_model_interactive(provider, api_key, inline_filters, current_model=model_id)
                if new_model is not None:
                    old = model_id
                    model_id = new_model
                    if old == model_id:
                        cprint(f"{C.INFO}Already using {model_id}.{C.RESET}")
                    else:
                        cprint(f"{C.INFO}Switched model: {old} → {C.BOLD}{model_id}{C.RESET}")

            elif cmd == "/upload":
                if not args:
                    eprint(f"{C.IMAGE}Usage: /upload <image_path>{C.RESET}")
                else:
                    image.load(args)

            elif cmd == "/image":
                if image.attached:
                    cprint(f"{C.IMAGE}Attached: {image.path} ({image.mime}){C.RESET}")
                else:
                    cprint(f"{C.IMAGE}No image attached.{C.RESET}")

            elif cmd == "/clearimage":
                image.clear()
                cprint(f"{C.IMAGE}Image cleared.{C.RESET}")

            elif cmd == "/togglethinking":
                thinking_on = not thinking_on
                cprint(f"{C.INFO}Thinking output {'enabled' if thinking_on else 'disabled'}.{C.RESET}")

            elif cmd == "/toggletools":
                tools_on = not tools_on
                cprint(f"{C.INFO}Tool calling {'enabled' if tools_on else 'disabled'}.{C.RESET}")

            elif cmd == "/history":
                cprint(f"{C.INFO}── History ({len(history)} messages) ─────────────────────{C.RESET}")
                if not history:
                    cprint("  (empty)")
                for msg in history:
                    role = msg.get("role", "?")
                    colour = {
                        "user": C.USER, "assistant": C.AI, "model": C.AI,
                        "system": C.WARN, "tool": C.TOOL,
                    }.get(role, C.DIM)
                    cprint(f"  {colour}[{role}]{C.RESET} {truncate(_extract_display_text(msg), 500)}")
                cprint(f"{C.INFO}────────────────────────────────────────────────────{C.RESET}")

            elif cmd == "/save":
                if not args:
                    eprint(f"{C.WARN}Usage: /save <name>{C.RESET}")
                elif validate_session_name(args):
                    save_session(args, history)

            elif cmd == "/load":
                if not args:
                    eprint(f"{C.WARN}Usage: /load <name>{C.RESET}")
                elif validate_session_name(args):
                    loaded = load_session(args)
                    if loaded is not None:
                        history = loaded

            elif cmd == "/clear":
                clear_sessions()

            else:
                eprint(f"{C.WARN}Unknown command '{cmd}'. Type /help for a list.{C.RESET}")

            if handled:
                continue

        if not user_input and not image.attached:
            continue
        if not user_input and image.attached:
            user_input = "Describe this image in detail."
        if len(user_input) > MAX_MESSAGE_LENGTH:
            eprint(f"{C.ERROR}Message too long ({len(user_input):,} chars).{C.RESET}")
            continue

        eprint(f"{C.INFO}[Sending…]{C.RESET}")
        user_msg = build_user_message(user_input, image, provider, is_openai_compat)
        image.clear()

        history_snapshot = list(history)
        history.append(user_msg)

        compact_from = len(history)
        had_tool_calls = False
        final_ai_text: Optional[str] = None
        tool_iter = 0
        tool_loop_ok = True

        reviewed_domains: set[str] = set()
        original_user_input = user_input
        research_enforcements = 0

        try:
            while True:
                tool_iter += 1
                if tool_iter > MAX_TOOL_ITERATIONS:
                    eprint(f"{C.ERROR}Tool loop limit reached.{C.RESET}")
                    break

                history = truncate_history(history, is_openai_compat)

                ai_text, tool_calls = stream_response(
                    provider, model_id, history, is_openai_compat,
                    api_key, tools_on, thinking_on,
                )

                if ai_text is None and not tool_calls:
                    if history and history[-1].get("role") == "user":
                        history.pop()
                    tool_loop_ok = False
                    break

                _append_assistant_turn(history, ai_text, tool_calls, provider, is_openai_compat)

                if tool_calls and tools_on:
                    had_tool_calls = True
                    results: list[str] =[]

                    for tc in tool_calls:
                        fn_name = tc["function"]["name"]
                        fn_args = tc["function"]["arguments"]
                        display = _args_display(fn_args)

                        cprint(f"\n{C.TOOL}⚡ Tool: {fn_name}({display}){C.RESET}")
                        result = execute_tool(fn_name, fn_args)
                        cprint(f"{C.DIM}   → {truncate(result, 300)}{C.RESET}")

                        if fn_name in {"web_research", "fetch_url", "wikipedia"}:
                            reviewed_domains.update(_extract_domains_from_tool_output(result))

                        results.append(result)

                    _append_tool_results(history, tool_calls, results, provider, is_openai_compat)
                    continue

                final_ai_text = ai_text

                if (
                    tools_on
                    and _question_needs_web_research(original_user_input)
                    and len(reviewed_domains) < RESEARCH_MIN_SOURCES
                    and research_enforcements < 2
                    and tool_iter < MAX_TOOL_ITERATIONS
                ):
                    research_enforcements += 1
                    reminder = (
                        f"Do not answer yet. Continue researching until you have reviewed at least "
                        f"{RESEARCH_MIN_SOURCES} independent web sources if possible. "
                        f"You have reviewed {len(reviewed_domains)} so far. "
                        f"Prefer web_research(query={json.dumps(original_user_input)}, max_sources={RESEARCH_TARGET_SOURCES}). "
                        f"If fewer than {RESEARCH_MIN_SOURCES} sources are actually reachable, say that explicitly."
                    )
                    eprint(f"{C.INFO}(Research enforcement: {len(reviewed_domains)}/{RESEARCH_MIN_SOURCES} sources reviewed; asking model to continue){C.RESET}")
                    if not is_openai_compat:
                        history.append({"role": "user", "parts": [{"text": reminder}]})
                    else:
                        history.append({"role": "user", "content": reminder})
                    final_ai_text = None
                    continue

                break

        except Exception as exc:
            eprint(f"{C.ERROR}Unexpected error during tool loop: {exc}{C.RESET}")
            history[:] = history_snapshot
            tool_loop_ok = False

        if tool_loop_ok and had_tool_calls and final_ai_text is not None:
            if not is_openai_compat:
                clean_final: Message = {"role": "model", "parts":[{"text": final_ai_text}]}
            else:
                clean_final = {"role": "assistant", "content": final_ai_text}
            history[compact_from:] =[clean_final]

        cprint("")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    for name, val, lo, hi in[
        ("DEFAULT_TEMPERATURE", DEFAULT_TEMPERATURE, 0, 2),
        ("DEFAULT_TOP_P", DEFAULT_TOP_P, 0, 1),
        ("DEFAULT_MAX_TOKENS", DEFAULT_MAX_TOKENS, 1, 1_000_000),
    ]:
        if not (lo <= val <= hi):
            sys.exit(f"Config error: {name}={val} must be between {lo} and {hi}")

    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print_usage()
        sys.exit(0)

    provider = argv[0].lower()
    filters = argv[1:]

    if provider not in VALID_PROVIDERS:
        cprint(f"{C.ERROR}Unknown provider '{provider}'. Choose from: {', '.join(VALID_PROVIDERS)}{C.RESET}")
        sys.exit(1)

    api_key = API_KEYS.get(provider, "")
    if not check_placeholder_key(api_key, provider):
        sys.exit(1)

    is_openai_compat = provider != "gemini"

    # Interactive Selectors
    enable_thinking = _interactive_confirm("Enable thinking/reasoning?", default=True, color=C.THINK)
    enable_tools = _interactive_confirm("Enable tool calling?", default=False, color=C.TOOL)

    model_id = select_model_interactive(provider, api_key, filters)
    if model_id is None:
        sys.exit(1)

    cprint(f"{C.INFO}Using model:{C.RESET} {model_id}\n")
    signal.signal(signal.SIGINT, signal.default_int_handler)

    try:
        chat_loop(
            provider, model_id, is_openai_compat, api_key,
            enable_tools, enable_thinking, filters,
        )
    except KeyboardInterrupt:
        _PROGRESS.stop()
        cprint(f"\n{C.WARN}Interrupted.{C.RESET}")

    cprint("👋 Session ended.")


if __name__ == "__main__":
    main()
