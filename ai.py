#!/usr/bin/env python3
"""
Universal Chat CLI
Pure stdlib, Python 3.8+. Zero pip installs required.

Features:
  - Markdown rendering (Bold, Italic, Code, Fenced code blocks)
  - LaTeX rendering (Greek letters, superscripts, fractions → Unicode)
  - Image attachment support (Vision models)
  - Multi-provider support (Gemini, OpenRouter, Groq, etc.)

Usage:
    python ai.py <provider> [filter]...
    ./ai.py gemini
    ./ai.py openrouter claude
    ./ai.py groq llama

Providers: gemini, openrouter, groq, together, cerebras, novita, ollama

Chat commands:
    /history            Show conversation history
    /save <name>        Save session to ~/.chat_sessions/<name>.json
    /load <name>        Load a saved session
    /clear              Delete all saved sessions
    /upload <path>      Attach an image to your next message
    /image              Show currently attached image
    /clearimage         Remove the attached image
    /togglethinking     Toggle reasoning/thinking output display
    /help               Show available commands
    quit / exit         End the session
"""

import sys
import json
import re
import base64
import signal
import urllib.request
import urllib.error
import mimetypes
import atexit
from pathlib import Path
from typing import Optional

# Try readline (not available on Windows without pyreadline3)
try:
    import readline
    _READLINE_AVAILABLE = True
except ImportError:
    _READLINE_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION  –  Edit here
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
HISTORY_FILE          = Path.home() / ".ai_cli_history"  # readline history

# Image support
MAX_IMAGE_SIZE_MB = 20
SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# System prompt (set to "" to disable)
SYSTEM_PROMPT = "You are a helpful assistant running in a command-line interface."

# Toggle thinking/reasoning output on startup
ENABLE_THINKING_OUTPUT = True

# Request timeouts in seconds
REQUEST_TIMEOUT     = 300   # Streaming chat timeout
MODEL_FETCH_TIMEOUT = 30    # Model listing timeout

# ─────────────────────────────────────────────────────────────────────────────
#  ANSI COLOURS
# ─────────────────────────────────────────────────────────────────────────────

class C:
    RESET  = "\033[0m"
    USER   = "\033[38;5;199m"   # bright magenta
    AI     = "\033[38;5;40m"    # bright green
    THINK  = "\033[38;5;214m"   # soft orange
    ERROR  = "\033[38;5;203m"   # vivid red
    WARN   = "\033[38;5;221m"   # soft yellow
    INFO   = "\033[38;5;75m"    # cyan-blue
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    ITALIC = "\033[3m"
    IMAGE  = "\033[38;5;208m"   # orange
    CODE   = "\033[38;5;229m"   # pale yellow
    CLR    = "\033[2K\r"        # clear current terminal line


def cprint(msg: str, end: str = "\n") -> None:
    """Print to stdout, flushing immediately."""
    sys.stdout.write(msg + end)
    sys.stdout.flush()


def eprint(msg: str) -> None:
    """Print to stderr (status / warning messages)."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# ─────────────────────────────────────────────────────────────────────────────
#  LATEX RENDERER (Pure Stdlib → Unicode)
# ─────────────────────────────────────────────────────────────────────────────

class LatexRenderer:
    """Converts simple LaTeX commands to Unicode for terminal display."""

    def __init__(self):
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

        # ── Superscript map (38 chars each) ─────────────────────────────────
        #  source: 0123456789+-=()nabcdefghijklmoprstuvwx
        #  target: ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣ
        self.sup_map = str.maketrans(
            "0123456789+-=()nabcdefghijklmoprstuvwx",
            "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣ"
        )

        # ── Subscript map (32 chars each) ───────────────────────────────────
        #  source: 0123456789+-=()aehijklmnoprstuvx
        #  target: ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ
        self.sub_map = str.maketrans(
            "0123456789+-=()aehijklmnoprstuvx",
            "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"
        )

    def render(self, text: str) -> str:
        """Process LaTeX in text: block $$ / \\[ then inline $."""
        # 1. Block math: $$ ... $$ or \[ ... \]
        text = re.sub(
            r'\$\$(.+?)\$\$|\\\[(.+?)\\\]',
            lambda m: self._convert(m.group(1) or m.group(2)),
            text,
            flags=re.DOTALL,
        )

        # 2. Inline math: $ ... $
        text = re.sub(
            r'\$(.+?)\$',
            lambda m: self._convert(m.group(1)),
            text,
        )

        return text

    def _convert(self, tex: str) -> str:
        if not tex:
            return ""
        tex = tex.strip()

        # Greek letters
        for name, uni in self.greek.items():
            tex = tex.replace(f"\\{name}", uni)

        # Fractions: \frac{a}{b} → (a/b)
        tex = re.sub(r'\\frac\{([^}]+)\}\{([^}]+)\}', r'(\1/\2)', tex)

        # Superscripts: ^{...} or ^x
        tex = re.sub(
            r'\^\{([^}]+)\}',
            lambda m: m.group(1).translate(self.sup_map), tex,
        )
        tex = re.sub(
            r'\^([a-zA-Z0-9])',
            lambda m: m.group(1).translate(self.sup_map), tex,
        )

        # Subscripts: _{...} or _x
        tex = re.sub(
            r'_\{([^}]+)\}',
            lambda m: m.group(1).translate(self.sub_map), tex,
        )
        tex = re.sub(
            r'_([a-zA-Z0-9])',
            lambda m: m.group(1).translate(self.sub_map), tex,
        )

        # Common symbols
        replacements = {
            "\\cdot": "·", "\\times": "×", "\\div": "÷",
            "\\sqrt": "√", "\\infty": "∞", "\\pm": "±",
            "\\neq": "≠", "\\leq": "≤", "\\geq": "≥",
            "\\approx": "≈", "\\sum": "Σ", "\\prod": "Π",
            "\\int": "∫",
        }
        for cmd, sym in replacements.items():
            tex = tex.replace(cmd, sym)

        return tex


LATEX_RENDERER = LatexRenderer()


# ─────────────────────────────────────────────────────────────────────────────
#  MARKDOWN RENDERER (With LaTeX + fenced code block support)
# ─────────────────────────────────────────────────────────────────────────────

class MarkdownRenderer:
    """Line-by-line Markdown → ANSI renderer with code-block awareness."""

    def __init__(self):
        self.bold_pat   = re.compile(r'\*\*(.+?)\*\*')
        self.italic_pat = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)')
        self.code_pat   = re.compile(r'`([^`]+)`')
        self.header_pat = re.compile(r'^(#{1,6})\s+(.*)')
        self.in_code_block = False

    def render_line(self, line: str) -> str:
        """Render a single completed line with Markdown + LaTeX formatting."""
        stripped = line.strip()

        # ── Fenced code block toggle ────────────────────────────────────────
        if stripped.startswith("```"):
            self.in_code_block = not self.in_code_block
            lang = stripped[3:].strip()
            if self.in_code_block:
                return f"{C.DIM}{'─' * 40} {lang}{C.RESET}"
            else:
                return f"{C.DIM}{'─' * 40}{C.RESET}"

        # Inside code block — no Markdown/LaTeX processing
        if self.in_code_block:
            return f"{C.CODE}{line}{C.RESET}"

        # ── Headers ─────────────────────────────────────────────────────────
        h_match = self.header_pat.match(line)
        if h_match:
            return f"{C.BOLD}{C.INFO}{h_match.group(2)}{C.RESET}"

        # ── LaTeX ───────────────────────────────────────────────────────────
        line = LATEX_RENDERER.render(line)

        # ── Inline code (process first to protect content) ──────────────────
        line = self.code_pat.sub(
            rf'{C.CODE}`\1`{C.RESET}{C.AI}', line
        )

        # ── Bold ────────────────────────────────────────────────────────────
        line = self.bold_pat.sub(
            rf'{C.BOLD}\1{C.RESET}{C.AI}', line
        )

        # ── Italic (negative lookaround avoids matching **bold**) ───────────
        line = self.italic_pat.sub(
            rf'{C.ITALIC}\1{C.RESET}{C.AI}', line
        )

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
        # Swap to http://localhost:11434 for local Ollama.com
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
        eprint(f"{C.ERROR}Error: Session name may only contain "
               f"letters, numbers, dash, underscore.{C.RESET}")
        return False
    if len(name) > 100:
        eprint(f"{C.ERROR}Error: Session name too long (max 100 chars).{C.RESET}")
        return False
    return True


def check_placeholder_key(key: str, provider: str) -> bool:
    """Returns True if key looks valid, False (with warning) otherwise."""
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
    elif provider in ("novita", "ollama") and len(key) < 10:
        msg = "is too short to be valid"

    if msg:
        eprint(f"{C.WARN}{'!' * 68}{C.RESET}")
        eprint(f"{C.WARN}!! WARNING: API key for '{provider.upper()}' {msg}.{C.RESET}")
        eprint(f"{C.WARN}!! Edit this script and set your real key in API_KEYS.{C.RESET}")
        eprint(f"{C.WARN}{'!' * 68}{C.RESET}")
        return False
    return True


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks for clean history storage."""
    result, remaining = "", text
    while remaining:
        start = remaining.find("<think")
        if start == -1:
            result += remaining
            break
        result += remaining[:start]
        after_open = remaining[start + 6:]          # skip "<think"
        bracket = after_open.find(">")
        remaining = after_open[bracket + 1:] if bracket != -1 else ""
        close = remaining.find("</think")
        if close == -1:
            break                                    # unclosed – drop rest
        after_close = remaining[close + 7:]
        bracket2 = after_close.find(">")
        remaining = after_close[bracket2 + 1:] if bracket2 != -1 else ""
    return result


def filter_models(models: list[str], filters: list[str]) -> list[str]:
    """Word-boundary filter: '3' matches 'gpt-3' but not '13b'."""
    if not filters:
        return models
    eprint(f"{C.INFO}Filtering with: {' '.join(filters)}  (word-boundary){C.RESET}")
    result = []
    for model in models:
        ml = model.lower()
        if all(
            re.search(
                r"(?:^|[^a-z0-9])" + re.escape(f.lower()) + r"(?:[^a-z0-9]|$)",
                ml,
            )
            for f in filters
        ):
            result.append(model)
    return result


def _read_chunk(resp, size: int = 8192) -> bytes:
    """Read from response using read1() for instant delivery, with fallback."""
    try:
        return resp.read1(size)
    except AttributeError:
        return resp.read(1)


# ─────────────────────────────────────────────────────────────────────────────
#  IMAGE HANDLING
# ─────────────────────────────────────────────────────────────────────────────

class ImageAttachment:
    def __init__(self):
        self.path   = ""
        self.base64 = ""
        self.mime   = ""

    def clear(self):
        self.path = self.base64 = self.mime = ""

    @property
    def attached(self) -> bool:
        return bool(self.base64)

    def load(self, raw_path: str) -> bool:
        """Validate, encode and store an image.  Returns True on success."""
        path = Path(raw_path.strip("'\""))

        if not path.is_file():
            eprint(f"{C.ERROR}Error: File not found: {path}{C.RESET}")
            return False

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_IMAGE_SIZE_MB:
            eprint(f"{C.ERROR}Error: Image too large "
                   f"({size_mb:.1f} MB). Max {MAX_IMAGE_SIZE_MB} MB.{C.RESET}")
            return False

        mime, _ = mimetypes.guess_type(str(path))
        if not mime:
            ext_map = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png",  ".gif":  "image/gif",
                ".webp": "image/webp",
            }
            mime = ext_map.get(path.suffix.lower(), "")

        if mime not in SUPPORTED_MIME_TYPES:
            eprint(f"{C.ERROR}Error: Unsupported image type '{mime}'. "
                   f"Supported: {', '.join(sorted(SUPPORTED_MIME_TYPES))}{C.RESET}")
            return False

        eprint(f"{C.IMAGE}Encoding image…{C.RESET}")
        try:
            raw = path.read_bytes()
            self.base64 = base64.b64encode(raw).decode("ascii")
            self.mime   = mime
            self.path   = str(path)
            size_kb = len(raw) // 1024
            eprint(f"{C.IMAGE}✓ Attached: {path.name} "
                   f"({mime}, {size_kb} KB){C.RESET}")
            return True
        except OSError as exc:
            eprint(f"{C.ERROR}Error reading image: {exc}{C.RESET}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
#  SESSION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def _session_path(name: str) -> Path:
    return SESSION_DIR / f"{name}.json"


def save_session(name: str, history: list[dict]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = _session_path(name)
    try:
        path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cprint(f"{C.INFO}Session saved → {path}{C.RESET}")
    except OSError as exc:
        eprint(f"{C.ERROR}Error saving session: {exc}{C.RESET}")


def _validate_session_data(data: object) -> str:
    """Return '' if valid, or an error description."""
    if not isinstance(data, list):
        return "not a JSON array"
    for msg in data:
        if not isinstance(msg, dict):
            return "element is not an object"
        role = msg.get("role")
        if not role:
            return "missing 'role' field"
        if role == "system":
            if not isinstance(msg.get("content"), str):
                return "system message missing string content"
        elif role in ("user", "assistant", "model"):
            has_content = isinstance(msg.get("content"), (str, list))
            has_parts   = isinstance(msg.get("parts"), list)
            if not has_content and not has_parts:
                return f"message role='{role}' has no content or parts"
        else:
            return f"unknown role '{role}'"
    return ""


def load_session(name: str) -> Optional[list[dict]]:
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
    cprint(f"{C.WARN}This will permanently delete all sessions "
           f"in {SESSION_DIR}:{C.RESET}")
    for f in files:
        cprint(f"  {f.stem}")
    try:
        answer = input(f"{C.WARN}Continue? (y/N): {C.RESET}").strip().lower()
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

def init_history(is_openai_compat: bool) -> list[dict]:
    if SYSTEM_PROMPT and is_openai_compat:
        return [{"role": "system", "content": SYSTEM_PROMPT}]
    return []


def truncate_history(history: list[dict], is_openai_compat: bool) -> list[dict]:
    """Trim history to MAX_HISTORY_MESSAGES conversational turns."""
    system_offset = (
        1
        if (is_openai_compat and history and history[0].get("role") == "system")
        else 0
    )
    max_total = MAX_HISTORY_MESSAGES + system_offset

    if len(history) <= max_total:
        return history

    to_remove = len(history) - max_total
    # Gemini requires strict user/model alternation → remove in pairs
    if not is_openai_compat and to_remove % 2 == 1:
        to_remove += 1

    if system_offset:
        return [history[0]] + history[1 + to_remove:]
    return history[to_remove:]


# ─────────────────────────────────────────────────────────────────────────────
#  MODEL FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def _build_request(
    url: str,
    api_key: str,
    provider: str,
    data: Optional[bytes] = None,
) -> urllib.request.Request:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers["HTTP-Referer"] = "urn:chatcli:python"
        headers["X-Title"]      = "PythonChatCLI"
    method = "POST" if data else "GET"
    return urllib.request.Request(url, data=data, headers=headers, method=method)


def fetch_models(provider: str, api_key: str) -> Optional[list[str]]:
    ep = ENDPOINTS[provider]
    if provider == "gemini":
        url = f"{ep['models']}?key={api_key}"
        req = urllib.request.Request(url, method="GET")
    else:
        req = _build_request(ep["models"], api_key, provider)

    try:
        with urllib.request.urlopen(req, timeout=MODEL_FETCH_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        eprint(f"{C.ERROR}HTTP {exc.code} fetching models: "
               f"{truncate(body, 200)}{C.RESET}")
        return None
    except OSError as exc:
        eprint(f"{C.ERROR}Network error fetching models: {exc}{C.RESET}")
        return None

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        eprint(f"{C.ERROR}Invalid JSON from model endpoint.{C.RESET}")
        eprint(f"{C.INFO}Raw (first 300): {truncate(body, 300)}{C.RESET}")
        return None

    # Surface API-level errors
    api_err = data.get("error") if isinstance(data, dict) else None
    if api_err:
        msg = (
            api_err.get("message", str(api_err))
            if isinstance(api_err, dict)
            else str(api_err)
        )
        eprint(f"{C.ERROR}API error: {msg}{C.RESET}")
        return None

    # Extract model IDs per provider
    try:
        if provider == "gemini":
            models = [
                m["name"].replace("models/", "")
                for m in data.get("models", [])
                if (
                    any(
                        "generateContent" in method
                        for method in m.get("supportedGenerationMethods", [])
                    )
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
        eprint(f"{C.INFO}Raw (first 300): {truncate(body, 300)}{C.RESET}")
        return None

    return [m for m in models if m]


# ─────────────────────────────────────────────────────────────────────────────
#  PAYLOAD BUILDING
# ─────────────────────────────────────────────────────────────────────────────

def build_user_message(
    text: str,
    image: ImageAttachment,
    provider: str,
    is_openai_compat: bool,
    is_first: bool,
) -> dict:
    """Construct the user turn dict, injecting system prompt for Gemini on
    turn 1."""
    prompt = text

    if image.attached:
        if not is_openai_compat:          # Gemini multimodal
            if is_first and SYSTEM_PROMPT:
                prompt = f"{SYSTEM_PROMPT}\n\n{text}"
            return {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inlineData": {
                        "mimeType": image.mime,
                        "data": image.base64,
                    }},
                ],
            }
        elif provider == "ollama":        # Ollama native images array
            return {
                "role": "user",
                "content": prompt,
                "images": [image.base64],
            }
        else:                             # OpenAI multimodal
            return {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image.mime};base64,{image.base64}",
                    }},
                ],
            }
    else:
        if not is_openai_compat:          # Gemini text-only
            if is_first and SYSTEM_PROMPT:
                prompt = f"{SYSTEM_PROMPT}\n\nUser: {text}"
            return {"role": "user", "parts": [{"text": prompt}]}
        else:
            return {"role": "user", "content": prompt}


def build_payload(
    provider: str,
    model_id: str,
    history: list[dict],
    is_openai_compat: bool,
    enable_tools: bool,
) -> dict:
    if not is_openai_compat:              # Gemini
        contents = [m for m in history if m.get("role") != "system"]
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature":     DEFAULT_TEMPERATURE,
                "maxOutputTokens": DEFAULT_MAX_TOKENS,
                "topP":            DEFAULT_TOP_P,
            },
        }
        if enable_tools:
            payload["tools"] = [{"urlContext": {}}, {"googleSearch": {}}]
        return payload

    # OpenAI-compatible base
    payload = {
        "model":       model_id,
        "messages":    history,
        "temperature": DEFAULT_TEMPERATURE,
        "stream":      True,
    }
    if provider == "ollama":
        payload["options"] = {
            "num_predict": DEFAULT_MAX_TOKENS,
            "top_p":       DEFAULT_TOP_P,
        }
    elif provider != "together":
        payload["max_tokens"] = DEFAULT_MAX_TOKENS
        payload["top_p"]      = DEFAULT_TOP_P
    return payload


# ─────────────────────────────────────────────────────────────────────────────
#  STREAMING RESPONSE
# ─────────────────────────────────────────────────────────────────────────────

def stream_response(
    provider: str,
    model_id: str,
    history: list[dict],
    is_openai_compat: bool,
    api_key: str,
    enable_tools: bool,
    enable_thinking: bool,
) -> Optional[str]:
    """
    Send the current history to the API and stream the reply to the terminal.
    Returns the assistant reply text (think-tags stripped) on success, or None.

    Text is streamed through a line buffer so complete lines can be rendered
    with Markdown/LaTeX formatting.  <think> tags are handled with a state
    machine identical to the original.
    """
    payload = build_payload(
        provider, model_id, history, is_openai_compat, enable_tools
    )
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    ep = ENDPOINTS[provider]
    if not is_openai_compat:  # Gemini SSE endpoint
        url = (
            f"{ep['chat_base']}{model_id}:streamGenerateContent"
            f"?key={api_key}&alt=sse"
        )
        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    else:
        req = _build_request(ep["chat"], api_key, provider, data=payload_bytes)

    sys.stdout.write(f"{C.AI}AI:{C.RESET} {C.INFO}(💬 Waiting…){C.RESET}")
    sys.stdout.flush()

    # ── State ────────────────────────────────────────────────────────────────
    full_text       = ""
    full_thinking   = ""
    first_chunk     = True
    is_thinking     = False
    in_think_disp   = False
    finish_reason   = ""
    error_msg       = ""
    interrupted     = False
    line_buffer     = ""
    stream_done     = False

    # Reset markdown renderer state (in case previous response was interrupted
    # mid-code-block)
    MD_RENDERER.in_code_block = False

    # ── Helper: flush normal text through the markdown line buffer ───────────
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

    # ── Stream ───────────────────────────────────────────────────────────────
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            buffer = ""
            while not stream_done:
                # Read with instant delivery
                try:
                    raw = _read_chunk(resp)
                except KeyboardInterrupt:
                    interrupted = True
                    cprint(f"\n{C.WARN}(Stream interrupted by user){C.RESET}")
                    break
                if not raw:
                    break
                buffer += raw.decode("utf-8", errors="replace")

                # Split on newlines, keep incomplete tail in buffer
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")

                    # ── Determine json_chunk from line ──────────────────
                    json_chunk = ""
                    if line.startswith("data: "):
                        json_chunk = line[6:].strip()
                        if json_chunk == "[DONE]":
                            stream_done = True
                            break
                    elif line.startswith("{"):
                        json_chunk = line

                    if not json_chunk:
                        continue

                    try:
                        obj = json.loads(json_chunk)
                    except json.JSONDecodeError:
                        continue

                    # ── Error check ─────────────────────────────────────
                    chunk_err = None
                    if isinstance(obj.get("error"), dict):
                        chunk_err = obj["error"].get(
                            "message", str(obj["error"])
                        )
                    elif isinstance(obj.get("error"), str):
                        chunk_err = obj["error"]
                    elif isinstance(obj.get("detail"), str):
                        chunk_err = obj["detail"]
                    if chunk_err:
                        error_msg = f"API error: {chunk_err}"
                        stream_done = True
                        break

                    # ── Extract text / thinking / finish_reason ─────────
                    text_tok   = ""
                    think_tok  = ""
                    cur_finish = ""

                    if is_openai_compat:
                        if provider == "ollama":
                            text_tok = (
                                obj.get("message", {}).get("content") or ""
                            )
                            think_tok = (
                                obj.get("message", {}).get("thinking") or ""
                            )
                            if obj.get("done") is True:
                                cur_finish = "stop"
                        else:
                            delta = (
                                (obj.get("choices") or [{}])[0]
                                .get("delta", {})
                            )
                            text_tok = delta.get("content") or ""
                            think_tok = delta.get("reasoning") or ""
                            cur_finish = (
                                (obj.get("choices") or [{}])[0]
                                .get("finish_reason") or ""
                            )
                    else:
                        # Gemini
                        candidate = (obj.get("candidates") or [{}])[0]

                        # Prompt-level safety block
                        pf = obj.get("promptFeedback")
                        if pf and pf.get("blockReason"):
                            error_msg = (
                                f"Content blocked "
                                f"(reason: {pf['blockReason']})"
                            )
                            stream_done = True
                            break

                        content_obj = candidate.get("content", {})
                        part_list = content_obj.get("parts", [])
                        text_tok = (
                            part_list[0].get("text", "") if part_list else ""
                        )
                        cur_finish = candidate.get("finishReason", "")

                        # Safety ratings check
                        if not cur_finish or cur_finish == "null":
                            safety_ratings = candidate.get(
                                "safetyRatings", []
                            )
                            blocked = next(
                                (r for r in safety_ratings if r.get("blocked")),
                                None,
                            )
                            if blocked:
                                cur_finish = "SAFETY"

                        # Tool calls (informational only)
                        if enable_tools and part_list:
                            tool_parts = [
                                p for p in part_list if "functionCall" in p
                            ]
                            if tool_parts:
                                if first_chunk:
                                    sys.stdout.write(C.CLR)
                                    sys.stdout.write(f"{C.AI}AI:{C.RESET}  ")
                                    first_chunk = False
                                cprint(
                                    f"\n{C.WARN}Tool call "
                                    f"requested 🌐:{C.RESET}"
                                )
                                for tp in tool_parts:
                                    cprint(json.dumps(tp, indent=2))
                                cprint(
                                    f"{C.WARN}(This script does not "
                                    f"automatically execute tool calls or "
                                    f"return tool output to the "
                                    f"model.){C.RESET}"
                                )

                    if cur_finish and not finish_reason:
                        finish_reason = cur_finish

                    # ── Print first-chunk prefix ────────────────────────
                    if first_chunk and (text_tok or think_tok):
                        sys.stdout.write(C.CLR)
                        sys.stdout.write(f"{C.AI}AI:{C.RESET}  ")
                        first_chunk = False

                    # ── Native thinking field (Ollama / OpenAI reasoning)
                    if think_tok:
                        full_thinking += think_tok
                        if enable_thinking:
                            if not in_think_disp:
                                sys.stdout.write(f"{C.THINK}[Thinking] ")
                                in_think_disp = True
                            sys.stdout.write(
                                f"{C.THINK}{think_tok}{C.RESET}"
                            )
                            sys.stdout.flush()

                    # ── Text token with <think> state machine ───────────
                    if text_tok:
                        full_text += text_tok
                        remaining = text_tok

                        while remaining:
                            if is_thinking:
                                close = remaining.find("</think")
                                if close != -1:
                                    think_before = remaining[:close]
                                    after = remaining[close + 7:]
                                    bracket = after.find(">")
                                    after = (
                                        after[bracket + 1:]
                                        if bracket != -1
                                        else ""
                                    )
                                    if enable_thinking:
                                        sys.stdout.write(
                                            f"{C.THINK}"
                                            f"{think_before}"
                                            f"{C.RESET}"
                                        )
                                    sys.stdout.write(f"{C.RESET}\n")
                                    sys.stdout.flush()
                                    is_thinking   = False
                                    in_think_disp = False
                                    remaining     = after
                                else:
                                    # Still inside thinking
                                    if enable_thinking:
                                        sys.stdout.write(
                                            f"{C.THINK}"
                                            f"{remaining}"
                                            f"{C.RESET}"
                                        )
                                        sys.stdout.flush()
                                    remaining = ""
                            else:
                                open_idx = remaining.find("<think")
                                if open_idx != -1:
                                    before = remaining[:open_idx]
                                    after  = remaining[open_idx + 6:]
                                    bracket = after.find(">")
                                    after = (
                                        after[bracket + 1:]
                                        if bracket != -1
                                        else ""
                                    )

                                    # Flush text before the tag
                                    if before:
                                        _flush_text(before)

                                    # Flush any partial line before
                                    # entering thinking mode
                                    if line_buffer:
                                        rendered = (
                                            MD_RENDERER.render_line(
                                                line_buffer
                                            )
                                        )
                                        sys.stdout.write(
                                            f"{C.AI}{rendered}{C.RESET}"
                                        )
                                        sys.stdout.flush()
                                        line_buffer = ""

                                    if enable_thinking:
                                        sys.stdout.write(
                                            f"\n{C.THINK}[Thinking] "
                                        )
                                        in_think_disp = True
                                    is_thinking = True
                                    remaining   = after
                                else:
                                    # Normal text → markdown line buffer
                                    _flush_text(remaining)
                                    remaining = ""

                    # ── Gemini non-standard finish reasons ──────────────
                    if (
                        not is_openai_compat
                        and finish_reason
                        in ("SAFETY", "RECITATION", "OTHER")
                    ):
                        if not full_text:
                            error_msg = (
                                f"Stream ended by API "
                                f"(reason: {finish_reason})"
                            )
                        stream_done = True
                        break

                    # ── Ollama stop ─────────────────────────────────────
                    if provider == "ollama" and finish_reason == "stop":
                        stream_done = True
                        break

    except KeyboardInterrupt:
        interrupted = True
        if first_chunk:
            sys.stdout.write(C.CLR)
        cprint(f"\n{C.WARN}(Request interrupted by user){C.RESET}")
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

    # ── Post-stream ─────────────────────────────────────────────────────────

    # Flush remaining line buffer
    if line_buffer:
        rendered = MD_RENDERER.render_line(line_buffer)
        sys.stdout.write(f"{C.AI}{rendered}{C.RESET}\n")
        sys.stdout.flush()
        line_buffer = ""

    # Reset markdown state
    MD_RENDERER.in_code_block = False

    if first_chunk and not error_msg and not interrupted:
        sys.stdout.write(C.CLR)
        cprint(f"{C.AI}AI:{C.RESET} {C.INFO}(empty response){C.RESET}")
    elif not first_chunk:
        sys.stdout.write(f"{C.RESET}\n")
        sys.stdout.flush()

    if error_msg:
        cprint(f"{C.ERROR}{error_msg}{C.RESET}")
        return None

    # On interrupt, return whatever we got so far (if any)
    if interrupted and full_text:
        eprint(f"{C.INFO}(Partial response saved to history){C.RESET}")

    # Truncate oversized responses
    if len(full_text) > MAX_MESSAGE_LENGTH:
        eprint(
            f"{C.WARN}Response truncated at "
            f"{MAX_MESSAGE_LENGTH} chars.{C.RESET}"
        )
        full_text = full_text[:MAX_MESSAGE_LENGTH]

    clean = strip_think_tags(full_text)
    return clean if clean else None


# ─────────────────────────────────────────────────────────────────────────────
#  HISTORY DISPLAY HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _extract_display_text(msg: dict) -> str:
    """Extract readable text from any message format for /history display."""
    raw = msg.get("content") or msg.get("parts", [{}])

    if isinstance(raw, str):
        return raw

    if isinstance(raw, list) and raw:
        if isinstance(raw[0], dict):
            # Try Gemini parts format
            text = raw[0].get("text", "")
            if text:
                return text

            # Try OpenAI multimodal format
            for part in raw:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        return text

            # Check if there's an image attachment
            has_image = any(
                isinstance(p, dict)
                and (p.get("type") == "image_url" or "inlineData" in p)
                for p in raw
            )
            return "[📎 image]" if has_image else "[content]"

        return str(raw)

    return str(raw) if raw else "[empty]"


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
  {C.BOLD}/save <name>{C.RESET}        Save session  (~/.chat_sessions/<name>.json)
  {C.BOLD}/load <name>{C.RESET}        Load a saved session
  {C.BOLD}/clear{C.RESET}              Delete all saved sessions
  {C.BOLD}/upload <path>{C.RESET}      Attach an image {C.IMAGE}(vision models){C.RESET}
  {C.BOLD}/image{C.RESET}              Show attached image info
  {C.BOLD}/clearimage{C.RESET}         Remove attached image
  {C.BOLD}/togglethinking{C.RESET}     Toggle reasoning display {C.THINK}(reasoning models){C.RESET}
  {C.BOLD}/help{C.RESET}               Show this help
  {C.BOLD}quit{C.RESET} / {C.BOLD}exit{C.RESET}          End session

{C.INFO}Features:{C.RESET}
  • Markdown rendering (Bold, Italic, Inline code, Fenced code blocks)
  • LaTeX rendering  (Greek letters, superscripts, fractions → Unicode)

{C.INFO}Examples:{C.RESET}
  {C.AI}python {me} gemini{C.RESET}
  {C.AI}python {me} openrouter claude{C.RESET}
  {C.AI}python {me} groq llama{C.RESET}
  {C.AI}python {me} ollama{C.RESET}

{C.IMAGE}Image support:{C.RESET}
  Supports JPEG, PNG, GIF, WebP. Max {MAX_IMAGE_SIZE_MB} MB per image.
  Usage: {C.BOLD}/upload ~/photo.jpg{C.RESET}, then type your question.

{C.THINK}Thinking output:{C.RESET}
  Displays reasoning/thinking content from supported models in orange.
  Toggle with {C.BOLD}/togglethinking{C.RESET} during chat.

{C.WARN}Set your API keys in the API_KEYS dict at the top of this script.{C.RESET}""")


def print_chat_help() -> None:
    """Shorter help displayed during an active chat session."""
    cprint(f"""{C.INFO}Commands:{C.RESET}
  {C.BOLD}/history{C.RESET}            Show conversation
  {C.BOLD}/save <name>{C.RESET}        Save session
  {C.BOLD}/load <name>{C.RESET}        Load session
  {C.BOLD}/clear{C.RESET}              Delete all sessions
  {C.BOLD}/upload <path>{C.RESET}      Attach image
  {C.BOLD}/image{C.RESET}              Show attached image
  {C.BOLD}/clearimage{C.RESET}         Remove image
  {C.BOLD}/togglethinking{C.RESET}     Toggle reasoning display
  {C.BOLD}/help{C.RESET}               Show this help
  {C.BOLD}quit{C.RESET} / {C.BOLD}exit{C.RESET}          End session""")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN CHAT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def chat_loop(
    provider: str,
    model_id: str,
    is_openai_compat: bool,
    api_key: str,
    enable_tools: bool,
) -> None:

    # ── readline setup ───────────────────────────────────────────────────────
    if _READLINE_AVAILABLE:
        readline.set_history_length(1000)
        if HISTORY_FILE.exists():
            try:
                readline.read_history_file(str(HISTORY_FILE))
            except OSError:
                pass
        atexit.register(
            lambda: readline.write_history_file(str(HISTORY_FILE))
        )

    # ── state ────────────────────────────────────────────────────────────────
    history:      list[dict]     = init_history(is_openai_compat)
    image:        ImageAttachment = ImageAttachment()
    first_msg:    bool           = True
    thinking_on:  bool           = ENABLE_THINKING_OUTPUT

    # ── startup banner ───────────────────────────────────────────────────────
    sep = "─" * 85
    cprint(f"\n{sep}")
    cprint(f"  {C.INFO}Provider:{C.RESET}  {provider.upper()}   "
           f"{C.INFO}Model:{C.RESET}  {model_id}")
    cprint(f"  {C.INFO}History:{C.RESET}   last {MAX_HISTORY_MESSAGES} turns  │  "
           f"{C.INFO}Temp:{C.RESET} {DEFAULT_TEMPERATURE}  │  "
           f"{C.INFO}Tokens:{C.RESET} {DEFAULT_MAX_TOKENS}  │  "
           f"{C.INFO}TopP:{C.RESET} {DEFAULT_TOP_P}")
    cprint(f"  {C.INFO}Max message:{C.RESET}  {MAX_MESSAGE_LENGTH:,} characters")
    if SYSTEM_PROMPT:
        label = (
            "prepended to first message"
            if not is_openai_compat
            else "active"
        )
        cprint(f"  {C.INFO}System prompt:{C.RESET}  {label}")
    else:
        cprint(f"  {C.INFO}System prompt:{C.RESET}  inactive (empty)")
    if provider == "gemini":
        status = (
            f"{C.BOLD}enabled{C.RESET}" if enable_tools else "disabled"
        )
        cprint(f"  {C.INFO}Tool calling:{C.RESET}  {status}")
    cprint(f"  {C.INFO}Rendering:{C.RESET}   Markdown + LaTeX → Unicode")
    think_status = (
        f"{C.BOLD}{C.THINK}enabled{C.RESET}"
        if thinking_on
        else "disabled"
    )
    cprint(f"  {C.INFO}Thinking output:{C.RESET}  {think_status}  "
           f"(toggle: /togglethinking)")
    cprint(f"  Type {C.BOLD}quit{C.RESET} or {C.BOLD}exit{C.RESET} to end  │  "
           f"{C.BOLD}/help{C.RESET} for all commands")
    cprint(sep + "\n")

    # ── REPL ─────────────────────────────────────────────────────────────────
    while True:
        # Build input prompt
        img_tag = (
            f"[{C.IMAGE}📎 {Path(image.path).name}{C.RESET}] "
            if image.attached
            else ""
        )
        prompt = f"{img_tag}{C.BOLD}{C.USER}You:{C.RESET} "

        try:
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            cprint(f"\n{C.INFO}Ending session.{C.RESET}")
            break

        # ── Exit ─────────────────────────────────────────────────────────
        if user_input.lower() in ("quit", "exit"):
            break

        # ── Slash commands ───────────────────────────────────────────────
        if user_input.startswith("/"):
            parts = user_input.split(None, 1)
            cmd   = parts[0].lower()
            args  = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/help":
                print_chat_help()
                continue

            elif cmd == "/upload":
                if not args:
                    eprint(f"{C.IMAGE}Usage: /upload <image_path>{C.RESET}")
                else:
                    image.load(args)
                continue

            elif cmd == "/image":
                if image.attached:
                    cprint(f"{C.IMAGE}Attached: {image.path}  "
                           f"({image.mime}){C.RESET}")
                else:
                    cprint(f"{C.IMAGE}No image attached.{C.RESET}")
                continue

            elif cmd == "/clearimage":
                image.clear()
                cprint(f"{C.IMAGE}Image cleared.{C.RESET}")
                continue

            elif cmd == "/togglethinking":
                thinking_on = not thinking_on
                state = (
                    f"{C.BOLD}{C.THINK}enabled{C.RESET}"
                    if thinking_on
                    else f"{C.BOLD}disabled{C.RESET}"
                )
                cprint(f"{C.INFO}Thinking output {state}.{C.RESET}")
                continue

            elif cmd == "/history":
                cprint(f"{C.INFO}── History ({len(history)} messages) "
                       f"─────────────────────{C.RESET}")
                if not history:
                    cprint("  (empty)")
                for msg in history:
                    role = msg.get("role", "?")
                    text = _extract_display_text(msg)
                    colour = (
                        C.USER if role == "user"
                        else C.AI if role in ("assistant", "model")
                        else C.WARN
                    )
                    cprint(f"  {colour}[{role}]{C.RESET}  "
                           f"{truncate(text, 500)}")
                cprint(f"{C.INFO}──────────────────────────────"
                       f"─────────────────────{C.RESET}")
                continue

            elif cmd == "/save":
                if not args:
                    eprint(f"{C.WARN}Usage: /save <name>{C.RESET}")
                elif validate_session_name(args):
                    save_session(args, history)
                continue

            elif cmd == "/load":
                if not args:
                    eprint(f"{C.WARN}Usage: /load <name>{C.RESET}")
                elif validate_session_name(args):
                    loaded = load_session(args)
                    if loaded is not None:
                        history   = loaded
                        first_msg = False
                continue

            elif cmd == "/clear":
                clear_sessions()
                continue

            else:
                eprint(f"{C.WARN}Unknown command '{cmd}'. "
                       f"Type /help for a list.{C.RESET}")
                continue

        # ── Guard: empty input ───────────────────────────────────────────
        if not user_input and not image.attached:
            continue

        # Default prompt when only an image is attached
        if not user_input and image.attached:
            user_input = "Describe this image in detail."

        # Length guard
        if len(user_input) > MAX_MESSAGE_LENGTH:
            eprint(f"{C.ERROR}Message too long "
                   f"({len(user_input):,} chars, "
                   f"max {MAX_MESSAGE_LENGTH:,}).{C.RESET}")
            continue

        eprint(f"{C.INFO}[Sending…]{C.RESET}")

        # ── Build user message ───────────────────────────────────────────
        user_msg = build_user_message(
            user_input, image, provider, is_openai_compat, first_msg
        )
        image.clear()
        first_msg = False

        history.append(user_msg)
        history = truncate_history(history, is_openai_compat)

        # ── Call API ─────────────────────────────────────────────────────
        ai_text = stream_response(
            provider, model_id, history, is_openai_compat,
            api_key, enable_tools, thinking_on,
        )

        if ai_text:
            if not is_openai_compat:
                history.append(
                    {"role": "model", "parts": [{"text": ai_text}]}
                )
            else:
                history.append({"role": "assistant", "content": ai_text})
        else:
            # Rollback the user message on failure
            if history and history[-1].get("role") == "user":
                history.pop()
                eprint(f"{C.WARN}(User message rolled back "
                       f"due to error){C.RESET}")

        cprint("")   # blank line between turns


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Validate config
    for name, val, lo, hi in [
        ("DEFAULT_TEMPERATURE", DEFAULT_TEMPERATURE, 0, 2),
        ("DEFAULT_TOP_P",       DEFAULT_TOP_P,       0, 1),
        ("DEFAULT_MAX_TOKENS",  DEFAULT_MAX_TOKENS,  1, 1_000_000),
    ]:
        if not (lo <= val <= hi):
            sys.exit(
                f"Config error: {name}={val} must be between {lo} and {hi}"
            )

    # Argument parsing
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print_usage()
        sys.exit(0)

    provider = argv[0].lower()
    filters  = argv[1:]

    if provider not in VALID_PROVIDERS:
        cprint(f"{C.ERROR}Unknown provider '{provider}'. "
               f"Choose from: {', '.join(VALID_PROVIDERS)}{C.RESET}")
        sys.exit(1)

    api_key = API_KEYS.get(provider, "")
    if not check_placeholder_key(api_key, provider):
        sys.exit(1)

    # Determine compatibility mode
    is_openai_compat = (provider != "gemini")

    # ── Optional: Gemini tool-calling prompt ─────────────────────────────
    enable_tools = False
    if provider == "gemini":
        while True:
            try:
                ans = input(
                    f"{C.INFO}Enable Gemini tool calling "
                    f"(web search, URL context)? (y/n): {C.RESET}"
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                sys.exit(0)
            if ans in ("y", "1", "yes"):
                enable_tools = True
                cprint(f"{C.INFO}Tool calling enabled.{C.RESET}")
                break
            elif ans in ("n", "0", "no"):
                cprint(f"{C.INFO}Tool calling disabled.{C.RESET}")
                break
            else:
                eprint(f"{C.WARN}Please enter y or n.{C.RESET}")

    # ── Fetch models ─────────────────────────────────────────────────────
    cprint(f"{C.INFO}Fetching models for {provider.upper()}…{C.RESET}")
    models = fetch_models(provider, api_key)
    if not models:
        cprint(f"{C.ERROR}No models returned by {provider.upper()}.{C.RESET}")
        sys.exit(1)

    if filters:
        models = filter_models(models, filters)

    if not models:
        cprint(f"{C.ERROR}No models matched filter: "
               f"{' '.join(filters)}{C.RESET}")
        cprint(f"{C.INFO}Tip: filters use word-boundary matching "
               f"('3' matches 'gpt-3' but not '13b').{C.RESET}")
        sys.exit(1)

    # ── Model selection ──────────────────────────────────────────────────
    if len(models) == 1:
        model_id = models[0]
        cprint(f"{C.INFO}Auto-selected:{C.RESET} {model_id}")
    else:
        cprint(f"{C.INFO}Available models for {provider.upper()}:{C.RESET}")
        for i, m in enumerate(models, 1):
            cprint(f"  {C.BOLD}{i:3}{C.RESET}. {m}")
        while True:
            try:
                choice = input(
                    f"{C.INFO}Select model number: {C.RESET}"
                ).strip()
            except (EOFError, KeyboardInterrupt):
                sys.exit(0)
            if choice.isdigit() and 1 <= int(choice) <= len(models):
                model_id = models[int(choice) - 1]
                break
            eprint(f"{C.WARN}Enter a number between 1 "
                   f"and {len(models)}.{C.RESET}")

    cprint(f"{C.INFO}Using model:{C.RESET} {model_id}\n")

    # ── Graceful Ctrl-C outside of streaming ─────────────────────────────
    def _sigint_handler(sig, frame):
        cprint(f"\n{C.WARN}Interrupted.{C.RESET}")
        sys.exit(130)

    signal.signal(signal.SIGINT, _sigint_handler)

    # ── Start chat ───────────────────────────────────────────────────────
    chat_loop(provider, model_id, is_openai_compat, api_key, enable_tools)
    cprint("👋 Session ended.")


if __name__ == "__main__":
    main()
