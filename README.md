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
*   **Tool Calling:** Tool Calling added into gemini with prompt to enable.

1.  **Download the Script:**
    Clone the repository or download `ai.sh` to your local machine.
    ```bash
    git clone https://github.com/olumolu/consoleAI.git
    ```
    ```bash
    cd consoleAI
    ```
    Or just download the `ai.sh` file.

> [!IMPORTANT]
> 2.    configure API Keys:**
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
    
    
3.  **Make it Executable:**
    ```bash
    chmod +x ai.sh
    ```


4.  **To Run This**

To start interacting with a specific AI provider, execute the script from your terminal followed by the provider's name. Here are the supported commands:

    ./ai.sh gemini
    ./ai.sh groq
    ./ai.sh chutes
    ./ai.sh fireworks
    ./ai.sh together
    ./ai.sh openrouter
    ./ai.sh cerebras

Choose any model from any provider just by selecting the Number mentioned before the model name from the list of available models.

> [!NOTE]    
> 5.  **(Optional) Adjust Default Settings:**
    You can customize other settings near the top of the script:
    *   `MAX_HISTORY_MESSAGES`: Number of past messages (user + AI) to keep in history.
    *   `DEFAULT_OAI_TEMPERATURE`, `DEFAULT_OAI_MAX_TOKENS`, `DEFAULT_OAI_TOP_P`: Parameters for OpenAI-compatible APIs.
    *   `SYSTEM_PROMPT`: The default system-level instruction for the AI. Set to `""` to disable.


> [!NOTE]
> ### Out of scope
>  *  Image genaration is out of scope as it is a terminal app.
>  *  Image upload for VL models is not yet implemented.
