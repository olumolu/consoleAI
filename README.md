# consoleAI

A versatile **Python CLI** for interacting with various Large Language Models (LLMs) from multiple inference providers. Pure stdlib, Python 3.9+ — **zero pip installs required**.

> The original bash script (`ai.sh`) is also included for minimal environments.

## Demo
[Screencast From 2026-02-03 23-47-45.webm](https://github.com/user-attachments/assets/937a8a68-5407-4242-a42b-718252e59a93)

---

## Features

### Core Features
- 🌐 **Multi-Provider Support:** Chat with models from:
  - Google Gemini
  - OpenRouter (access to multiple model providers)
  - Groq
  - Together AI
  - Cerebras AI
  - Novita AI
  - Ollama (local or cloud)

- 🎯 **Dynamic Model Selection:** Fetches and lists available models from the chosen provider with optional filtering.

- 💾 **Conversation History:** Remembers the last `N` messages (configurable). Use `/history` to recall conversation logs.

- 📝 **System Prompt:** Define a system-level instruction for the AI (configurable).

- ⚡ **Streaming Responses:** AI responses are streamed token by token for a real-time feel.

- 🎨 **Color-Coded Output:** Differentiates between user input, AI responses, thinking, tools, errors, and info messages.

### Extended Features

- 📖 **Markdown Rendering:** Bold, italic, inline code, fenced code blocks — rendered beautifully in terminal.

- 🔢 **LaTeX Rendering:** Greek letters, superscripts, subscripts, fractions → Unicode (e.g., `α²`, `π/2`).

- 🖼️ **Vision Support:** Attach images for vision-capable models.
  - `/upload <path>` — Attach an image
  - `/image` — Show attached image info
  - `/clearimage` — Remove attached image

- 🔄 **Live Model Switching:** Switch models mid-conversation with `/model` (preserves history).

- 🛠️ **Tool/Function Calling:** When enabled, the model can invoke local tools:
  - `get_time` — Current local date & time
  - `calculator` — Safe arithmetic evaluation (supports `sqrt`, `sin`, `cos`, `tan`, `log`, `exp`, `factorial`, etc.)
  - `web_search` — **Search the web via Startpage** (live results with titles, URLs, snippets)
  - `fetch_url` — Fetch & clean a web page (≤ 8,000 chars)
  - `wikipedia` — Search Wikipedia & return article text
  - Gemini also gets **Google Search** grounding when tools are on.

- 🔍 **Web Search:** Real web search capability via Startpage — no API key required!

- 🧠 **Thinking/Reasoning Output:** Toggle display of reasoning tokens from supported models with `/togglethinking`.

- 📋 **Multi-line Input:**
  - End any line with `\` to continue on the next line
  - Use `/paste [text]` for bulk paste mode (end with `---`)

- 💾 **Session Management:**
  - `/save <name>` — Save session to `~/.chat_sessions/<name>.json`
  - `/load <name>` — Load a saved session
  - `/clear` — Delete all saved sessions

### Security & Reliability

- 🔒 **SSRF Protection:** DNS-pinned URL validation prevents access to private/internal networks (localhost, 127.0.0.1, metadata endpoints, etc.)

- 🔄 **Auto-Retry:** Automatic retry with exponential backoff for HTTP 429/5xx errors.

- ⏱️ **Tool Timeout:** Configurable timeout for tool execution (default 60s).

- 📦 **History Compaction:** Tool messages are auto-collapsed after each exchange to keep context clean.

- 🎯 **Live Progress Spinner:** Animated spinner shows real-time progress during tool execution (web search, URL fetch, etc.).

---

## Installation

### Requirements
- Python 3.9 or newer
- No pip installs required — uses only Python standard library

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/olumolu/consoleAI.git
   cd consoleAI
   ```

2. **Configure API Keys:**

   > [!IMPORTANT]
   > You **MUST** add your API keys. Open `ai.py` and locate the `API_KEYS` dictionary:

   ```python
   API_KEYS: dict[str, str] = {
       "gemini":     "",   # https://aistudio.google.com/app/apikey
       "openrouter": "",   # https://openrouter.ai/keys
       "groq":       "",   # https://console.groq.com/keys
       "together":   "",   # https://api.together.ai/settings/api-keys
       "cerebras":   "",   # https://cloud.cerebras.ai/
       "novita":     "",   # https://novita.ai/
       "ollama":     "",   # Leave blank for local Ollama
   }
   ```

   Alternatively, set environment variables:
   ```bash
   export GEMINI_API_KEY="your-key-here"
   export OPENROUTER_API_KEY="your-key-here"
   # etc.
   ```

3. **Run it:**
   ```bash
   python ai.py <provider> [filter]...
   ```

---

## Usage

### Basic Usage

```bash
python ai.py gemini
python ai.py groq llama
python ai.py openrouter claude
python ai.py together
python ai.py cerebras
python ai.py novita
python ai.py ollama
```

Use the optional `[filter]...` argument to narrow down model selection:
```bash
python ai.py openrouter 32b        # Shows only models with "32b" in the name
python ai.py gemini pro            # Shows only models with "pro" in the name
```

---

## Chat Commands

| Command | Description |
|---------|-------------|
| `/history` | Show conversation history |
| `/model` | Switch to a different model mid-chat |
| `/save <name>` | Save session to `~/.chat_sessions/<name>.json` |
| `/load <name>` | Load a saved session |
| `/clear` | Delete all saved sessions |
| `/upload <path>` | Attach an image to your next message |
| `/image` | Show currently attached image info |
| `/clearimage` | Remove the attached image |
| `/paste [text]` | Multi-line paste mode (end with `---`) |
| `/togglethinking` | Toggle reasoning/thinking output display |
| `/toggletools` | Toggle tool calling on/off |
| `/help` | Show available commands |
| `quit` / `exit` | End the session |

### Multi-line Input
- End any line with `\` to continue on the next line
- Use `/paste` for bulk paste mode (end with `---` on its own line)

---

## Tool Calling

When enabled, the model can invoke local tools:

| Tool | Description |
|------|-------------|
| `get_time` | Get the current local date & time |
| `calculator` | Evaluate mathematical expressions safely (supports `sqrt`, `sin`, `cos`, `tan`, `log`, `exp`, `factorial`, `gcd`, constants `pi`, `e`, `tau`) |
| `web_search` | **Search the web via Startpage** — returns titles, URLs, and snippets |
| `fetch_url` | Fetch & read full content from any web page URL |
| `wikipedia` | Search Wikipedia and return article text |

> **Note:** Gemini also gets **Google Search** grounding when tools are enabled.

### Web Search Example

When you ask the AI something like *"What's the latest news about Python 3.13?"*, it can:
1. Use `web_search` to find current results via Startpage
2. Use `fetch_url` to read the full content of any promising result
3. Summarize the findings for you

All with live progress feedback in the terminal!

---

## Configuration

You can customize settings at the top of `ai.py`:

```python
# Conversation settings
MAX_HISTORY_MESSAGES  = 20        # Messages to keep in context
MAX_MESSAGE_LENGTH    = 50_000    # Max chars per message
DEFAULT_TEMPERATURE   = 0.7       # 0–2: higher = more creative
DEFAULT_MAX_TOKENS    = 3000      # Max tokens in each AI reply
DEFAULT_TOP_P         = 0.9       # 0–1: nucleus sampling

# Tool settings
MAX_TOOL_ITERATIONS   = 10        # Max tool calls per message
TOOL_EXEC_TIMEOUT     = 60        # Seconds before tool times out
SEARCH_MAX_RESULTS    = 6         # Web search results to return
FETCH_MAX_CHARS       = 8000      # Max chars from fetched pages

# Network settings
REQUEST_TIMEOUT       = 300       # Streaming chat timeout
MODEL_FETCH_TIMEOUT   = 30        # Model listing timeout
MAX_RETRIES           = 3         # HTTP retry attempts

# System prompt (set to "" to disable)
SYSTEM_PROMPT = "You are a helpful assistant running in a command-line interface."
```

---

## Security

### SSRF Protection

The `fetch_url` and `web_search` tools include **Server-Side Request Forgery (SSRF) protection**:

- ✅ Blocks requests to `localhost`, `127.0.0.1`, `0.0.0.0`, `::1`
- ✅ Blocks private IP ranges (10.x, 172.16-31.x, 192.168.x)
- ✅ Blocks link-local addresses (169.254.x.x)
- ✅ Blocks cloud metadata endpoints (`metadata.google.internal`, etc.)
- ✅ DNS-pinning: resolves hostname first, then validates the IP

This prevents the AI from accidentally accessing internal services or cloud metadata.

---

## Platforms

Runs on:
- macOS
- Linux
- Android (via Termux)
- Any system with Python 3.9+

---

## Legacy Bash Script (`ai.sh`)

The original bash-based version is still included for minimal environments.

### Features
- Multi-provider support (Gemini, OpenRouter, Groq, Together, Cerebras, Novita, Ollama)
- Dynamic model selection with filtering
- Conversation history + `/history` command
- Streaming responses
- Color-coded output
- Tool calling (Gemini)
- Session management (`/save`, `/load`, `/clear`)
- Image support (`/upload`, `/image`, `/clearimage`)
- Minimal dependencies: only `bash`, `curl`, `bc`, and `jq`

### Requirements
- bash
- curl
- bc
- jq

### Usage
```bash
chmod +x ai.sh

./ai.sh gemini
./ai.sh groq llama
./ai.sh openrouter claude
./ai.sh together
./ai.sh cerebras
./ai.sh novita
./ai.sh ollama
```

### When to Use Which Version

| Scenario | Recommendation |
|----------|----------------|
| Minimal environment (no Python) | `ai.sh` |
| Need Markdown/LaTeX rendering | `ai.py` |
| Need web search capability | `ai.py` |
| Need multi-line paste mode | `ai.py` |
| Need thinking/reasoning display | `ai.py` |
| Need live model switching | `ai.py` |
| Need SSRF protection | `ai.py` |
| Quick setup on servers | `ai.sh` |
| Android/Termux with limited storage | `ai.sh` |

### Bash Configuration
Edit the API key section at the top of `ai.sh`:
```bash
GEMINI_API_KEY=""
OPENROUTER_API_KEY=""
GROQ_API_KEY=""
TOGETHER_API_KEY=""
CEREBRAS_API_KEY=""
NOVITA_API_KEY=""
OLLAMA_API_KEY=""
```

---

> [!NOTE]
> **Out of scope:** Image generation is not supported as this is a terminal-based chat application.
