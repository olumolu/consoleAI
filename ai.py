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
  - SSRF protection

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

MAX_HISTORY_MESSAGES  = 20
MAX_MESSAGE_LENGTH    = 50_000
DEFAULT_TEMPERATURE   = 0.7
DEFAULT_MAX_TOKENS    = 3000
DEFAULT_TOP_P         = 0.9
SESSION_DIR           = Path.home() / ".chat_sessions"
HISTORY_FILE          = Path.home() / ".ai_cli_history"

MAX_IMAGE_SIZE_MB = 20
SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

SYSTEM_PROMPT = "You are a helpful assistant running in a command-line interface."

ENABLE_THINKING_OUTPUT = True
MAX_TOOL_ITERATIONS    = 10

REQUEST_TIMEOUT     = 300
MODEL_FETCH_TIMEOUT = 30

FETCH_MAX_CHARS     = 8000
FETCH_MAX_BYTES     = 5 * 1024 * 1024
SEARCH_MAX_RESULTS  = 6

USER_AGENT = "PythonChatCLI/1.3"

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────────────────────────
#  ANSI COLOURS
# ─────────────────────────────────────────────────────────────────────────────

class C:
    """ANSI colour codes for terminal output (NOT for input() prompts)."""
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
    """Wrap ANSI code for readline input() prompts.

    Tells readline this text is invisible (zero display width) so it
    calculates line wrapping correctly in narrow terminals.
    Only use in strings passed to input(), never in print/stdout.write.
    """
    return f"\001{code}\002"


def cprint(msg: str, end: str = "\n") -> None:
    sys.stdout.write(msg + end)
    sys.stdout.flush()


def eprint(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# ─────────────────────────────────────────────────────────────────────────────
#  SSRF PROTECTION
# ─────────────────────────────────────────────────────────────────────────────

def _is_private_ip(hostname: str) -> bool:
    ips: list[str] = []
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in infos:
            ips.append(sockaddr[0])
    except (socket.gaierror, socket.herror, OSError):
        pass
    if not ips:
        return True
    for ip_str in ips:
        try:
            addr = ipaddress.ip_address(ip_str)
            if (
                addr.is_private or addr.is_loopback or addr.is_reserved
                or addr.is_link_local or addr.is_multicast
                or (isinstance(addr, ipaddress.IPv4Address) and (
                    ip_str.startswith("169.254.") or ip_str == "0.0.0.0"
                ))
                or (isinstance(addr, ipaddress.IPv6Address) and addr.is_site_local)
            ):
                return True
        except ValueError:
            return True
    return False


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
    blocked_hosts = {
        "localhost", "127.0.0.1", "0.0.0.0", "::1",
        "metadata.google.internal", "metadata.google.com",
    }
    if hostname.lower() in blocked_hosts:
        return False, f"Blocked hostname: {hostname}"
    if hostname.startswith("169.254."):
        return False, "Blocked: cloud metadata endpoint"
    if _is_private_ip(hostname):
        return False, f"Blocked: {hostname} resolves to private/internal IP"
    return True, ""


class _SSRFSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> Any:
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

    def render(self, text: str) -> str:
        text = re.sub(
            r'\$\$(.+?)\$\$|\\\[(.+?)\\\]',
            lambda m: self._convert(m.group(1) or m.group(2)),
            text, flags=re.DOTALL,
        )
        text = re.sub(r'\$(.+?)\$', lambda m: self._convert(m.group(1)), text)
        return text

    def _convert(self, tex: str) -> str:
        if not tex:
            return ""
        tex = tex.strip()
        for name, uni in self.greek.items():
            tex = tex.replace(f"\\{name}", uni)
        tex = re.sub(r'\\frac\{([^}]+)\}\{([^}]+)\}', r'(\1/\2)', tex)
        tex = re.sub(r'\^\{([^}]+)\}', lambda m: m.group(1).translate(self.sup_map), tex)
        tex = re.sub(r'\^([a-zA-Z0-9])', lambda m: m.group(1).translate(self.sup_map), tex)
        tex = re.sub(r'_\{([^}]+)\}', lambda m: m.group(1).translate(self.sub_map), tex)
        tex = re.sub(r'_([a-zA-Z0-9])', lambda m: m.group(1).translate(self.sub_map), tex)
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

    def render_line(self, line: str) -> str:
        stripped = line.strip()
        if stripped.startswith("```"):
            self.in_code_block = not self.in_code_block
            lang = stripped[3:].strip()
            if self.in_code_block:
                return f"{C.DIM}{'─' * 40} {lang}{C.RESET}"
            else:
                return f"{C.DIM}{'─' * 40}{C.RESET}"
        if self.in_code_block:
            return f"{C.CODE}{line}{C.RESET}"
        h_match = self.header_pat.match(line)
        if h_match:
            return f"{C.BOLD}{C.INFO}{h_match.group(2)}{C.RESET}"
        line = LATEX_RENDERER.render(line)
        line = self.code_pat.sub(rf'{C.CODE}`\1`{C.RESET}{C.AI}', line)
        line = self.bold_pat.sub(rf'{C.BOLD}\1{C.RESET}{C.AI}', line)
        line = self.italic_pat.sub(rf'{C.ITALIC}\1{C.RESET}{C.AI}', line)
        return line


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
        eprint(f"{C.WARN}!! Set the env var or edit API_KEYS in this script.{C.RESET}")
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
        return resp.read(1)


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


def _clean_html(raw: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<svg[^>]*>.*?</svg>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[\n ]*", "\n\n", text)
    return text.strip()


def _strip_css(text: str) -> str:
    text = re.sub(r'\.css-[a-zA-Z0-9_-]+\{[^}]*\}', '', text)
    text = re.sub(r'\.[a-zA-Z_][\w-]*\{[^}]*\}', '', text)
    text = re.sub(r'@media\s*\([^)]*\)\s*\{[^}]*(?:\{[^}]*\}[^}]*)?\}', '', text)
    text = re.sub(r'@[a-zA-Z-]+\s*[^{]*\{[^}]*(?:\{[^}]*\}[^}]*)?\}', '', text)
    text = re.sub(r'\{[^}]{0,500}\}', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()


def _clean_search_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    text = _strip_css(text)
    return text.strip()


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
        self._active = False
        self._status = ""
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._start_time = 0.0

    def start(self, label: str) -> None:
        self._active = True
        self._status = label
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def update(self, status: str) -> None:
        with self._lock:
            self._status = status

    def stop(self) -> None:
        self._active = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        sys.stdout.write(C.CLR)
        sys.stdout.flush()

    def _animate(self) -> None:
        i = 0
        while self._active:
            with self._lock:
                status = self._status
            elapsed = time.monotonic() - self._start_time
            timer = f" {C.DIM}({elapsed:.1f}s){C.RESET}"
            frame = self._FRAMES[i % len(self._FRAMES)]
            sys.stdout.write(
                f"{C.CLR}{C.TOOL}   {frame} {status}{C.RESET}{timer}"
            )
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1


_PROGRESS = _ToolProgress()

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


def tool_calculator(expression: str = "", **kwargs: Any) -> str:
    _OPS: dict[type, Any] = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.BitXor: operator.xor,
        ast.USub: operator.neg,
    }

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp):
            fn = _OPS.get(type(node.op))
            if fn is None:
                raise TypeError(f"Unsupported: {type(node.op).__name__}")
            return fn(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            fn = _OPS.get(type(node.op))
            if fn is None:
                raise TypeError(f"Unsupported: {type(node.op).__name__}")
            return fn(_eval(node.operand))
        raise TypeError(f"Unsupported node: {type(node).__name__}")

    try:
        expression = expression.replace("^", "**")
        result = _eval(ast.parse(expression, mode="eval").body)
        if isinstance(result, float) and result == int(result):
            return str(int(result))
        return str(result)
    except Exception as exc:
        return f"Error: {exc}"


# ── Startpage search ────────────────────────────────────────────────────────

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

    entries:      list[str] = []
    seen_domains: set[str]  = set()
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
            r'<h3[^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html_text, re.DOTALL,
        ):
            url = m.group(1)
            title = _clean_search_text(m.group(2))
            if "startpage.com" not in url and len(title) > 3:
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
        return (header + text)[:FETCH_MAX_CHARS]
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
            "description": "Evaluate a mathematical expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression, e.g. 5 * (3 + 2)",
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
                    "query": {
                        "type": "string",
                        "description": "The search query, e.g. 'latest news today'",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (1-10, default 6).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch and read the full text content from any web page URL. "
                "Returns clean plaintext extracted from the HTML."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full HTTP/HTTPS URL to fetch.",
                    },
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
                    "query": {
                        "type": "string",
                        "description": "The search query, e.g. 'Governor of West Bengal'",
                    },
                    "lang": {
                        "type": "string",
                        "description": "Wikipedia language code, default 'en'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

GEMINI_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "functionDeclarations": [
            {
                "name": "get_time",
                "description": "Get the current local time and date.",
            },
            {
                "name": "calculator",
                "description": "Evaluate a mathematical expression.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Math expression, e.g. 5 * (3 + 2)",
                        },
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
                        "query": {
                            "type": "string",
                            "description": "The search query, e.g. 'latest news today'",
                        },
                        "num_results": {
                            "type": "integer",
                            "description": "Number of results to return (1-10, default 6).",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "fetch_url",
                "description": (
                    "Fetch and read the full text content from any web page URL. "
                    "Returns clean plaintext extracted from the HTML."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The full HTTP/HTTPS URL to fetch.",
                        },
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
                        "query": {
                            "type": "string",
                            "description": "The search query, e.g. 'Governor of West Bengal'",
                        },
                        "lang": {
                            "type": "string",
                            "description": "Wikipedia language code, default 'en'.",
                        },
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
    try:
        return str(fn(**args))
    except Exception as exc:
        return f"Error executing '{name}': {exc}"
    finally:
        _PROGRESS.stop()


# ─────────────────────────────────────────────────────────────────────────────
#  SESSION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def _session_path(name: str) -> Path:
    return SESSION_DIR / f"{name}.json"


def save_session(name: str, history: History) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = _session_path(name)
    try:
        path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
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
        answer = input(
            f"{_rl(C.WARN)}Continue? (y/N): {_rl(C.RESET)}"
        ).strip().lower()
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
        1 if is_openai_compat and history and history[0].get("role") == "system"
        else 0
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
        with urllib.request.urlopen(req, timeout=MODEL_FETCH_TIMEOUT) as resp:
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
    provider: str, api_key: str, filters: list[str],
    current_model: str = "",
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
            choice = input(
                f"{_rl(C.INFO)}Select model number (or 'c' to cancel): {_rl(C.RESET)}"
            ).strip()
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
    provider: str, model_id: str, history: History,
    is_openai_compat: bool, enable_tools: bool,
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
#  STREAMING RESPONSE
# ─────────────────────────────────────────────────────────────────────────────

def stream_response(
    provider: str, model_id: str, history: History,
    is_openai_compat: bool, api_key: str,
    enable_tools: bool, enable_thinking: bool,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    payload = build_payload(provider, model_id, history, is_openai_compat, enable_tools)
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

    sys.stdout.write(f"{C.AI}AI:{C.RESET} {C.INFO}(💬 Waiting…){C.RESET}")
    sys.stdout.flush()

    full_text = ""
    first_chunk = True
    is_thinking = False
    in_think_disp = False
    finish_reason = ""
    error_msg = ""
    interrupted = False
    line_buffer = ""
    oai_tool_calls: dict[int, dict[str, Any]] = {}
    gem_tool_calls: list[dict[str, Any]] = []
    MD_RENDERER.in_code_block = False

    def _flush_text(text: str) -> None:
        nonlocal line_buffer
        to_process = text
        while to_process:
            nl_idx = to_process.find("\n")
            if nl_idx != -1:
                line_buffer += to_process[:nl_idx]
                rendered = MD_RENDERER.render_line(line_buffer)
                sys.stdout.write(f"{C.AI}{rendered}{C.RESET}\n")
                sys.stdout.flush()
                line_buffer = ""
                to_process = to_process[nl_idx + 1:]
            else:
                line_buffer += to_process
                to_process = ""

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            buffer = ""
            while True:
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
                        break
                    text_tok = think_tok = cur_finish = ""
                    if is_openai_compat:
                        if provider == "ollama":
                            msg_obj = obj.get("message", {})
                            text_tok = msg_obj.get("content") or ""
                            think_tok = msg_obj.get("thinking") or ""
                            if obj.get("done") is True:
                                cur_finish = "stop"
                            for tc in msg_obj.get("tool_calls", []):
                                idx = len(oai_tool_calls)
                                fn = tc.get("function", {})
                                args_raw = fn.get("arguments", "")
                                if isinstance(args_raw, dict):
                                    args_raw = json.dumps(args_raw)
                                elif not isinstance(args_raw, str):
                                    args_raw = "{}"
                                oai_tool_calls[idx] = {
                                    "id": f"call_{idx}", "type": "function",
                                    "function": {"name": fn.get("name", ""), "arguments": args_raw},
                                }
                        else:
                            choice = (obj.get("choices") or [{}])[0]
                            delta = choice.get("delta", {})
                            text_tok = delta.get("content") or ""
                            think_tok = delta.get("reasoning") or ""
                            cur_finish = choice.get("finish_reason") or ""
                            for tc_chunk in delta.get("tool_calls", []):
                                idx = tc_chunk.get("index", len(oai_tool_calls))
                                if idx not in oai_tool_calls:
                                    oai_tool_calls[idx] = {
                                        "id": tc_chunk.get("id", f"call_{idx}"), "type": "function",
                                        "function": {
                                            "name": tc_chunk.get("function", {}).get("name", ""),
                                            "arguments": tc_chunk.get("function", {}).get("arguments", ""),
                                        },
                                    }
                                else:
                                    fn_d = tc_chunk.get("function", {})
                                    entry = oai_tool_calls[idx]["function"]
                                    if fn_d.get("name"):
                                        entry["name"] += fn_d["name"]
                                    if fn_d.get("arguments"):
                                        entry["arguments"] += fn_d["arguments"]
                    else:
                        candidate = (obj.get("candidates") or [{}])[0]
                        pf = obj.get("promptFeedback")
                        if pf and pf.get("blockReason"):
                            error_msg = f"Content blocked (reason: {pf['blockReason']})"
                            break
                        content_obj = candidate.get("content", {})
                        for part in content_obj.get("parts", []):
                            if "text" in part:
                                text_tok += part["text"]
                            if "functionCall" in part:
                                gem_tool_calls.append(part["functionCall"])
                        cur_finish = candidate.get("finishReason", "")
                        if not cur_finish or cur_finish == "null":
                            if any(r.get("blocked") for r in candidate.get("safetyRatings", [])):
                                cur_finish = "SAFETY"
                    if cur_finish and not finish_reason:
                        finish_reason = cur_finish
                    if first_chunk and (text_tok or think_tok):
                        sys.stdout.write(C.CLR)
                        sys.stdout.write(f"{C.AI}AI:{C.RESET}  ")
                        first_chunk = False
                    if think_tok and enable_thinking:
                        if not in_think_disp:
                            sys.stdout.write(f"{C.THINK}[Thinking] ")
                            in_think_disp = True
                        sys.stdout.write(f"{C.THINK}{think_tok}{C.RESET}")
                        sys.stdout.flush()
                    if text_tok:
                        full_text += text_tok
                        remaining = text_tok
                        while remaining:
                            if is_thinking:
                                close = remaining.find("</think")
                                if close != -1:
                                    if enable_thinking:
                                        sys.stdout.write(f"{C.THINK}{remaining[:close]}{C.RESET}")
                                    sys.stdout.write(f"{C.RESET}\n\n")
                                    sys.stdout.flush()
                                    is_thinking = in_think_disp = False
                                    after = remaining[close + 7:]
                                    b = after.find(">")
                                    remaining = after[b + 1:] if b != -1 else ""
                                else:
                                    if enable_thinking:
                                        sys.stdout.write(f"{C.THINK}{remaining}{C.RESET}")
                                        sys.stdout.flush()
                                    remaining = ""
                            else:
                                open_idx = remaining.find("<think")
                                if open_idx != -1:
                                    before = remaining[:open_idx]
                                    if before:
                                        _flush_text(before)
                                    if line_buffer:
                                        sys.stdout.write(f"{C.AI}{MD_RENDERER.render_line(line_buffer)}{C.RESET}")
                                        sys.stdout.flush()
                                        line_buffer = ""
                                    if enable_thinking:
                                        sys.stdout.write(f"\n{C.THINK}[Thinking] ")
                                        in_think_disp = True
                                    is_thinking = True
                                    after = remaining[open_idx + 6:]
                                    b = after.find(">")
                                    remaining = after[b + 1:] if b != -1 else ""
                                else:
                                    _flush_text(remaining)
                                    remaining = ""
                    if not is_openai_compat and finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                        if not full_text:
                            error_msg = f"Stream ended (reason: {finish_reason})"
                        break
                    if provider == "ollama" and finish_reason == "stop":
                        break
    except KeyboardInterrupt:
        interrupted = True
        if first_chunk:
            sys.stdout.write(C.CLR)
        cprint(f"\n{C.WARN}(Request interrupted){C.RESET}")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        if first_chunk:
            sys.stdout.write(C.CLR)
        error_msg = f"HTTP {exc.code}: {truncate(err_body, 200)}"
    except urllib.error.URLError as exc:
        if first_chunk:
            sys.stdout.write(C.CLR)
        error_msg = f"Network error: {exc.reason}"
    except OSError as exc:
        if first_chunk:
            sys.stdout.write(C.CLR)
        error_msg = f"Connection error: {exc}"

    if line_buffer:
        sys.stdout.write(f"{C.AI}{MD_RENDERER.render_line(line_buffer)}{C.RESET}\n")
        sys.stdout.flush()
    MD_RENDERER.in_code_block = False

    tool_calls_out: list[dict[str, Any]] = []
    if is_openai_compat:
        for idx in sorted(oai_tool_calls):
            tool_calls_out.append(oai_tool_calls[idx])
    else:
        for gtc in gem_tool_calls:
            tool_calls_out.append({
                "id": gtc.get("name", "call_gemini"), "type": "function",
                "function": {"name": gtc.get("name", ""), "arguments": json.dumps(gtc.get("args", {}))},
            })

    if first_chunk and not error_msg and not interrupted:
        sys.stdout.write(C.CLR)
        if tool_calls_out:
            cprint(f"{C.AI}AI:{C.RESET} {C.TOOL}🛠️  [Calling tools…]{C.RESET}")
        else:
            cprint(f"{C.AI}AI:{C.RESET} {C.INFO}(empty response){C.RESET}")
    elif not first_chunk:
        sys.stdout.write(f"{C.RESET}\n")
        sys.stdout.flush()

    if error_msg:
        cprint(f"{C.ERROR}{error_msg}{C.RESET}")
        return None, []
    if interrupted and full_text:
        eprint(f"{C.INFO}(Partial response saved){C.RESET}")
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
            parts.append({"functionCall": {"name": tc["function"]["name"],
                          "args": _args_to_obj(tc["function"]["arguments"])}})
        if parts:
            history.append({"role": "model", "parts": parts})
        return
    if provider == "ollama":
        asst_msg: Message = {"role": "assistant", "content": ai_text or ""}
        if tool_calls:
            asst_msg["tool_calls"] = [
                {"function": {"name": tc["function"]["name"],
                              "arguments": _args_to_obj(tc["function"]["arguments"])}}
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
            {"functionResponse": {"name": tc["function"]["name"],
                                  "response": {"result": result}}}
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
            "role": "tool", "tool_call_id": tc.get("id", ""),
            "name": tc["function"]["name"], "content": result,
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
            has_image = any(isinstance(p, dict) and (p.get("type") == "image_url" or "inlineData" in p) for p in raw)
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
    if prefix:
        return f"{prefix}\n\n{body}"
    return body


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

{C.INFO}Chat commands:{C.RESET}
  {C.BOLD}/history{C.RESET}            Show conversation history
  {C.BOLD}/model{C.RESET}              Switch to a different model mid-chat
  {C.BOLD}/save <name>{C.RESET}        Save session  (~/.chat_sessions/<name>.json)
  {C.BOLD}/load <name>{C.RESET}        Load a saved session
  {C.BOLD}/clear{C.RESET}              Delete all saved sessions
  {C.BOLD}/upload <path>{C.RESET}      Attach an image {C.IMAGE}(vision models){C.RESET}
  {C.BOLD}/image{C.RESET}              Show attached image info
  {C.BOLD}/clearimage{C.RESET}         Remove attached image
  {C.BOLD}/paste [text]{C.RESET}       Multi-line paste mode (end with ---)
  {C.BOLD}/togglethinking{C.RESET}     Toggle reasoning display {C.THINK}(reasoning models){C.RESET}
  {C.BOLD}/toggletools{C.RESET}        Toggle tool calling on/off
  {C.BOLD}/help{C.RESET}               Show this help
  {C.BOLD}quit{C.RESET} / {C.BOLD}exit{C.RESET}          End session

{C.TOOL}Tool calling:{C.RESET}
  When enabled, the model can invoke local tools:
    • {C.BOLD}get_time{C.RESET}      – current local date & time
    • {C.BOLD}calculator{C.RESET}    – safe arithmetic evaluation
    • {C.BOLD}web_search{C.RESET}    – search the web via Startpage
    • {C.BOLD}fetch_url{C.RESET}     – fetch & clean any web page (≤ {FETCH_MAX_CHARS:,} chars)
    • {C.BOLD}wikipedia{C.RESET}     – search Wikipedia & return article text
  Live progress spinner shows tool status in real time.
  History auto-compacted after tool calls (only question + answer kept).
  SSRF protection active.
  Gemini also gets {C.BOLD}Google Search{C.RESET} grounding when tools are on.

{C.INFO}Examples:{C.RESET}
  {C.AI}python {me} gemini{C.RESET}
  {C.AI}python {me} openrouter claude{C.RESET}
  {C.AI}python {me} ollama{C.RESET}

{C.WARN}Set your API keys via environment variables or in the API_KEYS dict.{C.RESET}""")


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
  {C.BOLD}/paste [text]{C.RESET}       Multi-line paste mode (end with ---)
  {C.BOLD}/togglethinking{C.RESET}     Toggle reasoning display
  {C.BOLD}/toggletools{C.RESET}        Toggle tool calling on/off
  {C.BOLD}/help{C.RESET}               Show this help
  {C.BOLD}quit{C.RESET} / {C.BOLD}exit{C.RESET}          End session
{C.TOOL}Available tools:{C.RESET}
  get_time · calculator · web_search · fetch_url · wikipedia
{C.INFO}Search:{C.RESET}  Startpage (live progress spinner)
{C.INFO}Security:{C.RESET}  SSRF protection active (private IPs, localhost, cloud metadata blocked)
{C.INFO}History:{C.RESET}
  Tool-call messages auto-compacted after each exchange.
  Only your question + AI's final answer kept in history.""")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN CHAT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def chat_loop(
    provider: str, model_id: str, is_openai_compat: bool,
    api_key: str, enable_tools: bool, filters: list[str],
) -> None:
    if _READLINE_AVAILABLE:
        readline.set_history_length(1000)
        if HISTORY_FILE.exists():
            try:
                readline.read_history_file(str(HISTORY_FILE))
            except OSError:
                pass
        atexit.register(_save_readline_history)

    history:     History         = init_history(is_openai_compat)
    image:       ImageAttachment = ImageAttachment()
    thinking_on: bool           = ENABLE_THINKING_OUTPUT
    tools_on:    bool           = enable_tools

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
            cprint(f"  {C.INFO}History mode:{C.RESET}    auto-compact (tool msgs collapsed)")
        cprint(f"  {C.INFO}SSRF protection:{C.RESET} on")
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
                        cprint(
                            f"{C.INFO}Switched model: "
                            f"{C.DIM}{old_model}{C.RESET}{C.INFO} → "
                            f"{C.BOLD}{model_id}{C.RESET}"
                        )
                        cprint(
                            f"{C.INFO}  Conversation history "
                            f"({len(history)} messages) preserved.{C.RESET}"
                        )
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
                    colour = {"user": C.USER, "assistant": C.AI, "model": C.AI,
                              "system": C.WARN, "tool": C.TOOL}.get(role, C.DIM)
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
        history.append(user_msg)

        compact_from = len(history)
        had_tool_calls = False
        final_ai_text: Optional[str] = None
        tool_iter = 0

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

        if had_tool_calls and final_ai_text is not None:
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
        ("DEFAULT_TOP_P",       DEFAULT_TOP_P,       0, 1),
        ("DEFAULT_MAX_TOKENS",  DEFAULT_MAX_TOKENS,  1, 1_000_000),
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

    def _sigint_handler(sig: int, frame: Any) -> None:
        _PROGRESS.stop()
        cprint(f"\n{C.WARN}Interrupted.{C.RESET}")
        sys.exit(130)

    signal.signal(signal.SIGINT, _sigint_handler)

    chat_loop(provider, model_id, is_openai_compat, api_key, enable_tools, filters)
    cprint("👋 Session ended.")


if __name__ == "__main__":
    main()
