# consoleAI
A versatile Bash script that provides a command-line interface (CLI) for 
interacting with various Large Language Models (LLMs) from multiple AI providers. 
It supports model selection, conversation history, system prompts, and 
real-time streaming of responses.
Run this from any macos or linux even from android with turmux.

## Features

*   **Multi-Provider Support:** Chat with models from:
    *   Google Gemini
    *   OpenRouter (access to OpenAI, Anthropic, Mistral, etc.)
    *   Groq
    *   Together AI
    *   Fireworks AI
    *   Chutes AI
    *   Cerebras AI
*   **Dynamic Model Selection:** Fetches and lists available models from the chosen provider, allowing you to select one interactively.
*   **Conversation History:** Remembers the last `N` messages (configurable) to maintain context.
*   **System Prompt:** Define a system-level instruction for the AI (configurable).
*   **Streaming Responses:** AI responses are streamed token by token for a real-time feel.
*   **Color-Coded Output:** Differentiates between user input, AI responses, errors, and info messages.
*   **Minimal Dependencies:** Requires only `bash`, `curl`, and `jq`.
*   **Easy Configuration:** API keys and core settings are managed directly within the script.

## Setup & Configuration

1.  **Download the Script:**
    Clone the repository or download `ai.sh` to your local machine.
    ```bash
    git clone https://github.com/olumolu/consoleAI.git
    cd consoleAI
    ```
    Or just download the `ai.sh` file.

2.  **Make it Executable:**
    ```bash
    chmod +x ai.sh
    ```

3.  **IMPORTANT: Configure API Keys:**
    You **MUST** add your API keys to the script. Open `ai.sh` in a text editor and locate the API key section:

    ```bash
    GEMINI_API_KEY=""
    OPENROUTER_API_KEY=""
    GROQ_API_KEY=""
    TOGETHER_API_KEY=""
    FIREWORKS_API_KEY=""
    CHUTES_API_KEY=""
    CEREBRAS_API_KEY=""
    ```
    
4.  **(Optional) Adjust Default Settings:**
    You can customize other settings near the top of the script:
    *   `MAX_HISTORY_MESSAGES`: Number of past messages (user + AI) to keep in history.
    *   `DEFAULT_OAI_TEMPERATURE`, `DEFAULT_OAI_MAX_TOKENS`, `DEFAULT_OAI_TOP_P`: Parameters for OpenAI-compatible APIs.
    *   `SYSTEM_PROMPT`: The default system-level instruction for the AI. Set to `""` to disable.
