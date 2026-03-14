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
  - Web search via Startpage
  - Live tool progress spinner
  - Live model switching (/model command)
  - History compaction (tool messages auto-collapsed after each exchange)
  - SSRF protection with DNS-pinning
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
    /paste [text]       Multi-line paste mode (end with ---)
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


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
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


# Conversation defaults
MAX_HISTORY_MESSAGES  = 20        # Max user+AI turns kept in context
MAX_MESSAGE_LENGTH    = 50_000    # Max chars for a single message
DEFAULT_TEMPERATURE   = 0.7       # 0–2: higher = more creative
DEFAULT_MAX_TOKENS    = 3000      # Max tokens in each AI reply
DEFAULT_TOP_P         = 0.9       # 0–1: nucleus sampling
SESSION_DIR           = Path.home() / ".chat_sessions"
HISTORY_FILE          = Path.home() / ".ai_cli_history"

# Image support
MAX_IMAGE_SIZE_MB = 20
SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# System prompt (set to "" to disable)
SYSTEM_PROMPT = "You are a helpful assistant running in a command-line interface."

MAX_TOOL_ITERATIONS = 10
TOOL_EXEC_TIMEOUT   = 60

# Request timeouts in seconds
REQUEST_TIMEOUT     = 300   # Streaming chat timeout
MODEL_FETCH_TIMEOUT = 30    # Model listing timeout

FETCH_MAX_CHARS    = 8000
FETCH_MAX_BYTES    = 5 * 1024 * 1024
SEARCH_MAX_RESULTS = 6

MAX_RETRIES          = 3
RETRYABLE_HTTP_CODES = (429, 500, 502, 503, 504)

# User-Agent sent with all HTTP requests
USER_AGENT = "PythonChatCLI/1.4"

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# ─────────────────────────────────────────────────────────────────────────────
#  ANSI COLOURS
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
#  THREAD-SAFE OUTPUT
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
#  SSRF PROTECTION
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
        return False, f"Blocked protocol: {parsed.scheme}:// (only http/https allowed)"
    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"
    try:
        _resolve_and_validate(hostname)
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
#  LATEX RENDERER
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
        text = self._display_pat.sub(
            lambda m: self._convert(m.group(1) or m.group(2)), text
        )
        text = self._inline_pat.sub(lambda m: self._convert(m.group(1)), text)
        return text

    def _convert(self, tex: str) -> str:
        if not tex:
            return ""
        tex = tex.strip()
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


LATEX_RENDERER = LatexRenderer()


# ─────────────────────────────────────────────────────────────────────────────
#  MARKDOWN RENDERER
# ─────────────────────────────────────────────────────────────────────────────

class MarkdownRenderer:
    def __init__(self) -> None:
        self.bold_pat   = re.compile(r'\*\*(.+?)\*\*')
        self.italic_pat = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)')
        self.code_pat   = re.compile(r'`([^`]+)`')
        self.header_pat = re.compile(r'^(#{1,6})\s+(.*)')
        self.in_code_block = False

    def _render_line_impl(self, line: str, in_code_block: bool) -> tuple[str, bool]:
        stripped = line.strip()

        if stripped.startswith("```"):
            new_state = not in_code_block
            lang = stripped[3:].strip()
            if new_state:
                return f"{C.DIM}{'─' * 40} {lang}{C.RESET}", new_state
            return f"{C.DIM}{'─' * 40}{C.RESET}", new_state

        if in_code_block:
            return f"{C.CODE}{line}{C.RESET}", in_code_block

        h_match = self.header_pat.match(line)
        if h_match:
            return f"{C.BOLD}{C.INFO}{h_match.group(2)}{C.RESET}", in_code_block

        line = LATEX_RENDERER.render(line)
        line = self.code_pat.sub(rf'{C.CODE}`\1`{C.RESET}{C.AI}', line)
        line = self.bold_pat.sub(rf'{C.BOLD}\1{C.RESET}{C.AI}', line)
        line = self.italic_pat.sub(rf'{C.ITALIC}\1{C.RESET}{C.AI}', line)
        return line, in_code_block

    def render_line(self, line: str) -> str:
        rendered, new_state = self._render_line_impl(line, self.in_code_block)
        self.in_code_block = new_state
        return rendered

    def preview_line(self, line: str) -> str:
        rendered, _ = self._render_line_impl(line, self.in_code_block)
        return rendered


MD_RENDERER = MarkdownRenderer()


# ─────────────────────────────────────────────────────────────────────────────
#  PROVIDER ENDPOINTS
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
#  HELPERS
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
    msg = ""
    if not key:
        msg = "is empty"
    elif key.startswith("YOUR_") or key.endswith("-HERE") or "..." in key:
        msg = "appears to be a placeholder"
    elif provider == "gemini" and key == "-":
        msg = "is the default placeholder '-'"
    elif provider == "openrouter" and key == "sk-or-v1-":
        msg = "is an incomplete OpenRouter key"
    elif provider == "groq" and key.startswith("gsk_") and len(key) < 10:
        msg = "looks like an incomplete Groq key"
    elif provider == "cerebras" and key == "csk-":
        msg = "is the bare Cerebras prefix"
    elif provider == "novita" and len(key) < 10:
        msg = "is too short to be valid"
    if msg:
        eprint(f"{C.WARN}{'!' * 68}{C.RESET}")
        eprint(f"{C.WARN}!! WARNING: API key for '{provider.upper()}' {msg}.{C.RESET}")
        eprint(f"{C.WARN}!! Edit the API_KEYS dict at the top of this script.{C.RESET}")
        eprint(f"{C.WARN}{'!' * 68}{C.RESET}")
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
    eprint(f"{C.INFO}Filtering with: {' '.join(filters)}  (word-boundary){C.RESET}")
    result: list[str] = []
    for model in models:
        ml = model.lower()
        if all(
            re.search(r"(?:^|[^a-z0-9])" + re.escape(f.lower()) + r"(?:[^a-z0-9]|$)", ml)
            for f in filters
        ):
            result.append(model)
    return result


def _read_chunk(resp: Any, size: int = 8192) -> bytes:
    try:
        return resp.read1(size)
    except AttributeError:
        return resp.read(size)


def _is_length_finish(reason: str) -> bool:
    r = (reason or "").strip()
    return r in {"length", "max_tokens", "MAX_TOKENS", "max_output_tokens"}


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
    obj = {k: v for k, v in obj.items() if k}
    if not obj:
        return ""
    return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))


_BLOCK_REMOVE_RE = re.compile(
    r'<(?:script|style|noscript|svg)[^>]*>.*?</(?:script|style|noscript|svg)>'
    r'|<!--.*?-->',
    re.DOTALL | re.IGNORECASE,
)
_BLOCK_TAG_RE = re.compile(
    r'<(/?)(div|p|br|h[1-6]|li|tr|article|section|main)[^>]*>',
    re.IGNORECASE,
)
_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'[ \t]+')
_BLANK_LINES_RE = re.compile(r'\n[ \t]*\n[\n ]*')
_CSS_COMBINED_RE = re.compile(
    r'\.css-[a-zA-Z0-9_-]+\{[^}]*\}'
    r'|\.[a-zA-Z_][\w-]*\{[^}]*\}'
    r'|@media\s*\([^)]*\)\s*\{[^}]*(?:\{[^}]*\}[^}]*)?\}'
    r'|@[a-zA-Z-]+\s*[^{]*\{[^}]*(?:\{[^}]*\}[^}]*)?\}'
    r'|\{[^}]{0,500}\}',
)

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _clean_html(raw: str) -> str:
    text = _BLOCK_REMOVE_RE.sub(' ', raw)
    text = _BLOCK_TAG_RE.sub('\n', text)
    text = _TAG_RE.sub(' ', text)
    text = _html.unescape(text)
    text = _CSS_COMBINED_RE.sub('', text)
    text = _WHITESPACE_RE.sub(' ', text)
    text = _BLANK_LINES_RE.sub('\n\n', text)
    return text.strip()


def _clean_search_text(text: str) -> str:
    text = _TAG_RE.sub("", text)
    text = _html.unescape(text)
    text = _CSS_COMBINED_RE.sub('', text)
    return text.strip()


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _wrapped_rows(text: str) -> int:
    cols = max(shutil.get_terminal_size((80, 24)).columns, 20)
    vis = max(_visible_len(text), 1)
    return (vis - 1) // cols + 1


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
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


# ─────────────────────────────────────────────────────────────────────────────
#  TOOL PROGRESS SPINNER
# ─────────────────────────────────────────────────────────────────────────────

class _ToolProgress:
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._status = ""
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._start_time = 0.0

    def start(self, label: str) -> None:
        self.stop()
        self._stop_event.clear()
        with self._lock:
            self._status = label
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def update(self, status: str) -> None:
        with self._lock:
            self._status = status

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        _stdout_write(C.CLR)

    def _animate(self) -> None:
        i = 0
        while not self._stop_event.is_set():
            with self._lock:
                status = self._status
            elapsed = time.monotonic() - self._start_time
            timer = f" {C.DIM}({elapsed:.1f}s){C.RESET}"
            frame = self._FRAMES[i % len(self._FRAMES)]
            _stdout_write(f"{C.CLR}{C.TOOL}   {frame} {status}{C.RESET}{timer}")
            self._stop_event.wait(0.08)
            i += 1


_PROGRESS = _ToolProgress()


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP HELPERS WITH RETRY
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
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and retry_after.isdigit():
                wait = min(int(retry_after), 30)
            eprint(f"{C.WARN}HTTP {exc.code} — retrying in {wait}s (attempt {attempt + 1}/{max_retries})…{C.RESET}")
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
#  WEB FETCHER
# ─────────────────────────────────────────────────────────────────────────────

_FETCH_HEADERS_PRIMARY: dict[str, str] = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, identity",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}

_FETCH_HEADERS_FALLBACK: dict[str, str] = {
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
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > FETCH_MAX_BYTES:
                    raise ValueError(
                        f"Response too large: {int(content_length) // (1024*1024)} MB "
                        f"(max {FETCH_MAX_BYTES // (1024*1024)} MB)"
                    )
                raw_bytes = resp.read(FETCH_MAX_BYTES + 1)
                if len(raw_bytes) > FETCH_MAX_BYTES:
                    raw_bytes = raw_bytes[:FETCH_MAX_BYTES]
                raw_bytes = _decompress(raw_bytes, resp.headers.get("Content-Encoding", ""))
                return raw_bytes.decode("utf-8", errors="replace")
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
    raise RuntimeError("Failed to fetch URL")


# ─────────────────────────────────────────────────────────────────────────────
#  IMAGE HANDLING
# ─────────────────────────────────────────────────────────────────────────────

class ImageAttachment:
    def __init__(self) -> None:
        self.path = self.base64 = self.mime = ""

    def clear(self) -> None:
        self.path = self.base64 = self.mime = ""

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
            ext_map = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
            }
            mime = ext_map.get(path.suffix.lower(), "")
        if mime not in SUPPORTED_MIME_TYPES:
            eprint(f"{C.ERROR}Error: Unsupported type '{mime}'. Supported: {', '.join(sorted(SUPPORTED_MIME_TYPES))}{C.RESET}")
            return False
        eprint(f"{C.IMAGE}Encoding image…{C.RESET}")
        try:
            raw = path.read_bytes()
            self.base64 = base64.b64encode(raw).decode("ascii")
            self.mime = mime
            self.path = str(path)
            eprint(f"{C.IMAGE}✓ Attached: {path.name} ({mime}, {len(raw) // 1024} KB){C.RESET}")
            return True
        except OSError as exc:
            eprint(f"{C.ERROR}Error reading image: {exc}{C.RESET}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
#  TOOL / FUNCTION CALLING
# ─────────────────────────────────────────────────────────────────────────────

def tool_get_time(**kwargs: Any) -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_CALC_MAX_EXPONENT = 10_000
_CALC_MAX_RESULT = 1e308

_CALC_ALLOWED_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "gcd": math.gcd,
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
            if fn is operator.pow:
                if isinstance(right, (int, float)) and abs(right) > _CALC_MAX_EXPONENT:
                    raise ValueError(f"Exponent too large: {right} (max ±{_CALC_MAX_EXPONENT})")
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
            func_name = node.func.id
            fn = _CALC_ALLOWED_FUNCS.get(func_name)
            if fn is None:
                raise TypeError(
                    f"Unknown function '{func_name}'. Allowed: {', '.join(sorted(_CALC_ALLOWED_FUNCS))}"
                )
            if node.keywords:
                raise TypeError("Keyword arguments not supported")
            args = [_eval_node(a) for a in node.args]
            if func_name == "factorial":
                if len(args) != 1:
                    raise TypeError("factorial() takes exactly 1 argument")
                if not isinstance(args[0], int) or args[0] < 0:
                    raise ValueError("factorial() requires a non-negative integer")
                if args[0] > 1000:
                    raise ValueError(f"factorial({args[0]}) too large (max 1000)")
            return fn(*args)

        raise TypeError(f"Unsupported expression element: {type(node).__name__}")

    if not expression or not expression.strip():
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
    except Exception as exc:
        return f"Error: {exc}"


def _startpage_search(query: str, limit: int) -> Optional[str]:
    _PROGRESS.update("Connecting to Startpage…")
    opener = _make_opener()
    home_headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, identity",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        home_req = urllib.request.Request("https://www.startpage.com/", headers=home_headers)
        with opener.open(home_req, timeout=10) as resp:
            resp.read(500_000)
    except Exception:
        pass

    _PROGRESS.update(f"Searching: {truncate(query, 40)}")
    post_data = urllib.parse.urlencode({
        "q": query, "cat": "web", "cmd": "process_search",
        "language": "english", "engine0": "v1all",
    }).encode()
    post_headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, identity",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.startpage.com",
        "Referer": "https://www.startpage.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        search_req = urllib.request.Request(
            "https://www.startpage.com/do/search",
            data=post_data, headers=post_headers, method="POST",
        )
        with opener.open(search_req, timeout=15) as resp:
            raw_bytes = resp.read(FETCH_MAX_BYTES)
            raw_bytes = _decompress(raw_bytes, resp.headers.get("Content-Encoding", ""))
            html_text = raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        return None

    if "captcha" in html_text.lower():
        return None

    _PROGRESS.update("Parsing results…")

    entries: list[str] = []
    seen_domains: set[str] = set()
    results_data: list[tuple[str, str, str]] = []

    for m in re.finditer(
        r'<a[^>]+class="[^"]*result-title[^"]*"[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        html_text, re.DOTALL,
    ):
        url = m.group(1)
        title = _clean_search_text(m.group(2))
        snippet = ""
        after = html_text[m.end():m.end() + 2000]
        snip_m = re.search(
            r'class="[^"]*(?:result-description|w-gl__description)[^"]*"[^>]*>(.*?)</(?:p|div|span)>',
            after, re.DOTALL,
        )
        if snip_m:
            snippet = _clean_search_text(snip_m.group(1))
        results_data.append((url, title, snippet))

    if not results_data:
        for m in re.finditer(
            r'href=["\']([^"\']+)["\'][^>]*class="[^"]*result-title[^"]*"[^>]*>(.*?)</a>',
            html_text, re.DOTALL,
        ):
            results_data.append((m.group(1), _clean_search_text(m.group(2)), ""))

    if not results_data:
        for m in re.finditer(
            r'<h[23][^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html_text, re.DOTALL | re.IGNORECASE,
        ):
            url = m.group(1)
            title = _clean_search_text(m.group(2))
            if "startpage.com" not in url and len(title) > 3:
                results_data.append((url, title, ""))

    if not results_data:
        for m in re.finditer(
            r'<a[^>]+href=["\'](http[^"\']+)["\'][^>]*>(.*?)</a>',
            html_text, re.DOTALL,
        ):
            url = m.group(1)
            title = _clean_search_text(m.group(2))
            if "startpage.com" not in url and "google.com/policies" not in url and len(title) > 3:
                results_data.append((url, title, ""))

    for r_url, r_title, r_snip in results_data:
        if len(entries) >= limit:
            break
        if not r_url.startswith("http") or "startpage.com" in r_url:
            continue
        if not r_title or len(r_title) < 3:
            continue
        try:
            domain = urllib.parse.urlparse(r_url).netloc
        except Exception:
            domain = r_url
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        entry = f"{len(entries) + 1}. {r_title}\n   URL: {r_url}"
        if r_snip:
            entry += f"\n   {r_snip}"
        entries.append(entry)

    if not entries:
        return None

    header = f"Web search results for: {query}\n"
    header += f"({len(entries)} results via Startpage — use fetch_url on any URL for full content)\n"
    return header + "\n" + "\n\n".join(entries)


def tool_web_search(query: str = "", num_results: int = 0, **kwargs: Any) -> str:
    if not query:
        return "Error: No query provided."
    limit = num_results if 1 <= num_results <= 10 else SEARCH_MAX_RESULTS
    result = _startpage_search(query, limit)
    if result:
        return result
    return f"No results found for: {query}"


def tool_fetch_url(url: str = "", **kwargs: Any) -> str:
    if not url:
        return "Error: No URL provided."
    if not url.startswith("http"):
        url = "https://" + url
    ok, err = _validate_url(url)
    if not ok:
        return f"Error: {err}"

    try:
        domain = urllib.parse.urlparse(url).hostname or url
    except Exception:
        domain = url

    wiki_match = re.match(r"https?://(\w+)\.wikipedia\.org/wiki/([^#?]+)", url)
    if wiki_match:
        lang, title = wiki_match.group(1), wiki_match.group(2)
        display_title = urllib.parse.unquote(title).replace("_", " ")
        _PROGRESS.update(f"Wikipedia: {truncate(display_title, 35)}")
        api_url = (
            f"https://{lang}.wikipedia.org/w/api.php?"
            f"action=query&titles={urllib.parse.quote(title, safe='')}"
            f"&prop=extracts&explaintext=1&exlimit=1"
            f"&exchars={FETCH_MAX_CHARS}&format=json"
        )
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                pages = data.get("query", {}).get("pages", {})
                for page_id, page_data in pages.items():
                    if page_id == "-1":
                        break
                    extract = page_data.get("extract", "")
                    page_title = page_data.get("title", title)
                    if extract:
                        return f"Wikipedia — {page_title}\n\n{extract}"[:FETCH_MAX_CHARS]
        except Exception:
            pass

    _PROGRESS.update(f"Fetching {truncate(domain, 35)}…")
    try:
        raw = _fetch_page(url)
        _PROGRESS.update(f"Parsing {truncate(domain, 35)}…")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.DOTALL | re.IGNORECASE)
        page_title = ""
        if title_match:
            page_title = re.sub(r"\s+", " ", _html.unescape(
                re.sub(r"<[^>]+>", "", title_match.group(1))
            )).strip()
        article_text = ""
        for tag in (
            "article", "main",
            r'div[^>]+class="[^"]*(?:article|story|content|post|body|entry)[^"]*"',
            r'div[^>]+id="[^"]*(?:article|story|content|main|body)[^"]*"',
        ):
            article_match = re.search(
                rf"<{tag}[^>]*>(.*?)</{tag.split('[')[0]}>",
                raw, flags=re.DOTALL | re.IGNORECASE,
            )
            if article_match:
                candidate = _clean_html(article_match.group(1))
                if len(candidate) > 200:
                    article_text = candidate
                    break
        text = article_text if article_text else _clean_html(raw)
        header = f"Page: {page_title}\nURL: {url}\n\n" if page_title else ""
        full = header + text
        if len(full) > FETCH_MAX_CHARS:
            return full[:FETCH_MAX_CHARS] + "\n\n[Content truncated — page exceeded character limit]"
        return full
    except urllib.error.HTTPError as exc:
        return f"Error fetching URL: HTTP {exc.code} ({exc.reason})"
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error fetching URL: {exc}"


def tool_wikipedia(query: str = "", lang: str = "en", **kwargs: Any) -> str:
    if not query:
        return "Error: No query provided."
    if not re.fullmatch(r"[a-z]{2,5}", lang):
        lang = "en"
    _PROGRESS.update(f"Searching Wikipedia: {truncate(query, 30)}")
    search_url = (
        f"https://{lang}.wikipedia.org/w/api.php?"
        f"action=query&list=search"
        f"&srsearch={urllib.parse.quote(query)}"
        f"&srlimit=1&format=json"
    )
    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        results = data.get("query", {}).get("search", [])
        if not results:
            return f"No Wikipedia results for: {query}"
        title = results[0]["title"]
        _PROGRESS.update(f"Fetching article: {truncate(title, 30)}")
        return tool_fetch_url(
            url=f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title, safe='')}"
        )
    except Exception as exc:
        return f"Error: {exc}"


TOOLS_REGISTRY: dict[str, Any] = {
    "get_time":    tool_get_time,
    "calculator":  tool_calculator,
    "web_search":  tool_web_search,
    "fetch_url":   tool_fetch_url,
    "wikipedia":   tool_wikipedia,
}

OPENAI_TOOLS_SCHEMA: list[dict[str, Any]] = [
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
            "description": (
                "Evaluate a mathematical expression. Supports: +, -, *, /, //, %, ** (power), "
                "and functions: sqrt, sin, cos, tan, asin, acos, atan, log, log2, log10, "
                "exp, floor, ceil, factorial, gcd, abs. Constants: pi, e, tau."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression, e.g. 'sqrt(2) * sin(pi/4)' or '5 * (3 + 2)'",
                    },
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
                "Search the web for current information via Startpage. "
                "Returns results with titles, URLs, and snippets. "
                "Use fetch_url on any returned URL to read the full article content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "num_results": {"type": "integer", "description": "Number of results (1-10, default 6)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and read the full text content from any web page URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full HTTP/HTTPS URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia",
            "description": "Search Wikipedia by query and return the top article's plaintext content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "lang": {"type": "string", "description": "Wikipedia language code, default 'en'"},
                },
                "required": ["query"],
            },
        },
    },
]

GEMINI_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "functionDeclarations": [
            {"name": "get_time", "description": "Get the current local time and date."},
            {
                "name": "calculator",
                "description": (
                    "Evaluate a mathematical expression. Supports: +, -, *, /, //, %, ** (power), "
                    "and functions: sqrt, sin, cos, tan, asin, acos, atan, log, log2, log10, "
                    "exp, floor, ceil, factorial, gcd, abs. Constants: pi, e, tau."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "Math expression"},
                    },
                    "required": ["expression"],
                },
            },
            {
                "name": "web_search",
                "description": (
                    "Search the web for current information via Startpage. "
                    "Returns results with titles, URLs, and snippets. "
                    "Use fetch_url on any returned URL to read the full article content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query"},
                        "num_results": {"type": "integer", "description": "Number of results (1-10, default 6)"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "fetch_url",
                "description": "Fetch and read the full text content from any web page URL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The full HTTP/HTTPS URL to fetch"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "wikipedia",
                "description": "Search Wikipedia by query and return the top article's plaintext content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query"},
                        "lang": {"type": "string", "description": "Wikipedia language code, default 'en'"},
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
    error_box: list[Optional[Exception]] = [None]

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
#  SESSION MANAGEMENT
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
            return "missing 'role' field"
        if role in ("user", "assistant", "model", "tool", "system"):
            has_content = isinstance(msg.get("content"), (str, list))
            has_parts = isinstance(msg.get("parts"), list)
            if role == "system" and not isinstance(msg.get("content"), str):
                return "system message missing string content"
            elif role != "system" and not has_content and not has_parts:
                return f"message role='{role}' has no content or parts"
        else:
            return f"unknown role '{role}'"
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
        eprint(f"{C.ERROR}Session is corrupt ({err}). Cannot load.{C.RESET}")
        return None
    cprint(f"{C.INFO}Session loaded ← {path}  ({len(data)} messages){C.RESET}")
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
        answer = input(f"{_rl(C.WARN)}Continue? (y/N): {_rl(C.RESET)}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        cprint(f"\n{C.INFO}Cancelled.{C.RESET}")
        return
    if answer == "y":
        for f in files:
            f.unlink(missing_ok=True)
        cprint(f"{C.INFO}All sessions cleared.{C.RESET}")
    else:
        cprint(f"{C.INFO}Cancelled.{C.RESET}")


# ─────────────────────────────────────────────────────────────────────────────
#  HISTORY MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def init_history(is_openai_compat: bool) -> History:
    if SYSTEM_PROMPT and is_openai_compat:
        return [{"role": "system", "content": SYSTEM_PROMPT}]
    return []


def truncate_history(history: History, is_openai_compat: bool) -> History:
    system_offset = (
        1 if is_openai_compat and history and history[0].get("role") == "system" else 0
    )
    max_total = MAX_HISTORY_MESSAGES + system_offset
    if len(history) <= max_total:
        return history
    to_remove = len(history) - max_total
    if not is_openai_compat and to_remove % 2 == 1:
        to_remove += 1
    if system_offset:
        return [history[0]] + history[1 + to_remove:]
    return history[to_remove:]


# ─────────────────────────────────────────────────────────────────────────────
#  MODEL FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def _build_request(
    url: str, api_key: str, provider: str, data: Optional[bytes] = None,
) -> urllib.request.Request:
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
    api_err = data.get("error") if isinstance(data, dict) else None
    if api_err:
        msg = api_err.get("message", str(api_err)) if isinstance(api_err, dict) else str(api_err)
        eprint(f"{C.ERROR}API error: {msg}{C.RESET}")
        return None
    try:
        if provider == "gemini":
            models = [
                m["name"].replace("models/", "")
                for m in data.get("models", [])
                if (
                    any("generateContent" in method for method in m.get("supportedGenerationMethods", []))
                    and not m["name"].startswith("models/embedding")
                )
            ]
        elif provider == "ollama":
            models = [m["name"] for m in data.get("models", [])]
        elif provider == "together":
            arr = data if isinstance(data, list) else data.get("data", [])
            models = sorted(m["id"] for m in arr)
        else:
            models = sorted(m["id"] for m in data.get("data", []))
    except (KeyError, TypeError) as exc:
        eprint(f"{C.ERROR}Could not parse model list: {exc}{C.RESET}")
        return None
    return [m for m in models if m]


def select_model_interactive(
    provider: str, api_key: str, filters: list[str], current_model: str = "",
) -> Optional[str]:
    cprint(f"{C.INFO}Fetching models for {provider.upper()}…{C.RESET}")
    models = fetch_models(provider, api_key)
    if not models:
        eprint(f"{C.ERROR}No models returned by {provider.upper()}.{C.RESET}")
        return None
    if filters:
        models = filter_models(models, filters)
    if not models:
        eprint(f"{C.ERROR}No models matched filter: {' '.join(filters)}{C.RESET}")
        return None
    if len(models) == 1:
        cprint(f"{C.INFO}Auto-selected:{C.RESET} {models[0]}")
        return models[0]
    cprint(f"{C.INFO}Available models for {provider.upper()}:{C.RESET}")
    for i, m in enumerate(models, 1):
        marker = f"  {C.BOLD}{C.AI}◉{C.RESET}" if m == current_model else "   "
        cprint(f"{marker}{C.BOLD}{i:3}{C.RESET}. {m}")
    while True:
        try:
            choice = input(f"{_rl(C.INFO)}Select model number (or 'c' to cancel): {_rl(C.RESET)}").strip()
        except (EOFError, KeyboardInterrupt):
            cprint(f"\n{C.INFO}Cancelled.{C.RESET}")
            return None
        if choice.lower() in ("c", "cancel", "q"):
            cprint(f"{C.INFO}Cancelled.{C.RESET}")
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            return models[int(choice) - 1]
        eprint(f"{C.WARN}Enter a number between 1 and {len(models)}, or 'c' to cancel.{C.RESET}")


# ─────────────────────────────────────────────────────────────────────────────
#  PAYLOAD BUILDING
# ─────────────────────────────────────────────────────────────────────────────

def build_user_message(
    text: str, image: ImageAttachment, provider: str, is_openai_compat: bool,
) -> Message:
    prompt = text
    if image.attached:
        if not is_openai_compat:
            return {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": image.mime, "data": image.base64}},
                ],
            }
        elif provider == "ollama":
            return {"role": "user", "content": prompt, "images": [image.base64]}
        else:
            return {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image.mime};base64,{image.base64}",
                    }},
                ],
            }
    if not is_openai_compat:
        return {"role": "user", "parts": [{"text": prompt}]}
    return {"role": "user", "content": prompt}


def build_payload(
    provider: str,
    model_id: str,
    history: History,
    is_openai_compat: bool,
    enable_tools: bool,
    enable_thinking: bool,
) -> dict[str, Any]:
    if not is_openai_compat:
        contents = [m for m in history if m.get("role") != "system"]
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
            payload["tools"] = GEMINI_TOOLS_SCHEMA + [{"googleSearch": {}}]
        return payload

    oai_payload: dict[str, Any] = {
        "model": model_id,
        "messages": history,
        "temperature": DEFAULT_TEMPERATURE,
        "stream": True,
    }

    if enable_tools:
        oai_payload["tools"] = OPENAI_TOOLS_SCHEMA

    if provider == "ollama":
        oai_payload["think"] = enable_thinking
        oai_payload["options"] = {
            "num_predict": DEFAULT_MAX_TOKENS,
            "top_p": DEFAULT_TOP_P,
        }
    elif provider == "cerebras":
        oai_payload["max_completion_tokens"] = DEFAULT_MAX_TOKENS
        oai_payload["top_p"] = DEFAULT_TOP_P
    elif provider != "together":
        oai_payload["max_tokens"] = DEFAULT_MAX_TOKENS
        oai_payload["top_p"] = DEFAULT_TOP_P

    return oai_payload

# ─────────────────────────────────────────────────────────────────────────────
#  SSE PARSERS
# ─────────────────────────────────────────────────────────────────────────────

class _ChunkResult:
    __slots__ = ("text", "think", "finish", "error", "tool_chunks")

    def __init__(self) -> None:
        self.text = ""
        self.think = ""
        self.finish = ""
        self.error = ""
        self.tool_chunks: list[dict[str, Any]] = []


def _parse_openai_chunk(obj: dict[str, Any], provider: str) -> _ChunkResult:
    r = _ChunkResult()

    if provider == "ollama":
        msg_obj = obj.get("message", {})
        r.text = msg_obj.get("content") or ""
        r.think = msg_obj.get("thinking") or ""
        if obj.get("done") is True:
            r.finish = obj.get("done_reason") or "stop"
        for tc in msg_obj.get("tool_calls", []):
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
    for tc_chunk in delta.get("tool_calls", []):
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
    for part in content_obj.get("parts", []):
        if "text" in part:
            r.text += part["text"]
        if "functionCall" in part:
            fc = part["functionCall"]
            r.tool_chunks.append({
                "name": fc.get("name", ""),
                "args": fc.get("args", {}),
            })

    r.finish = candidate.get("finishReason", "")
    if not r.finish or r.finish == "null":
        if any(rating.get("blocked") for rating in candidate.get("safetyRatings", [])):
            r.finish = "SAFETY"
    return r


# ─────────────────────────────────────────────────────────────────────────────
#  STREAM RENDERER
# ─────────────────────────────────────────────────────────────────────────────

class StreamRenderer:
    """Smooth streaming with safe markdown/latex rendering."""

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

        rendered = (
            MD_RENDERER.render_line(self._line_buffer)
            if final else
            MD_RENDERER.preview_line(self._line_buffer)
        )
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
        to_process = text
        while to_process:
            nl_idx = to_process.find("\n")
            if nl_idx != -1:
                self._line_buffer += to_process[:nl_idx]
                self._commit_current_line()
                to_process = to_process[nl_idx + 1:]
            else:
                self._line_buffer += to_process
                self._draw_current_line(final=False)
                to_process = ""

    def feed_thinking(self, think_tok: str) -> None:
        if not think_tok or not self.enable_thinking:
            return

        if self.first_chunk:
            _stdout_write(C.CLR)
            _stdout_write(f"{C.AI}AI:{C.RESET}  ")
            self._used_ai_prefix = True
            self.first_chunk = False

        if not self._in_think_display:
            _stdout_write(f"{C.THINK}[Thinking] ")
            self._in_think_display = True

        _stdout_write(f"{C.THINK}{think_tok}{C.RESET}")

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
                    if self.enable_thinking:
                        _stdout_write(f"{C.THINK}{remaining[:close]}{C.RESET}")
                    _stdout_write(f"{C.RESET}\n\n")
                    self.is_thinking = False
                    self._in_think_display = False
                    after = remaining[close + 7:]
                    b = after.find(">")
                    remaining = after[b + 1:] if b != -1 else ""
                else:
                    if self.enable_thinking:
                        _stdout_write(f"{C.THINK}{remaining}{C.RESET}")
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
                        _stdout_write(f"{C.THINK}[Thinking] ")
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
#  STREAMING RESPONSE
# ─────────────────────────────────────────────────────────────────────────────

def stream_response(
    provider: str, model_id: str, history: History,
    is_openai_compat: bool, api_key: str,
    enable_tools: bool, enable_thinking: bool,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    payload = build_payload(
    provider,
    model_id,
    history,
    is_openai_compat,
    enable_tools,
    enable_thinking,
    )
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ep = ENDPOINTS[provider]

    if not is_openai_compat:
        url = (
            f"{ep['chat_base']}{model_id}:streamGenerateContent"
            f"?key={api_key}&alt=sse"
        )
        req = urllib.request.Request(
            url, data=payload_bytes,
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
    gem_tool_calls: list[dict[str, Any]] = []

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

                    if not is_openai_compat and finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                        if not renderer.full_text:
                            error_msg = f"Stream ended (reason: {finish_reason})"
                        done_received = True
                        break

                    if provider == "ollama" and finish_reason:
                        done_received = True
                        break

    except KeyboardInterrupt:
        interrupted = True
        if renderer.first_chunk:
            _stdout_write(C.CLR)
        cprint(f"\n{C.WARN}(Request interrupted){C.RESET}")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        if renderer.first_chunk:
            _stdout_write(C.CLR)
        error_msg = f"HTTP {exc.code}: {truncate(err_body, 200)}"
    except urllib.error.URLError as exc:
        if renderer.first_chunk:
            _stdout_write(C.CLR)
        error_msg = f"Network error: {exc.reason}"
    except OSError as exc:
        if renderer.first_chunk:
            _stdout_write(C.CLR)
        error_msg = f"Connection error: {exc}"

    renderer.finalize()

    tool_calls_out: list[dict[str, Any]] = []
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
        eprint(
            f"{C.WARN}(Response truncated: provider hit output limit: {finish_reason}){C.RESET}"
        )

    if error_msg:
        if renderer.full_text or tool_calls_out:
            eprint(
                f"{C.WARN}(Stream ended after partial output: {error_msg}){C.RESET}"
            )
            full_text = renderer.full_text
            if len(full_text) > MAX_MESSAGE_LENGTH:
                full_text = full_text[:MAX_MESSAGE_LENGTH]
            clean = strip_think_tags(full_text)
            return (clean if clean else ""), tool_calls_out

        cprint(f"{C.ERROR}{error_msg}{C.RESET}")
        return None, []

    if interrupted and renderer.full_text:
        eprint(f"{C.INFO}(Partial response saved){C.RESET}")

    full_text = renderer.full_text
    if len(full_text) > MAX_MESSAGE_LENGTH:
        full_text = full_text[:MAX_MESSAGE_LENGTH]
    clean = strip_think_tags(full_text)
    if not clean and not tool_calls_out and not interrupted:
        return None, []
    return (clean if clean else ""), tool_calls_out

# ─────────────────────────────────────────────────────────────────────────────
#  HISTORY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _append_assistant_turn(
    history: History, ai_text: Optional[str],
    tool_calls: list[dict[str, Any]], provider: str, is_openai_compat: bool,
) -> None:
    if not is_openai_compat:
        parts: list[dict[str, Any]] = []
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
            asst_msg["tool_calls"] = [
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
    history: History, tool_calls: list[dict[str, Any]],
    results: list[str], provider: str, is_openai_compat: bool,
) -> None:
    if not is_openai_compat:
        response_parts = [
            {
                "functionResponse": {
                    "name": tc["function"]["name"],
                    "response": {"result": result},
                }
            }
            for tc, result in zip(tool_calls, results)
        ]
        history.append({"role": "user", "parts": response_parts})
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
        name = msg.get("name", "tool")
        return f"[🛠️  {name}] {truncate(msg.get('content', ''), 120)}"
    if "tool_calls" in msg:
        names = [tc.get("function", tc).get("name", "?") for tc in msg["tool_calls"]]
        base = msg.get("content") or ""
        return (base + f" [🛠️  → {', '.join(names)}]").strip()

    raw = msg.get("content") or msg.get("parts", [{}])
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and raw:
        if isinstance(raw[0], dict):
            if "text" in raw[0]:
                return raw[0]["text"]
            if "functionCall" in raw[0]:
                return f"[🛠️  → {', '.join(p['functionCall'].get('name', '?') for p in raw if 'functionCall' in p)}]"
            if "functionResponse" in raw[0]:
                return f"[🛠️  results for {', '.join(p['functionResponse'].get('name', '?') for p in raw if 'functionResponse' in p)}]"
            for part in raw:
                if isinstance(part, dict) and part.get("type") == "text":
                    return part.get("text", "")
            has_image = any(
                isinstance(p, dict) and (p.get("type") == "image_url" or "inlineData" in p)
                for p in raw
            )
            return "[📎 image]" if has_image else "[content]"
        return str(raw)
    return str(raw) if raw else "[empty]"


# ─────────────────────────────────────────────────────────────────────────────
#  MULTI-LINE INPUT
# ─────────────────────────────────────────────────────────────────────────────

def read_multiline_input(initial_prompt: str, cont_prompt: str = "") -> Optional[str]:
    if not cont_prompt:
        cont_prompt = f"  {_rl(C.DIM)}..{_rl(C.RESET)} "
    try:
        line = input(initial_prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    lines: list[str] = []
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
    lines: list[str] = []
    paste_prompt = f"  {_rl(C.DIM)}│{_rl(C.RESET)} "
    while True:
        try:
            line = input(paste_prompt)
        except (EOFError, KeyboardInterrupt):
            cprint(f"\n{C.INFO}(Paste cancelled){C.RESET}")
            return "\n".join(lines).strip() if lines else None
        if line.strip() == "---":
            break
        lines.append(line)
    body = "\n".join(lines).strip()
    if not body:
        return None
    cprint(f"{C.INFO}({len(lines)} lines captured){C.RESET}")
    return f"{prefix}\n\n{body}" if prefix else body


# ─────────────────────────────────────────────────────────────────────────────
#  USAGE / HELP
# ─────────────────────────────────────────────────────────────────────────────

def print_usage() -> None:
    me = Path(sys.argv[0]).name
    cprint(f"""
{C.INFO}Usage:{C.RESET}
  python {me} <provider> [filter]...

{C.INFO}Providers:{C.RESET}
  gemini  openrouter  groq  together  cerebras  novita  ollama

{C.INFO}Setup:{C.RESET}
  Edit the {C.BOLD}API_KEYS{C.RESET} dict at the top of this script with your keys.
  Edit the Ollama endpoints below if you want localhost instead of ollama.com.

{C.INFO}Chat commands:{C.RESET}
  {C.BOLD}/history{C.RESET}            Show conversation history
  {C.BOLD}/model{C.RESET}              Switch to a different model mid-chat
  {C.BOLD}/save <name>{C.RESET}        Save session
  {C.BOLD}/load <name>{C.RESET}        Load a saved session
  {C.BOLD}/clear{C.RESET}              Delete all saved sessions
  {C.BOLD}/upload <path>{C.RESET}      Attach an image
  {C.BOLD}/image{C.RESET}              Show attached image info
  {C.BOLD}/clearimage{C.RESET}         Remove attached image
  {C.BOLD}/paste [text]{C.RESET}       Multi-line paste mode
  {C.BOLD}/togglethinking{C.RESET}     Toggle reasoning display
  {C.BOLD}/toggletools{C.RESET}        Toggle tool calling on/off
  {C.BOLD}/help{C.RESET}               Show this help
  {C.BOLD}quit{C.RESET} / {C.BOLD}exit{C.RESET}          End session
""")


def print_chat_help() -> None:
    cprint(f"""{C.INFO}Commands:{C.RESET}
  {C.BOLD}/history{C.RESET}            Show conversation
  {C.BOLD}/model [filter]{C.RESET}     Switch model (keeps history)
  {C.BOLD}/save <name>{C.RESET}        Save session
  {C.BOLD}/load <name>{C.RESET}        Load session
  {C.BOLD}/clear{C.RESET}              Delete all sessions
  {C.BOLD}/upload <path>{C.RESET}      Attach image
  {C.BOLD}/image{C.RESET}              Show attached image
  {C.BOLD}/clearimage{C.RESET}         Remove image
  {C.BOLD}/paste [text]{C.RESET}       Multi-line paste mode
  {C.BOLD}/togglethinking{C.RESET}     Toggle reasoning display
  {C.BOLD}/toggletools{C.RESET}        Toggle tool calling on/off
  {C.BOLD}/help{C.RESET}               Show this help
  {C.BOLD}quit{C.RESET} / {C.BOLD}exit{C.RESET}          End session
{C.TOOL}Available tools:{C.RESET}
  get_time · calculator · web_search · fetch_url · wikipedia
""")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN CHAT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def chat_loop(
    provider: str, model_id: str, is_openai_compat: bool,
    api_key: str, enable_tools: bool, enable_thinking: bool,
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

    def _print_banner() -> None:
        sep = "─" * 85
        cprint(f"\n{sep}")
        cprint(f"  {C.INFO}Provider:{C.RESET}  {provider.upper()}   {C.INFO}Model:{C.RESET}  {model_id}")
        cprint(
            f"  {C.INFO}History:{C.RESET}   last {MAX_HISTORY_MESSAGES} turns  │  "
            f"{C.INFO}Temp:{C.RESET} {DEFAULT_TEMPERATURE}  │  "
            f"{C.INFO}Tokens:{C.RESET} {DEFAULT_MAX_TOKENS}  │  "
            f"{C.INFO}TopP:{C.RESET} {DEFAULT_TOP_P}"
        )
        cprint(f"  {C.INFO}Max message:{C.RESET}  {MAX_MESSAGE_LENGTH:,} characters")
        status = "active" if SYSTEM_PROMPT else "inactive (empty)"
        cprint(f"  {C.INFO}System prompt:{C.RESET}  {status}")
        if provider == "ollama":
            ollama_url = ENDPOINTS["ollama"]["chat"].rsplit("/api/", 1)[0]
            cprint(f"  {C.INFO}Ollama URL:{C.RESET}  {ollama_url}")
        cprint(f"  {C.INFO}Rendering:{C.RESET}   Markdown + LaTeX → Unicode")
        think_st = f"{C.BOLD}{C.THINK}enabled{C.RESET}" if thinking_on else "disabled"
        cprint(f"  {C.INFO}Thinking output:{C.RESET}  {think_st}  (toggle: /togglethinking)")
        tool_st = f"{C.BOLD}{C.TOOL}enabled{C.RESET}" if tools_on else "disabled"
        cprint(f"  {C.INFO}Tool calling:{C.RESET}     {tool_st}  (toggle: /toggletools)")
        if tools_on:
            cprint(f"  {C.INFO}Tools:{C.RESET}           {', '.join(TOOLS_REGISTRY)}")
            cprint(f"  {C.INFO}Search:{C.RESET}          Startpage (live progress)")
            cprint(f"  {C.INFO}Tool timeout:{C.RESET}    {TOOL_EXEC_TIMEOUT}s per call")
            cprint(f"  {C.INFO}History mode:{C.RESET}    auto-compact (tool msgs collapsed)")
        cprint(f"  {C.INFO}SSRF protection:{C.RESET} on (DNS-pinned)")
        cprint(f"  {C.INFO}Retry:{C.RESET}           {MAX_RETRIES}x on 429/5xx")
        cprint(
            f"  {C.INFO}Input:{C.RESET}   end line with {C.BOLD}\\{C.RESET} "
            f"to continue  │  {C.BOLD}/paste{C.RESET} for multi-line"
        )
        cprint(
            f"  Type {C.BOLD}quit{C.RESET} or {C.BOLD}exit{C.RESET} to end  │  "
            f"{C.BOLD}/model{C.RESET} to switch  │  "
            f"{C.BOLD}/help{C.RESET} for all commands"
        )
        cprint(sep + "\n")

    _print_banner()

    while True:
        img_tag = (
            f"[{_rl(C.IMAGE)}📎 {Path(image.path).name}{_rl(C.RESET)}] "
            if image.attached else ""
        )
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
                new_model = select_model_interactive(
                    provider, api_key, inline_filters, current_model=model_id,
                )
                if new_model is not None:
                    old_model = model_id
                    model_id = new_model
                    if old_model == model_id:
                        cprint(f"{C.INFO}Already using {model_id}.{C.RESET}")
                    else:
                        cprint(f"{C.INFO}Switched model: {C.DIM}{old_model}{C.RESET}{C.INFO} → {C.BOLD}{model_id}{C.RESET}")
                        cprint(f"{C.INFO}  Conversation history ({len(history)} messages) preserved.{C.RESET}")
            elif cmd == "/upload":
                if not args:
                    eprint(f"{C.IMAGE}Usage: /upload <image_path>{C.RESET}")
                else:
                    image.load(args)
            elif cmd == "/image":
                if image.attached:
                    cprint(f"{C.IMAGE}Attached: {image.path}  ({image.mime}){C.RESET}")
                else:
                    cprint(f"{C.IMAGE}No image attached.{C.RESET}")
            elif cmd == "/clearimage":
                image.clear()
                cprint(f"{C.IMAGE}Image cleared.{C.RESET}")
            elif cmd == "/togglethinking":
                thinking_on = not thinking_on
                st = f"{C.BOLD}{C.THINK}enabled{C.RESET}" if thinking_on else f"{C.BOLD}disabled{C.RESET}"
                cprint(f"{C.INFO}Thinking output {st}.{C.RESET}")
            elif cmd == "/toggletools":
                tools_on = not tools_on
                st = f"{C.BOLD}{C.TOOL}enabled{C.RESET}" if tools_on else f"{C.BOLD}disabled{C.RESET}"
                cprint(f"{C.INFO}Tool calling {st}.{C.RESET}")
                if tools_on:
                    cprint(f"{C.INFO}  Tools: {', '.join(TOOLS_REGISTRY)}{C.RESET}")
            elif cmd == "/history":
                cprint(f"{C.INFO}── History ({len(history)} messages) ─────────────────────{C.RESET}")
                if not history:
                    cprint("  (empty)")
                for msg in history:
                    role = msg.get("role", "?")
                    text = _extract_display_text(msg)
                    colour = {
                        "user": C.USER, "assistant": C.AI, "model": C.AI,
                        "system": C.WARN, "tool": C.TOOL,
                    }.get(role, C.DIM)
                    cprint(f"  {colour}[{role}]{C.RESET}  {truncate(text, 500)}")
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
            eprint(f"{C.ERROR}Message too long ({len(user_input):,} chars, max {MAX_MESSAGE_LENGTH:,}).{C.RESET}")
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

        try:
            while True:
                tool_iter += 1
                if tool_iter > MAX_TOOL_ITERATIONS:
                    eprint(f"{C.ERROR}Tool-call loop limit ({MAX_TOOL_ITERATIONS}) reached.{C.RESET}")
                    break
                history = truncate_history(history, is_openai_compat)
                ai_text, tool_calls = stream_response(
                    provider, model_id, history, is_openai_compat,
                    api_key, tools_on, thinking_on,
                )
                if ai_text is None and not tool_calls:
                    if history and history[-1].get("role") == "user":
                        history.pop()
                        eprint(f"{C.WARN}(User message rolled back due to error){C.RESET}")
                    tool_loop_ok = False
                    break
                _append_assistant_turn(history, ai_text, tool_calls, provider, is_openai_compat)
                if tool_calls and tools_on:
                    had_tool_calls = True
                    results: list[str] = []
                    for tc in tool_calls:
                        fn_name = tc["function"]["name"]
                        fn_args = tc["function"]["arguments"]
                        display = _args_display(fn_args)
                        cprint(f"\n{C.TOOL}⚡ Tool: {fn_name}({display}){C.RESET}")
                        result = execute_tool(fn_name, fn_args)
                        cprint(f"{C.DIM}   → {truncate(result, 300)}{C.RESET}")
                        results.append(result)
                    _append_tool_results(history, tool_calls, results, provider, is_openai_compat)
                    continue
                final_ai_text = ai_text
                break
        except Exception as exc:
            eprint(f"{C.ERROR}Unexpected error during tool loop: {exc}{C.RESET}")
            history[:] = history_snapshot
            eprint(f"{C.WARN}(History rolled back to pre-message state){C.RESET}")
            tool_loop_ok = False

        if tool_loop_ok and had_tool_calls and final_ai_text is not None:
            if not is_openai_compat:
                clean_final: Message = {"role": "model", "parts": [{"text": final_ai_text}]}
            else:
                clean_final = {"role": "assistant", "content": final_ai_text}
            n_intermediate = len(history) - compact_from
            history[compact_from:] = [clean_final]
            if n_intermediate > 1:
                eprint(f"{C.DIM}(History compacted: {n_intermediate} tool messages → 1 answer){C.RESET}")

        cprint("")


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    for name, val, lo, hi in [
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

    is_openai_compat = (provider != "gemini")

    enable_thinking = True
    while True:
        try:
            ans = input(
                f"{_rl(C.THINK)}Enable thinking/reasoning ? (Y/n): {_rl(C.RESET)}"
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
        if ans in ("", "y", "yes"):
            enable_thinking = True
            cprint(f"{C.THINK}Thinking enabled.{C.RESET}")
            break
        elif ans in ("n", "no"):
            enable_thinking = False
            cprint(f"{C.THINK}Thinking disabled.{C.RESET}")
            break
        else:
            eprint(f"{C.WARN}Please enter y or n.{C.RESET}")

    enable_tools = False
    tool_names = ", ".join(TOOLS_REGISTRY)
    while True:
        try:
            ans = input(
                f"{_rl(C.TOOL)}Enable tool calling ({tool_names})? (y/N): {_rl(C.RESET)}"
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
        if ans in ("y", "yes"):
            enable_tools = True
            cprint(f"{C.TOOL}Tool calling enabled.  Search: Startpage  │  SSRF: protected{C.RESET}")
            break
        elif ans in ("n", "no", ""):
            break
        else:
            eprint(f"{C.WARN}Please enter y or n.{C.RESET}")

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
