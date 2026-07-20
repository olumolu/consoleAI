# consoleAI

<div align="center">

**A powerful, zero-dependency Python CLI for chatting with LLMs across 9 providers.**

Pure stdlib · Python 3.9+ · No `pip install` — ever.

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)](https://docs.python.org/3/library/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows%20%7C%20Termux-lightgrey)](#platforms)

</div>

---

## ✨ What Makes It Different

| | consoleAI | Typical CLI wrappers |
|---|---|---|
| **Dependencies** | Zero — pure Python stdlib | `pip install openai requests rich …` |
| **Providers** | 9 built-in, switchable at runtime | Usually 1–2 |
| **Web research** | Multi-source engine (Startpage + DuckDuckGo) with auto-enforcement | None |
| **Tools** | 12 built-in (search, fetch, weather, calculator, …) | Basic or none |
| **Security** | SSRF protection with DNS-pinning on every URL | Rarely considered |
| **UI** | Interactive TUI pickers, live spinner, Markdown + LaTeX rendering | Plain text |
| **Streaming** | Token-by-token with thinking/reasoning display | Hit or miss |

---

## 📸 Demo

https://github.com/user-attachments/assets/937a8a68-5407-4242-a42b-718252e59a93

---

## 📋 Table of Contents

- [Features](#-features)
- [Installation](#-installation)
- [Quick Start](#-quick-start)
- [Providers](#-providers)
- [Chat Commands](#-chat-commands)
- [Tool Calling](#-tool-calling)
- [Web Research Engine](#-web-research-engine)
- [Interactive UI](#-interactive-ui)
- [Security](#-security)
- [Configuration](#-configuration)
- [Platforms](#-platforms)
- [Legacy Bash Script](#-legacy-bash-script-aish)
- [Project Structure](#-project-structure)

---

## 🚀 Features

### Core

- 🌐 **9 Providers** — Gemini, OpenRouter, Groq, Together, Cerebras, Novita, Cloudflare, Nvidia, Ollama
- ⚡ **Streaming** — Token-by-token output with live Markdown/LaTeX rendering
- 🧠 **Thinking Display** — Toggle reasoning traces from supported models (`/togglethinking`)
- 🎨 **Rich Terminal Output** — Color-coded roles, fenced code blocks, Greek letters, fractions → Unicode
- 💾 **Session Management** — Save, load, and clear conversation sessions as JSON
- 📋 **Multi-line Input** — Backslash continuation + `/paste` bulk mode
- 🔄 **Live Model Switching** — Change models mid-conversation with `/model` (history preserved)

### Vision

- 🖼️ **Image Attachments** — Send images to vision-capable models
  - Supports JPEG, PNG, WebP, GIF, AVIF, HEIC, TIFF, JXL (up to 20 MB)
  - `/upload <path>` · `/image` · `/clearimage`

### Tools (12 built-in)

- 🛠️ **Function Calling** — The model autonomously decides when to use tools
- 🔍 **Web Search** — Startpage + DuckDuckGo Lite HTML scraping (no API keys)
- 📚 **Deep Research** — Multi-source research with automatic source-count enforcement
- 🌤️ **Weather** — Current conditions via wttr.in (no API key)
- 📖 **Dictionary** — Definitions, phonetics, synonyms via dictionaryapi.dev
- 🔐 **Password Generator** — Cryptographically secure (`secrets` module)
- 💻 **System Info** — OS, CPU, RAM, disk, uptime
- 📐 **Unit Converter** — Length, weight, temperature, data, volume, speed
- 🔑 **Hash / Encode** — MD5, SHA-1, SHA-256, SHA-512, Base64
- 🧮 **Calculator** — Safe AST-based math evaluation (no `eval()`)
- 🕐 **Time** — Current local date & time
- 🌍 **Wikipedia** — Search and extract article text

### Security

- 🛡️ **SSRF Protection** — DNS-pinning, private IP blocking, redirect validation on every outbound URL
- 🔒 **Session Files** — `0o600` permissions, `0o700` directory
- 🧪 **Safe Calculator** — AST-walked evaluation, no arbitrary code execution

---

## 📦 Installation

### Requirements

- **Python 3.9+** (that's it — no pip, no venv, no packages)

### Setup

```bash
# 1. Clone
git clone https://github.com/olumolu/consoleAI.git
cd consoleAI

# 2. Add your API keys (open ai.py, find the API_KEYS dict)
#    Or use environment variables:
export GEMINI_API_KEY="your-key"
export OPENROUTER_API_KEY="your-key"
export GROQ_API_KEY="your-key"
# ... etc.

# 3. Run
python ai.py
```

> [!IMPORTANT]
> You **must** provide at least one API key. Open `ai.py` and edit the `API_KEYS` dictionary at the top, or export environment variables. Ollama works without a key for local models.

---

## 🏁 Quick Start

```bash
# Interactive provider picker (no arguments)
python ai.py

# Direct provider selection
python ai.py gemini
python ai.py groq
python ai.py openrouter
python ai.py ollama

# Filter models by keyword
python ai.py openrouter claude sonnet
python ai.py gemini pro
python ai.py groq llama 3
python ai.py together 70b
```

On launch you'll be asked:
1. **Enable thinking/reasoning?** — Shows `<think>` traces from reasoning models
2. **Enable tool calling?** — Lets the model use the 12 built-in tools
3. **Model picker** — Interactive searchable list with keyboard navigation

---

## 🌐 Providers

| Provider | Endpoint | API Key | Notes |
|----------|----------|---------|-------|
| **Gemini** | `generativelanguage.googleapis.com` | [AI Studio](https://aistudio.google.com/app/apikey) | Native Gemini API format |
| **OpenRouter** | `openrouter.ai` | [Keys](https://openrouter.ai/keys) | 200+ models via one key |
| **Groq** | `api.groq.com` | [Console](https://console.groq.com/keys) | Ultra-fast inference |
| **Together** | `api.together.ai` | [Settings](https://api.together.ai/settings/api-keys) | Open-source models |
| **Cerebras** | `api.cerebras.ai` | [Cloud](https://cloud.cerebras.ai/) | Wafer-scale speed |
| **Novita** | `api.novita.ai` | [Dashboard](https://novita.ai/) | 200+ serverless models |
| **Cloudflare** | `api.cloudflare.com` | [Dash](https://dash.cloudflare.com/) | Format: `ACCOUNT_ID:API_TOKEN` |
| **Nvidia** | `integrate.api.nvidia.com` | [Build](https://build.nvidia.com/) | NIM endpoints |
| **Ollama** | `ollama.com` | *(none for local)* | Local or cloud models |

---

## 💬 Chat Commands

| Command | Description |
|---------|-------------|
| `/history` | Show full conversation history with roles |
| `/model [filter]` | Open interactive model picker (optionally pre-filtered) |
| `/save <name>` | Save session → `~/.chat_sessions/<name>.json` |
| `/load <name>` | Load a previously saved session |
| `/clear` | Delete **all** saved sessions (with confirmation) |
| `/upload <path>` | Attach an image to your next message |
| `/image` | Show currently attached image info |
| `/clearimage` | Remove the attached image |
| `/paste [text]` | Multi-line paste mode — end with `---` on its own line |
| `/togglethinking` | Toggle reasoning/thinking trace display |
| `/toggletools` | Toggle tool calling on/off |
| `/help` | Show all available commands |
| `quit` / `exit` | End the session |

### Multi-line Input

```
You: Explain the difference between \
  .. TCP and UDP in networking
```

Or use paste mode for large blocks:

```
You: /paste
  │ def hello():
  │     print("world")
  │ ---
```

---

## 🛠️ Tool Calling

When enabled (`/toggletools`), the model autonomously invokes tools as needed:

| Tool | Description | API Key? |
|------|-------------|----------|
| `get_time` | Current local date & time | ❌ |
| `calculator` | Safe math evaluation (`**` = power, `^` = XOR) | ❌ |
| `web_search` | Quick lookup via Startpage → DuckDuckGo fallback | ❌ |
| `web_research` | Deep multi-source research with excerpts | ❌ |
| `fetch_url` | Fetch & extract readable text from any URL | ❌ |
| `wikipedia` | Search Wikipedia, return relevant excerpts | ❌ |
| `weather` | Current conditions for any city (wttr.in) | ❌ |
| `dictionary` | Definitions, phonetics, synonyms, antonyms | ❌ |
| `password_gen` | Cryptographically secure passwords | ❌ |
| `system_info` | OS, CPU, RAM, disk usage, uptime | ❌ |
| `unit_convert` | Length, weight, temp, data, volume, speed | ❌ |
| `hash_encode` | MD5 / SHA-1 / SHA-256 / SHA-512 / Base64 | ❌ |

> Every tool runs in a sandboxed thread with a 60-second timeout. The calculator uses AST walking — **no `eval()`**.

---

## 🔍 Web Research Engine

The research engine is the most sophisticated part of the toolset:

```
User: "What's the latest version of FastAPI and what changed?"

  ⠹ Building candidate list…
  ⠹ Searching Startpage: latest version FastAPI 2026
  ⠹ Searching DuckDuckGo: FastAPI official documentation
  ⠹ Fetching sources… 6/18 candidates
  ⠹ Fetching sources… 12/18 candidates

⚡ Tool: web_research({"query": "latest version FastAPI", "max_sources": 5})
   → [1] FastAPI 0.115.0 — Release Notes
     URL: https://github.com/tiangolo/fastapi/releases
     ...
```

### How it works

1. **Query expansion** — Generates up to 4 search variants (adds year, "official documentation", exact-match quotes)
2. **Dual search** — Tries Startpage first; falls back to DuckDuckGo Lite if Startpage returns zero results or a captcha
3. **Domain diversity** — Deduplicates by domain, prioritizes docs/developer/gov/edu sites
4. **Parallel fetching** — Fetches up to 6 pages concurrently with per-page timeouts
5. **Excerpt extraction** — Scores text chunks by relevance, returns top excerpts per source
6. **Auto-enforcement** — If the model tries to answer with < 4 sources on a factual question, the CLI automatically prompts it to research more

---

## 🖥️ Interactive UI

### Provider Picker

Launch with no arguments to get a centered TUI panel:

```
┌──────────────────────────────────────────────────┐
│ SELECT PROVIDER                       esc cancel │
│                                                  │
│   1. Gemini                                      │
│   2. OpenRouter                                  │
│ ▌ 3. Groq                                      ▌│  ← highlighted
│   4. Together                                    │
│   ...                                            │
│                                                  │
│ ↑/↓ move • Enter select • digits jump           │
└──────────────────────────────────────────────────┘
```

### Model Picker

Full-screen alternate screen with:
- **Live search** — Type to filter models instantly
- **Scroll indicator** — Thumb position for long lists
- **Number jump** — Type digits to jump to a model number
- **Current marker** — Shows `[current]` next to the active model
- **Keyboard nav** — `↑↓` `Home` `End` `PgUp` `PgDn` `Enter` `Esc` `Backspace`

### Confirm Dialogs

Inline Yes/No selector for settings:

```
✦ Enable thinking/reasoning?   Yes  No
                              ^^^^
                         (arrow keys + Enter)
```

---

## 🛡️ Security

| Layer | Protection |
|-------|-----------|
| **SSRF** | DNS-pinning before every request; blocks `127.0.0.1`, `10.x`, `172.16-31.x`, `192.168.x`, `169.254.x`, `::1`, link-local, multicast, cloud metadata endpoints |
| **Redirects** | Every HTTP redirect is re-validated against the SSRF blocklist |
| **Calculator** | AST-walked — only whitelisted functions/operators; no `eval()`, no `exec()`, no imports |
| **Sessions** | Files created with `0o600`, directory with `0o700` |
| **URLs** | Length-capped at 8192 chars; only `http://` and `https://` schemes allowed |
| **Cookies** | Fresh cookie jar per outbound fetch (no cross-site leakage) |
| **Response size** | Hard cap at 5 MB per fetch; Content-Length pre-check |

---

## ⚙️ Configuration

Edit the constants at the top of `ai.py`:

```python
# ── API Keys ──────────────────────────────────────────
API_KEYS: dict[str, str] = {
    "gemini":     "",   # https://aistudio.google.com/app/apikey
    "openrouter": "",   # https://openrouter.ai/keys
    "groq":       "",   # https://console.groq.com/keys
    "together":   "",   # https://api.together.ai/settings/api-keys
    "cerebras":   "",   # https://cloud.cerebras.ai/
    "novita":     "",   # https://novita.ai/
    "cloudflare": "",   # Format: ACCOUNT_ID:API_TOKEN
    "nvidia":     "",   # https://build.nvidia.com/
    "ollama":     "",   # Leave blank for local Ollama
}

# ── Behaviour ─────────────────────────────────────────
MAX_HISTORY_MESSAGES  = 20        # Turns kept in context window
MAX_MESSAGE_LENGTH    = 50_000    # Max chars per user message
DEFAULT_TEMPERATURE   = 0.9
DEFAULT_MAX_TOKENS    = 3000
DEFAULT_TOP_P         = 1.0
MAX_TOOL_ITERATIONS   = 10        # Tool calls per user turn
TOOL_EXEC_TIMEOUT     = 60        # Seconds before a tool is killed
REQUEST_TIMEOUT       = 300       # Seconds for API streaming

# ── Research Engine ───────────────────────────────────
RESEARCH_MIN_SOURCES     = 4      # Minimum before auto-enforcement kicks in
RESEARCH_TARGET_SOURCES  = 5      # Default target per research call
RESEARCH_MAX_SOURCES     = 8      # Hard cap
RESEARCH_BATCH_SIZE      = 6      # Concurrent page fetches
RESEARCH_MAX_CANDIDATES  = 30     # Max URLs considered per query

# ── System Prompt ─────────────────────────────────────
SYSTEM_PROMPT = """You are a highly helpful assistant..."""
```

---

## 🖥️ Platforms

| Platform | Status | Notes |
|----------|--------|-------|
| **Linux** | ✅ Full | Primary development target |
| **macOS** | ✅ Full | |
| **Windows** | ✅ Full | Uses `msvcrt` for key input |
| **Android (Termux)** | ✅ Full | `pkg install python` then run |
| **WSL** | ✅ Full | Treated as Linux |
| **SSH / Remote** | ✅ Full | Escape-sequence timeout tuned for latency |

---

## 📁 Project Structure

```
consoleAI/
├── ai.py          # Main application (~3,800 lines, pure stdlib)
├── ai.sh          # Legacy bash version (minimal environments)
├── README.md
└── LICENSE
```

---

## 📜 Legacy Bash Script (`ai.sh`)

The original bash version is included for environments without Python.

### Requirements

`bash` · `curl` · `bc` · `jq`

### Usage

```bash
chmod +x ai.sh
./ai.sh gemini
./ai.sh groq llama
./ai.sh openrouter claude
```

### When to Use Which

| Scenario | Use |
|----------|-----|
| No Python available | `ai.sh` |
| Markdown / LaTeX rendering | `ai.py` |
| Tool calling / web research | `ai.py` |
| Interactive TUI pickers | `ai.py` |
| Vision / image attachments | `ai.py` |
| Quick one-off on a server | `ai.sh` |
| Android with limited storage | `ai.sh` |

---

## ❓ FAQ

**Q: Do I need to install anything with pip?**
A: No. The entire application uses only the Python standard library. If you have Python 3.9+, you're done.

**Q: Can I use it without an internet connection?**
A: Yes, with Ollama running locally. All other providers require internet + an API key.

**Q: The web search isn't returning results. What do I do?**
A: The engine tries Startpage first, then falls back to DuckDuckGo Lite automatically. If both are captcha-blocked (rare), try again in a few minutes. No API keys are needed for search.

**Q: Can I add my own tools?**
A: Yes. Add a function to `TOOLS_REGISTRY`, add its schema to `OPENAI_TOOLS_SCHEMA` and `GEMINI_TOOLS_SCHEMA`, and it's available to the model immediately.

**Q: Image generation?**
A: Out of scope — this is a terminal-based *chat* application.

---

<div align="center">

**Built with nothing but the Python standard library.**

No `requests`. No `openai`. No `rich`. No `httpx`. Just Python.

</div>
