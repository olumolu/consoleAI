#!/bin/bash
# Universal Chat CLI (Bash/curl/jq/bc) - With Model Selection, HISTORY, SYSTEM PROMPT, STREAMING, IMAGE SUPPORT, THINKING OUTPUT
# REQUIREMENTS: bash, curl, jq, bc, grep, sed, file, base64 (must be pre-installed on the system)
# Supports: Gemini, OpenRouter, Groq, Together AI, Cerebras AI, Novita AI, Ollama Cloud, NVIDIA NIM, Cloudflare AI
# To Run This Tool First Make It executable with $ chmod +x ai.sh
# Run This $ ./ai.sh provider
# filter support added [filter]... (e.g., ./ai.sh openrouter 32b or ./ai.sh gemini pro)
# History, system prompt, and streaming are supported.
# /history for show conversation log and <think... in a different colour for better visual experience.
# Session management commands: /save <name>, /load <name>, /clear

# Error handling:
# -E: inherit ERR traps in functions/subshells (where applicable)
# -o pipefail: fail a pipeline if any command fails (not just the last)
set -E -o pipefail

# --- Configuration ---
MAX_HISTORY_MESSAGES=20       # Keep the last N messages (user + ai). Adjust if needed.
MAX_MESSAGE_LENGTH=50000      # Maximum length for a single message
DEFAULT_OAI_TEMPERATURE=0.9   # t = randomness: Higher = more creative, Lower = more predictable | allowed value 0-2
DEFAULT_OAI_MAX_TOKENS=3000   # Default max_tokens for OpenAI-compatible APIs
DEFAULT_OAI_TOP_P=1.0         # p = diversity: Higher = wider vocabulary, Lower = safer word choices | allowed value 0-1
SESSION_DIR="${HOME}/.chat_sessions"    # Directory for storing chat session history files.
CURL_CONNECT_TIMEOUT=15       # FIX: Seconds to wait for initial connection
CURL_MAX_TIME=300             # FIX: Maximum total time for a request (5 minutes)

# --- Image Support Configuration ---
CURRENT_IMAGE_PATH=""
CURRENT_IMAGE_BASE64=""
CURRENT_IMAGE_MIME=""
MAX_IMAGE_SIZE_MB=20
SUPPORTED_IMAGE_TYPES=("image/jpeg" "image/png" "image/webp" "image/gif" "image/avif" "image/heic" "image/heif" "image/jxl" "image/tiff")

# --- Thinking Output Configuration ---
ENABLE_THINKING_OUTPUT=true   # Set to false to disable thinking output display

# --- Validate Configuration ---
validate_numeric() {
    local value="$1"
    local min="$2"
    local max="$3"
    local name="$4"

    if ! [[ "$value" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        echo "Error: $name must be a number, got: $value" >&2
        return 1
    fi

    if [[ "$(echo "$value < $min" | bc)" == "1" ]] || [[ "$(echo "$value > $max" | bc)" == "1" ]]; then
        echo "Error: $name must be between $min and $max, got: $value" >&2
        return 1
    fi
    return 0
}

# Validate configuration values
validate_numeric "$DEFAULT_OAI_TEMPERATURE" 0 2 "DEFAULT_OAI_TEMPERATURE" || exit 1
validate_numeric "$DEFAULT_OAI_TOP_P" 0 1 "DEFAULT_OAI_TOP_P" || exit 1
validate_numeric "$DEFAULT_OAI_MAX_TOKENS" 1 1000000 "DEFAULT_OAI_MAX_TOKENS" || exit 1

# --- System Prompt Definition ---
SYSTEM_PROMPT="You are a helpful assistant running in a command-line interface."

# --- Color Definitions --- Use 256-color
COLOR_RESET='\033[0m'
COLOR_USER='\033[38;5;199m'     # Bright Magenta
COLOR_AI='\033[38;5;40m'        # Bright green
COLOR_THINK='\033[38;5;214m'    # Soft orange
COLOR_ERROR='\033[38;5;203m'    # Vivid red
COLOR_WARN='\033[38;5;221m'     # Soft yellow
COLOR_INFO='\033[38;5;75m'      # Darker cyan-blue
COLOR_BOLD='\033[1m'
COLOR_IMAGE='\033[38;5;208m'    # Orange - for image attachments and indicators
COLOR_NVIDIA='\033[38;5;118m'   # NVIDIA green - for NVIDIA branding
COLOR_TOOL='\033[38;5;141m'     # Purple - for tool indicators

##########################################################################
#                    !!! EDIT YOUR API KEYS HERE !!!                     #
#                    !!!        IMPORTANT        !!!                     #
##########################################################################
# Get keys from:
# Gemini: https://aistudio.google.com/app/apikey
GEMINI_API_KEY=""

# OpenRouter: https://openrouter.ai/keys
OPENROUTER_API_KEY=""

# Groq: https://console.groq.com/keys
GROQ_API_KEY=""

# Together: https://api.together.ai/settings/api-keys
TOGETHER_API_KEY=""

# Cerebras: https://cloud.cerebras.ai/
CEREBRAS_API_KEY=""

# Novita: https://novita.ai/
NOVITA_API_KEY=""

# Ollama Cloud: https://ollama.com/
OLLAMA_API_KEY=""

# Cloudflare AI: https://developers.cloudflare.com/workers-ai/
CLOUDFLARE_API_TOKEN=""
CLOUDFLARE_ACCOUNT_ID=""

# NVIDIA NIM: https://build.nvidia.com/
NVIDIA_API_KEY=""

# --- API Endpoints ---
# Chat Endpoints
GEMINI_CHAT_URL_BASE="https://generativelanguage.googleapis.com/v1beta/models/"
OPENROUTER_CHAT_URL="https://openrouter.ai/api/v1/chat/completions"
GROQ_CHAT_URL="https://api.groq.com/openai/v1/chat/completions"
TOGETHER_CHAT_URL="https://api.together.ai/v1/chat/completions"
CEREBRAS_CHAT_URL="https://api.cerebras.ai/v1/chat/completions"
NOVITA_CHAT_URL="https://api.novita.ai/v3/openai/chat/completions"
OLLAMA_CHAT_URL="https://ollama.com/api/chat"
NVIDIA_CHAT_URL="https://integrate.api.nvidia.com/v1/chat/completions"
# Ollama to localhost. If using Ollama local, uncomment the next line:
# OLLAMA_CHAT_URL="http://localhost:11434/api/chat"

# Model Listing Endpoints
GEMINI_MODELS_URL_BASE="https://generativelanguage.googleapis.com/v1beta/models"
OPENROUTER_MODELS_URL="https://openrouter.ai/api/v1/models"
GROQ_MODELS_URL="https://api.groq.com/openai/v1/models"
TOGETHER_MODELS_URL="https://api.together.ai/v1/models"
CEREBRAS_MODELS_URL="https://api.cerebras.ai/v1/models"
NOVITA_MODELS_URL="https://api.novita.ai/v3/openai/models"
OLLAMA_MODELS_URL="https://ollama.com/api/tags"
NVIDIA_MODELS_URL="https://integrate.api.nvidia.com/v1/models"
# FIX: Removed top-level CLOUDFLARE_MODELS_URL (was using empty CLOUDFLARE_ACCOUNT_ID).
# It is now constructed dynamically after account ID is validated.
# Ollama to localhost. If using Ollama local, uncomment the next line:
# OLLAMA_MODELS_URL="http://localhost:11434/api/tags"

# --- Cleanup Trap ---
CURL_STDERR_TEMP=""
STREAM_FD=""
CURL_CONFIG_TEMP=""

cleanup() {
    # Close any open file descriptors
    if [[ -n "${STREAM_FD:-}" ]] && [[ -e /proc/$$/fd/$STREAM_FD 2>/dev/null ]]; then
        exec {STREAM_FD}<&- 2>/dev/null
    fi
    # Remove temporary files
    if [[ -n "${CURL_STDERR_TEMP:-}" && -f "$CURL_STDERR_TEMP" ]]; then
        rm -f "$CURL_STDERR_TEMP"
    fi
    # FIX: Remove curl config temp file (contains API key)
    if [[ -n "${CURL_CONFIG_TEMP:-}" && -f "$CURL_CONFIG_TEMP" ]]; then
        rm -f "$CURL_CONFIG_TEMP"
    fi
}

trap cleanup EXIT
trap 'echo -e "\n${COLOR_WARN}Interrupted. Cleaning up...${COLOR_RESET}"; cleanup; exit 130' INT TERM

# --- Helper Functions ---
function print_usage() {
  echo -e ""
  echo -e "${COLOR_INFO}Usage: $0 <provider> [filter]...${COLOR_RESET}"
  echo -e ""
  echo -e "${COLOR_INFO}Description:${COLOR_RESET}"
  echo -e "  🤖 Starts an interactive chat session with the specified AI provider,"
  echo -e "  maintaining conversation history, using a system prompt (if applicable),"
  echo -e "  and streaming responses token by token."
  echo -e "  It will fetch available models and let you choose one by number."
  echo -e "  Supports image/multimodal for vision-capable models!"
  echo -e "  Supports thinking output for reasoning models!"
  echo -e ""
  echo -e "${COLOR_INFO}Supported Providers:${COLOR_RESET}"
  echo -e "  gemini, openrouter, groq, together, cerebras, novita, ollama, cloudflare, ${COLOR_NVIDIA}nvidia${COLOR_RESET}"
  echo -e ""
  echo -e "${COLOR_INFO}Chat Commands:${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}/help${COLOR_RESET}            - Show available commands"
  echo -e "  ${COLOR_BOLD}/history${COLOR_RESET}         - Show conversation history"
  echo -e "  ${COLOR_BOLD}/save <name>${COLOR_RESET}     - Save current session to file"
  echo -e "  ${COLOR_BOLD}/load <name>${COLOR_RESET}     - Load saved session from file"
  echo -e "  ${COLOR_BOLD}/clear${COLOR_RESET}           - Clear all saved sessions"
  echo -e "  ${COLOR_BOLD}/upload <path>${COLOR_RESET}   - Attach image to next message"
  echo -e "  ${COLOR_BOLD}/image${COLOR_RESET}           - Show currently attached image"
  echo -e "  ${COLOR_BOLD}/clearimage${COLOR_RESET}      - Remove attached image"
  echo -e "  ${COLOR_BOLD}/togglethinking${COLOR_RESET}  - Toggle thinking output display"
  echo -e "  ${COLOR_BOLD}quit${COLOR_RESET} or ${COLOR_BOLD}exit${COLOR_RESET}   - Exit chat"
  echo -e ""
  echo -e "${COLOR_INFO}Finding Model Identifiers (if needed manually):${COLOR_RESET}"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Gemini:${COLOR_RESET}     https://ai.google.dev/models/gemini"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}OpenRouter:${COLOR_RESET} https://openrouter.ai/models"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Groq:${COLOR_RESET}       https://console.groq.com/docs/models"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Together:${COLOR_RESET}   https://docs.together.ai/docs/inference-models"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Cerebras:${COLOR_RESET}   https://cloud.cerebras.ai"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Novita:${COLOR_RESET}     https://docs.novita.ai"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Ollama:${COLOR_RESET}     https://ollama.com/library"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Cloudflare:${COLOR_RESET} https://developers.cloudflare.com/workers-ai/models"
  echo -e "    ${COLOR_BOLD}${COLOR_NVIDIA}NVIDIA NIM:${COLOR_RESET} https://build.nvidia.com/explore/discover"
  echo -e ""
  echo -e "${COLOR_BOLD}${COLOR_INFO}Example Commands:${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 gemini${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 groq llama${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 openrouter claude${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 together${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 cerebras${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 novita${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 ollama${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 cloudflare${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_NVIDIA}$0 nvidia${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_NVIDIA}$0 nvidia llama${COLOR_RESET}       # filter to llama models only"
  echo -e "  ${COLOR_BOLD}${COLOR_NVIDIA}$0 nvidia deepseek${COLOR_RESET}    # filter to deepseek models only"
  echo -e ""
  echo -e "${COLOR_IMAGE}Image Support:${COLOR_RESET}"
  echo -e "  Supports JPEG, PNG, GIF, WebP, AVIF, HEIC, TIFF, JXL. Max ${MAX_IMAGE_SIZE_MB}MB per image."
  echo -e "  Usage: ${COLOR_BOLD}/upload ~/photo.jpg${COLOR_RESET}, then type your question."
  echo -e ""
  echo -e "${COLOR_THINK}Thinking Output:${COLOR_RESET}"
  echo -e "  Displays reasoning/thinking content from supported models in orange."
  echo -e "  Toggle with ${COLOR_BOLD}/togglethinking${COLOR_RESET} during chat."
  echo -e ""
  echo -e "${COLOR_WARN}NOTE: Ensure API keys are set inside the script before running!${COLOR_RESET}"
}

# FIX: Added /help command content
function print_chat_help() {
  echo -e "${COLOR_INFO}── Available Commands ──────────────────────────────────${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}/help${COLOR_RESET}            - Show this help message"
  echo -e "  ${COLOR_BOLD}/history${COLOR_RESET}         - Show conversation history"
  echo -e "  ${COLOR_BOLD}/save <name>${COLOR_RESET}     - Save current session to ~/.chat_sessions/<name>.json"
  echo -e "  ${COLOR_BOLD}/load <name>${COLOR_RESET}     - Load a saved session"
  echo -e "  ${COLOR_BOLD}/clear${COLOR_RESET}           - Delete all saved sessions"
  echo -e "  ${COLOR_BOLD}/upload <path>${COLOR_RESET}   - Attach an image to your next message"
  echo -e "  ${COLOR_BOLD}/image${COLOR_RESET}           - Show currently attached image info"
  echo -e "  ${COLOR_BOLD}/clearimage${COLOR_RESET}      - Remove the attached image"
  echo -e "  ${COLOR_BOLD}/togglethinking${COLOR_RESET}  - Toggle reasoning/thinking output display"
  echo -e "  ${COLOR_BOLD}quit${COLOR_RESET} / ${COLOR_BOLD}exit${COLOR_RESET}   - End the session"
  echo -e "${COLOR_INFO}────────────────────────────────────────────────────────${COLOR_RESET}"
}

# Validate session name - only allow alphanumeric, dash, underscore
validate_session_name() {
    local name="$1"
    if [[ ! "$name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        echo -e "${COLOR_ERROR}Error: Session name can only contain letters, numbers, dash and underscore.${COLOR_RESET}" >&2
        return 1
    fi
    if [[ ${#name} -gt 100 ]]; then
        echo -e "${COLOR_ERROR}Error: Session name is too long (max 100 characters).${COLOR_RESET}" >&2
        return 1
    fi
    return 0
}

# Checks if API key looks like a placeholder
check_placeholder_key() {
    local key_value="$1"
    local provider_name="$2"
    local placeholder_found=false
    local message=""

    if [[ -z "$key_value" ]]; then
        placeholder_found=true
        message="is empty"
    elif [[ "$key_value" == "YOUR_"* ]] || [[ "$key_value" == *"-HERE" ]] || [[ "$key_value" == *"..." ]]; then
        placeholder_found=true
        message="appears to be a generic placeholder"
    elif [[ "$provider_name" == "gemini" && "$key_value" == "-" ]]; then
        placeholder_found=true
        message="is the default placeholder ('-')"
    elif [[ "$provider_name" == "openrouter" && "$key_value" == "sk-or-v1-" ]]; then
        placeholder_found=true
        message="is the default OpenRouter prefix placeholder"
    elif [[ "$provider_name" == "groq" && "$key_value" == "gsk_"* && ${#key_value} -lt 10 ]]; then
        placeholder_found=true
        message="appears to be an incomplete Groq key (starts with gsk_ but is too short)"
    elif [[ "$provider_name" == "cerebras" && "$key_value" == "csk-" ]]; then
        placeholder_found=true
        message="is the default Cerebras prefix placeholder ('csk-')"
    elif [[ "$provider_name" == "novita" && ${#key_value} -lt 10 ]]; then
        placeholder_found=true
        message="appears to be too short to be a valid key"
    elif [[ "$provider_name" == "ollama" && ${#key_value} -lt 10 ]]; then
        placeholder_found=true
        message="appears to be too short to be a valid key"
    elif [[ "$provider_name" == "cloudflare" && ${#key_value} -lt 10 ]]; then
        placeholder_found=true
        message="appears to be too short to be a valid key"
    elif [[ "$provider_name" == "nvidia" && ${#key_value} -lt 10 ]]; then
        placeholder_found=true
        message="appears to be too short to be a valid key"
    fi

    if [[ "$placeholder_found" == true ]]; then
        echo -e "${COLOR_WARN}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${COLOR_RESET}" >&2
        echo -e "${COLOR_WARN}!! WARNING: API Key for provider '${provider_name^^}' $message.${COLOR_RESET}" >&2
        echo -e "${COLOR_WARN}!! Please edit the script ($0) and replace it with your actual key.${COLOR_RESET}" >&2
        echo -e "${COLOR_WARN}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${COLOR_RESET}" >&2
        return 1
    fi
    return 0
}

# Truncates a string to a max length, adding ellipsis
truncate() {
    local s="$1"
    local max_chars="$2"
    if [[ ${#s} -gt $max_chars ]]; then
        echo "${s:0:$((max_chars-3))}..."
    else
        echo "$s"
    fi
}

# Remove think tags from text
strip_think_tags() {
    local text="$1"
    local result=""
    local remaining="$text"

    while [[ -n "$remaining" ]]; do
        if [[ "$remaining" == *"<think"* ]]; then
            result+="${remaining%%<think*}"
            remaining="${remaining#*<think}"
            if [[ "$remaining" == *">"* ]]; then
                remaining="${remaining#*>}"
            fi
            if [[ "$remaining" == *"</think"* ]]; then
                remaining="${remaining#*</think}"
                if [[ "$remaining" == *">"* ]]; then
                    remaining="${remaining#*>}"
                fi
            else
                break
            fi
        else
            result+="$remaining"
            break
        fi
    done
    echo "$result"
}

# Escape ERE (grep -E) metacharacters in user-provided filters
regex_escape_ere() {
    printf '%s' "$1" | sed -e 's/[][(){}.^$*+?|\\]/\\&/g'
}

# --- Image Helper Functions ---
validate_image_file() {
    local file_path="$1"
    file_path="${file_path//\'/}"
    file_path="${file_path//\"/}"

    if [[ ! -f "$file_path" ]]; then
        echo -e "${COLOR_ERROR}Error: File not found: $file_path${COLOR_RESET}" >&2
        return 1
    fi

    local file_size_bytes
    file_size_bytes=$(stat -c%s "$file_path" 2>/dev/null || stat -f%z "$file_path" 2>/dev/null)
    local max_size_bytes=$((MAX_IMAGE_SIZE_MB * 1024 * 1024))

    if [[ $file_size_bytes -gt $max_size_bytes ]]; then
        echo -e "${COLOR_ERROR}Error: Image too large ($(($file_size_bytes / 1024 / 1024))MB). Max: ${MAX_IMAGE_SIZE_MB}MB${COLOR_RESET}" >&2
        return 1
    fi

    local mime_type
    mime_type=$(file -b --mime-type "$file_path" 2>/dev/null || file -I "$file_path" 2>/dev/null | cut -d';' -f1)

    local is_valid=false
    for valid_type in "${SUPPORTED_IMAGE_TYPES[@]}"; do
        if [[ "$mime_type" == "$valid_type" ]]; then
            is_valid=true
            break
        fi
    done

    if [[ "$is_valid" == false ]]; then
        echo -e "${COLOR_ERROR}Error: Unsupported image type: $mime_type${COLOR_RESET}" >&2
        return 1
    fi

    echo "$file_path|$mime_type"
    return 0
}

# FIX: Handle macOS base64 which wraps at 76 chars (adds newlines)
encode_image_to_base64() {
    local file_path="$1"
    file_path="${file_path//\'/}"
    file_path="${file_path//\"/}"
    # Try GNU base64 first (-w 0 = no wrapping), fall back to stripping newlines
    if base64 -w 0 "$file_path" 2>/dev/null; then
        return 0
    fi
    # macOS/BSD: base64 wraps at 76 chars; strip all newlines
    base64 "$file_path" 2>/dev/null | tr -d '\n'
}

clear_current_image() {
    CURRENT_IMAGE_PATH=""
    CURRENT_IMAGE_BASE64=""
    CURRENT_IMAGE_MIME=""
}

# FIX: Create a secure curl config file to avoid exposing API keys in `ps` output
create_curl_config() {
    local auth_header="$1"
    CURL_CONFIG_TEMP=$(mktemp)
    chmod 600 "$CURL_CONFIG_TEMP"
    if [[ -n "$auth_header" ]]; then
        echo "header = \"$auth_header\"" > "$CURL_CONFIG_TEMP"
    fi
    echo "$CURL_CONFIG_TEMP"
}

# --- Argument Parsing ---
if [[ "$#" -lt 1 ]]; then
    echo -e "${COLOR_ERROR}Error: Invalid number of arguments.${COLOR_RESET}" >&2
    print_usage
    exit 1
fi

PROVIDER=$(echo "$1" | tr '[:upper:]' '[:lower:]')
filters=("${@:2}")

# --- Dependency Check ---
required_commands=("curl" "jq" "bc" "grep" "sed" "file" "base64")
missing_commands=()
for cmd in "${required_commands[@]}"; do
    if ! command -v "$cmd" &> /dev/null; then
        missing_commands+=("$cmd")
    fi
done
if [[ ${#missing_commands[@]} -ne 0 ]]; then
    echo -e "${COLOR_ERROR}Error: Required command(s) not found: ${missing_commands[*]}. Please install them.${COLOR_RESET}" >&2
    exit 1
fi

# --- Get API Key and Check Placeholders ---
API_KEY=""
key_check_status=0
case "$PROVIDER" in
    gemini)     API_KEY="$GEMINI_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$? ;;
    openrouter) API_KEY="$OPENROUTER_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$? ;;
    groq)       API_KEY="$GROQ_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$? ;;
    together)   API_KEY="$TOGETHER_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$? ;;
    cerebras)   API_KEY="$CEREBRAS_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$? ;;
    novita)     API_KEY="$NOVITA_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$? ;;
    ollama)     API_KEY="$OLLAMA_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$? ;;
    nvidia)     API_KEY="$NVIDIA_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$? ;;
    cloudflare)
        if [[ -z "$CLOUDFLARE_API_TOKEN" ]]; then
            echo -e "${COLOR_WARN}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${COLOR_RESET}" >&2
            echo -e "${COLOR_WARN}!! WARNING: API Token for provider 'CLOUDFLARE' is empty.${COLOR_RESET}" >&2
            echo -e "${COLOR_WARN}!! Please edit the script ($0) and replace CLOUDFLARE_API_TOKEN with your actual token.${COLOR_RESET}" >&2
            echo -e "${COLOR_WARN}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${COLOR_RESET}" >&2
            key_check_status=1
        elif [[ -z "$CLOUDFLARE_ACCOUNT_ID" ]]; then
            echo -e "${COLOR_WARN}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${COLOR_RESET}" >&2
            echo -e "${COLOR_WARN}!! WARNING: Account ID for provider 'CLOUDFLARE' is empty.${COLOR_RESET}" >&2
            echo -e "${COLOR_WARN}!! Please edit the script ($0) and replace CLOUDFLARE_ACCOUNT_ID with your actual account ID.${COLOR_RESET}" >&2
            echo -e "${COLOR_WARN}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${COLOR_RESET}" >&2
            key_check_status=1
        else
            API_KEY="$CLOUDFLARE_API_TOKEN"
            check_placeholder_key "$API_KEY" "$PROVIDER"; key_check_status=$?
        fi
        ;;
    *)
        echo -e "${COLOR_ERROR}Error: Unknown provider '$PROVIDER'. Choose from: gemini, openrouter, groq, together, cerebras, novita, ollama, cloudflare, nvidia${COLOR_RESET}" >&2
        print_usage
        exit 1
        ;;
esac

if [[ "$key_check_status" -ne 0 ]]; then
    echo -e "${COLOR_INFO}Exiting due to placeholder API key. Please edit the script ($0) and add your actual key for '$PROVIDER'.${COLOR_RESET}" >&2
    exit 1
fi

# --- Fetch and Select Model ---
echo -e "${COLOR_INFO}Fetching available models for ${PROVIDER^^}...${COLOR_RESET}"
MODELS_URL=""
JQ_QUERY=""
MODELS_AUTH_HEADER=""
MODELS_EXTRA_HEADERS=()

case "$PROVIDER" in
    gemini)
        MODELS_URL="${GEMINI_MODELS_URL_BASE}?key=${API_KEY}"
        JQ_QUERY='.models[] | select(.supportedGenerationMethods[]? | contains("generateContent")) | .name | sub("models/";"") | select(length>0)'
        ;;
    openrouter)
        MODELS_URL="$OPENROUTER_MODELS_URL"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        MODELS_EXTRA_HEADERS+=("-H" "HTTP-Referer: urn:chatcli:bash")
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    groq)
        MODELS_URL="$GROQ_MODELS_URL"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    together)
        MODELS_URL="$TOGETHER_MODELS_URL"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='. | sort_by(.id) | .[].id'
        ;;
    cerebras)
        MODELS_URL="$CEREBRAS_MODELS_URL"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    novita)
        MODELS_URL="$NOVITA_MODELS_URL"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    ollama)
        MODELS_URL="$OLLAMA_MODELS_URL"
        if [[ -n "$API_KEY" ]]; then
            MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        fi
        JQ_QUERY='.models[] | .name'
        ;;
    nvidia)
        MODELS_URL="$NVIDIA_MODELS_URL"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    cloudflare)
        # FIX: Construct URL dynamically now that CLOUDFLARE_ACCOUNT_ID is validated
        MODELS_URL="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/models"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='.result | sort_by(.id) | .[].id'
        ;;
esac

# FIX: Use curl config file to hide API key from ps output
model_curl_config=""
model_curl_args=(-sS -L --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time 30 -X GET "$MODELS_URL")
if [[ -n "$MODELS_AUTH_HEADER" ]]; then
    model_curl_config=$(create_curl_config "$MODELS_AUTH_HEADER")
    model_curl_args+=(--config "$model_curl_config")
fi
if [[ ${#MODELS_EXTRA_HEADERS[@]} -gt 0 ]]; then
    model_curl_args+=("${MODELS_EXTRA_HEADERS[@]}")
fi

model_list_json=""
if ! model_list_json=$(curl "${model_curl_args[@]}"); then
    model_list_exit_code=$?
    echo -e "${COLOR_ERROR}Error fetching models: curl command failed (Exit code: $model_list_exit_code).${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}Check network connection, API key validity/permissions, and endpoint ($MODELS_URL).${COLOR_RESET}" >&2
    rm -f "$model_curl_config" 2>/dev/null
    exit 1
fi
rm -f "$model_curl_config" 2>/dev/null
CURL_CONFIG_TEMP=""

if ! echo "$model_list_json" | jq empty 2>/dev/null; then
    echo -e "${COLOR_ERROR}Error: API response for model list was not valid JSON.${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}Raw response (first 200 chars): $(truncate "$model_list_json" 200)${COLOR_RESET}" >&2
    exit 1
fi

api_fetch_error=$(echo "$model_list_json" | jq -r 'if type=="object" then .error.message // .error.code // .message // .detail // .error // empty else empty end')
if [[ -n "$api_fetch_error" && "$api_fetch_error" != "null" ]]; then
     echo -e "${COLOR_ERROR}API Error during model fetch: $api_fetch_error${COLOR_RESET}" >&2
     echo -e "${COLOR_INFO}Check API key permissions and validity for provider '${PROVIDER^^}'.${COLOR_RESET}" >&2
     exit 1
fi

jq_stderr_output=""
jq_err_file=$(mktemp)
mapfile -t available_models < <(jq -r "$JQ_QUERY" <<< "$model_list_json" 2>"$jq_err_file")
jq_exit_code=$?
jq_stderr_output=$(cat "$jq_err_file" 2>/dev/null || true)
rm -f "$jq_err_file"

if [[ $jq_exit_code -ne 0 ]] || [[ ${#available_models[@]} -eq 0 ]]; then
    echo -e "${COLOR_ERROR}Error: No models found or failed to parse API response for provider '$PROVIDER'.${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}JQ Exit Code: ${jq_exit_code}${COLOR_RESET}" >&2
    if [[ -n "$jq_stderr_output" ]]; then
      echo -e "${COLOR_ERROR}JQ Error:${COLOR_RESET} $jq_stderr_output" >&2
    fi
    echo -e "${COLOR_INFO}Raw API response (first 500 chars):${COLOR_RESET}" >&2
    echo "${model_list_json:0:500}" >&2
    exit 1
fi

# --- Filter models based on additional arguments ---
if [[ ${#filters[@]} -gt 0 ]]; then
    echo -e "${COLOR_INFO}Filtering models with terms: ${filters[*]}${COLOR_RESET}"
    declare -a filtered_models=()
    declare -a filters_lower=()
    for filter in "${filters[@]}"; do
        filters_lower+=("$(echo "$filter" | tr '[:upper:]' '[:lower:]')")
    done

    for model in "${available_models[@]}"; do
        is_match=true
        model_lower=$(echo "$model" | tr '[:upper:]' '[:lower:]')
        for filter_lower in "${filters_lower[@]}"; do
            esc_filter=$(regex_escape_ere "$filter_lower")
            if ! echo "$model_lower" | grep -E "(^|[^[:alnum:]])${esc_filter}([^[:alnum:]]|$)" >/dev/null 2>&1; then
                is_match=false
                break
            fi
        done
        if [[ "$is_match" == true ]]; then
            filtered_models+=("$model")
        fi
    done
    available_models=("${filtered_models[@]}")
fi

if [[ ${#available_models[@]} -eq 0 ]]; then
    echo -e "${COLOR_ERROR}No models available.${COLOR_RESET}" >&2
    if [[ ${#filters[@]} -gt 0 ]]; then
        echo -e "${COLOR_WARN}Your filter criteria (${filters[*]}) did not match any models from provider '${PROVIDER^^}'.${COLOR_RESET}" >&2
    fi
    exit 1
fi

MODEL_ID=""

# --- Auto-select if only one model, otherwise prompt user ---
if [[ ${#available_models[@]} -eq 1 ]]; then
    MODEL_ID="${available_models[0]}"
    echo -e "${COLOR_INFO}Auto-selecting only matching model.${COLOR_RESET}"
else
    echo -e "${COLOR_INFO}Available Models for ${PROVIDER^^}:${COLOR_RESET}"
    for i in "${!available_models[@]}"; do
        printf "  ${COLOR_BOLD}%3d${COLOR_RESET}. %s\n" $((i+1)) "${available_models[$i]}"
    done
    echo ""
    while true; do
        read -r -p "$(echo -e "${COLOR_INFO}Select model by number: ${COLOR_RESET}")" choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && [[ "$choice" -ge 1 ]] && [[ "$choice" -le ${#available_models[@]} ]]; then
            MODEL_ID="${available_models[$((choice-1))]}"
            break
        else
            echo -e "${COLOR_WARN}Invalid selection. Enter number between 1 and ${#available_models[@]}.${COLOR_RESET}" >&2
        fi
    done
fi

echo -e "${COLOR_INFO}Using model:${COLOR_RESET} ${MODEL_ID}"
echo ""

CHAT_API_URL=""
CHAT_AUTH_HEADER=""
CHAT_EXTRA_HEADERS=()
IS_OPENAI_COMPATIBLE=false
ENABLE_TOOL_CALLING=false
PROVIDER_SUPPORTS_THINKING=false

# --- Interactive prompt for tool calling for Gemini ---
if [[ "$PROVIDER" == "gemini" ]]; then
    echo ""
    tool_choice_input=""
    while true; do
        read -r -p "$(echo -e "${COLOR_INFO}Enable online tool calling (web search, URL context) for Gemini? (y/n): ${COLOR_RESET}")" tool_choice_input
        tool_choice_input=$(echo "$tool_choice_input" | tr '[:upper:]' '[:lower:]')
        if [[ "$tool_choice_input" == "y" || "$tool_choice_input" == "1" ]]; then
            ENABLE_TOOL_CALLING=true
            echo -e "${COLOR_INFO}Tool calling enabled.${COLOR_RESET}"
            break
        elif [[ "$tool_choice_input" == "n" || "$tool_choice_input" == "0" ]]; then
            ENABLE_TOOL_CALLING=false
            echo -e "${COLOR_INFO}Tool calling disabled.${COLOR_RESET}"
            break
        else
            echo -e "${COLOR_WARN}Invalid input. Please enter 'y' or 'n'.${COLOR_RESET}" >&2
        fi
    done
    echo ""
fi

# FIX: Added cloudflare to the outer case pattern (was missing = dead code bug)
case "$PROVIDER" in
    gemini)
        CHAT_API_URL="${GEMINI_CHAT_URL_BASE}${MODEL_ID}:streamGenerateContent?key=${API_KEY}&alt=sse"
        IS_OPENAI_COMPATIBLE=false
        ;;
    openrouter|groq|together|cerebras|novita|ollama|nvidia|cloudflare)
        CHAT_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        IS_OPENAI_COMPATIBLE=true
        case "$PROVIDER" in
            openrouter)
                CHAT_API_URL="$OPENROUTER_CHAT_URL"
                CHAT_EXTRA_HEADERS+=("-H" "HTTP-Referer: urn:chatcli:bash")
                CHAT_EXTRA_HEADERS+=("-H" "X-Title: BashChatCLI")
                ;;
            groq)       CHAT_API_URL="$GROQ_CHAT_URL" ;;
            together)   CHAT_API_URL="$TOGETHER_CHAT_URL" ;;
            cerebras)   CHAT_API_URL="$CEREBRAS_CHAT_URL" ;;
            novita)     CHAT_API_URL="$NOVITA_CHAT_URL" ;;
            nvidia)
                CHAT_API_URL="$NVIDIA_CHAT_URL"
                PROVIDER_SUPPORTS_THINKING=true
                ;;
            ollama)
                CHAT_API_URL="$OLLAMA_CHAT_URL"
                PROVIDER_SUPPORTS_THINKING=true
                ;;
            cloudflare)
                # FIX: Now reachable — construct URL with account ID and model
                CHAT_API_URL="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/run/${MODEL_ID}"
                ;;
        esac
        ;;
esac

declare -a chat_history=()

initialize_history() {
    chat_history=()
    if [[ -n "$SYSTEM_PROMPT" ]]; then
        if [[ "$IS_OPENAI_COMPATIBLE" == true ]]; then
            system_message_json=$(jq -n --arg content "$SYSTEM_PROMPT" '{role: "system", content: $content}')
            if [[ -n "$system_message_json" ]]; then
                chat_history+=("$system_message_json")
            fi
        fi
    fi
}

validate_session() {
    local session_file="$1"
    if ! jq -e 'type == "array"' "$session_file" >/dev/null 2>&1; then
        echo -e "${COLOR_ERROR}Error: Session file is not a valid JSON array.${COLOR_RESET}" >&2
        return 1
    fi
    local validation_result
    validation_result=$(
        jq -r '
          map(
            if type != "object" then "Item is not an object"
            elif .role == null then "Missing role field"
            elif .role == "system" then
              if (.content? | type) != "string" then "System message missing string .content" else null end
            elif (.role == "user" or .role == "assistant" or .role == "model") then
              if ((.content? | type) == "string") then null
              elif (.parts? | type) == "array" then null
              else "Message missing .content or .parts"
              end
            else "Unknown role"
            end
          )
          | map(select(. != null))
          | if length > 0 then .[0] else null end
        ' "$session_file"
    )
    if [[ -n "$validation_result" && "$validation_result" != "null" ]]; then
        echo -e "${COLOR_ERROR}Error: Invalid session format - $validation_result${COLOR_RESET}" >&2
        return 1
    fi
    return 0
}

initialize_history

# --- Banner ---
echo -e "┌─────────────────────────────────────────────────────────────────────┐"
echo -e "│ ${COLOR_BOLD}AI Chat CLI (Bash)${COLOR_RESET}                                                  │"
echo -e "├─────────────────────────────────────────────────────────────────────┤"
echo -e "│ ${COLOR_INFO}Provider:${COLOR_RESET}      ${PROVIDER^^}"
echo -e "│ ${COLOR_INFO}Model:${COLOR_RESET}         ${MODEL_ID}"
echo -e "│ ${COLOR_INFO}History Limit:${COLOR_RESET} Last $MAX_HISTORY_MESSAGES messages"
echo -e "│ ${COLOR_INFO}Message Limit:${COLOR_RESET} $MAX_MESSAGE_LENGTH characters"
echo -e "│ ${COLOR_INFO}Temp/Tokens:${COLOR_RESET}   $DEFAULT_OAI_TEMPERATURE / $DEFAULT_OAI_MAX_TOKENS"

if [[ -n "$SYSTEM_PROMPT" ]]; then
    if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
        echo -e "│ ${COLOR_INFO}System Prompt:${COLOR_RESET} Set (prepended to first user message for Gemini)"
    else
        echo -e "│ ${COLOR_INFO}System Prompt:${COLOR_RESET} Active"
    fi
else
    echo -e "│ ${COLOR_INFO}System Prompt:${COLOR_RESET} Inactive"
fi

if [[ "$PROVIDER" == "gemini" ]]; then
    if [[ "$ENABLE_TOOL_CALLING" == true ]]; then
        echo -e "│ ${COLOR_INFO}Tool Calling:${COLOR_RESET}  ${COLOR_BOLD}Enabled${COLOR_RESET}"
    else
        echo -e "│ ${COLOR_INFO}Tool Calling:${COLOR_RESET}  Disabled"
    fi
fi

if [[ "$ENABLE_THINKING_OUTPUT" == true ]]; then
    echo -e "│ ${COLOR_INFO}Thinking:${COLOR_RESET}      ${COLOR_BOLD}${COLOR_THINK}Enabled${COLOR_RESET} ${COLOR_DIM}(/togglethinking)${COLOR_RESET}"
else
    echo -e "│ ${COLOR_INFO}Thinking:${COLOR_RESET}      Disabled ${COLOR_DIM}(/togglethinking)${COLOR_RESET}"
fi

echo -e "├─────────────────────────────────────────────────────────────────────┤"
echo -e "│ Type ${COLOR_BOLD}quit${COLOR_RESET} to exit • ${COLOR_BOLD}/help${COLOR_RESET} for commands                              │"
echo -e "└─────────────────────────────────────────────────────────────────────┘"
echo ""

first_user_message=true

while true; do
    # Build prompt with image indicator
    prompt_prefix=""
    if [[ -n "$CURRENT_IMAGE_PATH" ]]; then
        prompt_prefix="[${COLOR_IMAGE}📎 $(basename "$CURRENT_IMAGE_PATH")${COLOR_RESET}] "
    fi

    if [[ -t 0 ]]; then
         read -r -e -p "$(echo -e "${prompt_prefix}${COLOR_BOLD}${COLOR_USER}You:${COLOR_RESET} ")" user_input
         [[ -n "${user_input:-}" ]] && history -s "$user_input" 2>/dev/null || true
    else
         read -r -p "$(echo -e "${prompt_prefix}${COLOR_BOLD}${COLOR_USER}You:${COLOR_RESET} ")" user_input
    fi

    if [[ "${user_input:-}" == "quit" || "${user_input:-}" == "exit" ]]; then
        echo "Exiting chat."
        break
    fi

    ### --- Command Handling --- ###
    if [[ "${user_input:-}" == /* ]]; then
        read -r cmd args <<< "$user_input"
        case "$cmd" in
            # FIX: Added /help command
            "/help")
                print_chat_help
                continue
                ;;
            "/upload")
                if [[ -z "${args:-}" ]]; then
                    echo -e "${COLOR_IMAGE}Usage: /upload <image_path>${COLOR_RESET}" >&2
                    continue
                fi
                args="${args//\'/}"
                args="${args//\"/}"
                echo -e "${COLOR_IMAGE}Validating image...${COLOR_RESET}" >&2
                validation_result=$(validate_image_file "$args")
                if [[ $? -ne 0 ]]; then
                    continue
                fi
                file_path=$(echo "$validation_result" | cut -d'|' -f1)
                mime_type=$(echo "$validation_result" | cut -d'|' -f2)
                echo -e "${COLOR_IMAGE}Encoding image...${COLOR_RESET}" >&2
                base64_data=$(encode_image_to_base64 "$file_path")
                if [[ $? -ne 0 ]] || [[ -z "$base64_data" ]]; then
                    echo -e "${COLOR_ERROR}Error: Failed to encode image${COLOR_RESET}" >&2
                    continue
                fi
                CURRENT_IMAGE_PATH="$file_path"
                CURRENT_IMAGE_BASE64="$base64_data"
                CURRENT_IMAGE_MIME="$mime_type"
                file_size_kb=$(($(stat -c%s "$file_path" 2>/dev/null || stat -f%z "$file_path" 2>/dev/null) / 1024))
                echo -e "${COLOR_IMAGE}✓ Attached: $(basename "$file_path") (${mime_type}, ${file_size_kb}KB)${COLOR_RESET}" >&2
                continue
                ;;
            "/image")
                if [[ -n "$CURRENT_IMAGE_PATH" ]]; then
                    echo -e "${COLOR_IMAGE}Current image: $(basename "$CURRENT_IMAGE_PATH") (${CURRENT_IMAGE_MIME})${COLOR_RESET}" >&2
                else
                    echo -e "${COLOR_IMAGE}No image attached.${COLOR_RESET}" >&2
                fi
                continue
                ;;
            "/clearimage")
                clear_current_image
                echo -e "${COLOR_IMAGE}Image cleared.${COLOR_RESET}" >&2
                continue
                ;;
            "/togglethinking")
                if [[ "$ENABLE_THINKING_OUTPUT" == true ]]; then
                    ENABLE_THINKING_OUTPUT=false
                    echo -e "${COLOR_INFO}Thinking output ${COLOR_BOLD}disabled${COLOR_RESET}." >&2
                else
                    ENABLE_THINKING_OUTPUT=true
                    echo -e "${COLOR_INFO}Thinking output ${COLOR_BOLD}${COLOR_THINK}enabled${COLOR_RESET}." >&2
                fi
                continue
                ;;
            "/history")
                echo -e "${COLOR_INFO}── History (${#chat_history[@]} messages) ─────────────────────${COLOR_RESET}"
                if [[ ${#chat_history[@]} -eq 0 ]]; then
                    echo "  (empty)" >&2
                else
                    printf '%s\n' "${chat_history[@]}" | jq -s -c '.[]' | while IFS= read -r msg; do
                        role=$(echo "$msg" | jq -r '.role')
                        content=$(echo "$msg" | jq -r '.content // .parts[0].text // "[content]"')
                        if [[ "$role" == "user" ]]; then
                            echo -e "  ${COLOR_USER}[$role]${COLOR_RESET} $(truncate "$content" 500)"
                        elif [[ "$role" == "assistant" || "$role" == "model" ]]; then
                            echo -e "  ${COLOR_AI}[$role]${COLOR_RESET} $(truncate "$content" 500)"
                        else
                            echo -e "  ${COLOR_WARN}[$role]${COLOR_RESET} $(truncate "$content" 500)"
                        fi
                    done >&2
                fi
                echo -e "${COLOR_INFO}────────────────────────────────────────────────────${COLOR_RESET}"
                continue
                ;;
            "/save")
                if [[ -z "${args:-}" ]]; then
                    echo -e "${COLOR_WARN}Usage: /save <session_name>${COLOR_RESET}" >&2
                    continue
                fi
                if ! validate_session_name "$args"; then
                    continue
                fi
                # FIX: Create session directory with restricted permissions
                mkdir -p "$SESSION_DIR"
                chmod 700 "$SESSION_DIR"
                session_file="${SESSION_DIR}/${args}.json"
                printf '%s\n' "${chat_history[@]}" | jq -s . > "$session_file"
                # FIX: Restrict session file permissions
                chmod 600 "$session_file"
                echo -e "${COLOR_INFO}Session saved to: $session_file${COLOR_RESET}"
                continue
                ;;
            "/load")
                if [[ -z "${args:-}" ]]; then
                    echo -e "${COLOR_WARN}Usage: /load <session_name>${COLOR_RESET}" >&2
                    continue
                fi
                if ! validate_session_name "$args"; then
                    continue
                fi
                mkdir -p "$SESSION_DIR"
                session_file="${SESSION_DIR}/${args}.json"
                if [[ ! -f "$session_file" ]]; then
                    echo -e "${COLOR_ERROR}Error: Session file not found: $session_file${COLOR_RESET}" >&2
                    continue
                fi
                if ! validate_session "$session_file"; then
                    echo -e "${COLOR_ERROR}Session file is corrupted or invalid. Cannot load.${COLOR_RESET}" >&2
                    continue
                fi
                mapfile -t chat_history < <(jq -c '.[]' "$session_file")
                first_user_message=false
                echo -e "${COLOR_INFO}Session loaded from: $session_file (${#chat_history[@]} messages)${COLOR_RESET}"
                continue
                ;;
            "/clear")
                if [[ ! -d "$SESSION_DIR" ]] || [[ -z "$(ls -A "$SESSION_DIR"/*.json 2>/dev/null)" ]]; then
                    echo -e "${COLOR_INFO}No saved sessions to clear.${COLOR_RESET}" >&2
                    continue
                fi
                echo -e "${COLOR_WARN}This will permanently delete all saved sessions in ${SESSION_DIR}:${COLOR_RESET}" >&2
                ls -1 "${SESSION_DIR}"/*.json 2>/dev/null | xargs -n1 basename | sed 's/.json$//' >&2
                read -r -p "$(echo -e "${COLOR_WARN}Are you sure? (y/N): ${COLOR_RESET}")" confirm
                if [[ "$confirm" =~ ^[Yy]$ ]]; then
                    find "$SESSION_DIR" -maxdepth 1 -type f -name "*.json" -delete
                    echo -e "${COLOR_INFO}All saved sessions cleared.${COLOR_RESET}"
                else
                    echo -e "${COLOR_INFO}Cancelled.${COLOR_RESET}"
                fi
                continue
                ;;
            # FIX: Catch-all for unknown commands (prevents sending "/typo" to the AI)
            *)
                echo -e "${COLOR_WARN}Unknown command '$cmd'. Type /help for available commands.${COLOR_RESET}" >&2
                continue
                ;;
        esac
    fi

    if [[ -z "${user_input:-}" && -z "$CURRENT_IMAGE_BASE64" ]]; then
        continue
    fi

    if [[ -z "${user_input:-}" && -n "$CURRENT_IMAGE_BASE64" ]]; then
        user_input="Describe this image in detail."
    fi

    if [[ ${#user_input} -gt $MAX_MESSAGE_LENGTH ]]; then
        echo -e "${COLOR_ERROR}Error: Message too long (${#user_input} chars). Max: $MAX_MESSAGE_LENGTH${COLOR_RESET}" >&2
        continue
    fi

    echo -e "${COLOR_INFO}[Sending...]${COLOR_RESET}" >&2

    user_prompt_text="$user_input"
    user_message_json=""

    # --- Construct message with image support ---
    if [[ -n "$CURRENT_IMAGE_BASE64" ]]; then
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            if [[ "$first_user_message" == true && -n "$SYSTEM_PROMPT" ]]; then
                user_prompt_text="${SYSTEM_PROMPT}\n\n${user_input}"
            fi
            user_message_json=$(jq -n \
                --arg text "$user_prompt_text" \
                --arg mime "$CURRENT_IMAGE_MIME" \
                --arg data "$CURRENT_IMAGE_BASE64" \
                '{role: "user", parts:[{text: $text}, {inlineData: {mimeType: $mime, data: $data}}]}'
            )
        elif [[ "$PROVIDER" == "ollama" ]]; then
            user_message_json=$(jq -n \
                --arg content "$user_prompt_text" \
                --arg image_data "$CURRENT_IMAGE_BASE64" \
                '{role: "user", content: $content, images: [$image_data]}'
            )
        else
            user_message_json=$(jq -n \
                --arg text "$user_input" \
                --arg mime "$CURRENT_IMAGE_MIME" \
                --arg data "$CURRENT_IMAGE_BASE64" \
                '{role: "user", content:[{type: "text", text: $text}, {type: "image_url", image_url: {url: ("data:" + $mime + ";base64," + $data)}}]}'
            )
        fi
        clear_current_image
    else
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            if [[ "$first_user_message" == true && -n "$SYSTEM_PROMPT" ]]; then
                user_prompt_text="${SYSTEM_PROMPT}\n\nUser: ${user_input}"
            fi
            user_message_json=$(jq -n --arg text "$user_prompt_text" '{role: "user", parts:[{text: $text}]}')
        else
            user_message_json=$(jq -n --arg content "$user_prompt_text" '{role: "user", content: $content}')
        fi
    fi

    first_user_message=false

    if [[ -z "$user_message_json" ]]; then
        echo -e "${COLOR_ERROR}Error: Failed to create user message JSON. Skipping.${COLOR_RESET}" >&2
        continue
    fi
    chat_history+=("$user_message_json")

    # History Truncation
    current_history_size=${#chat_history[@]}
    system_offset=0
    if [[ "$IS_OPENAI_COMPATIBLE" == true && ${#chat_history[@]} -gt 0 && "$(echo "${chat_history[0]}" | jq -r .role 2>/dev/null)" == "system" ]]; then
         system_offset=1
    fi
    effective_max_history_entries=$(( MAX_HISTORY_MESSAGES + system_offset ))

    if [[ $current_history_size -gt $effective_max_history_entries ]]; then
        elements_to_remove=$((current_history_size - effective_max_history_entries))
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            if (( elements_to_remove % 2 == 1 )); then
                elements_to_remove=$((elements_to_remove + 1))
            fi
        fi
        if [[ $system_offset -eq 1 ]]; then
            chat_history=("${chat_history[0]}" "${chat_history[@]:(1 + ${elements_to_remove})}")
        else
            chat_history=("${chat_history[@]:${elements_to_remove}}")
        fi
    fi

    # Serialize history
    history_json_array=$(printf '%s\n' "${chat_history[@]}" | jq -sc 'map(select(. != null))')
    if [[ -z "$history_json_array" || "$history_json_array" == "null" || "$history_json_array" == "[]" ]]; then
        echo -e "${COLOR_ERROR}Error: Failed to serialize history. Rolling back.${COLOR_RESET}" >&2
        if [[ ${#chat_history[@]} -gt 0 ]]; then
             last_idx=$(( ${#chat_history[@]} - 1 ))
             last_role_raw=$(echo "${chat_history[$last_idx]}" | jq -r .role 2>/dev/null)
             if [[ "$last_role_raw" == "user" ]]; then
                 unset 'chat_history[$last_idx]'
                 chat_history=("${chat_history[@]}")
             fi
        fi
        continue
    fi

    # Build payload
    json_payload=""
    if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
        json_payload=$(echo "$history_json_array" | jq -c -n \
            --arg temperature_str "$DEFAULT_OAI_TEMPERATURE" \
            --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
            --arg top_p_str "$DEFAULT_OAI_TOP_P" \
            'input as $contents | {contents: $contents, generationConfig: {temperature: ($temperature_str | tonumber), maxOutputTokens: ($max_tokens_str | tonumber), topP: ($top_p_str | tonumber)}}'
        )
        if [[ "$ENABLE_TOOL_CALLING" == true ]]; then
            json_payload=$(echo "$json_payload" | jq '. + {tools:[{"urlContext": {}}, {"googleSearch": {}}]}')
        fi
    else
         base_payload=$(echo "$history_json_array" | jq -c -n \
            --arg model "$MODEL_ID" \
            --arg temperature_str "$DEFAULT_OAI_TEMPERATURE" \
            'input as $messages | {model: $model, messages: $messages, temperature: ($temperature_str | tonumber), stream: true}'
         )

         if [[ "$PROVIDER" == "ollama" ]]; then
            json_payload=$(echo "$base_payload" | jq -c \
                --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                '. + {options: {num_predict: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber)}}'
            )
         elif [[ "$PROVIDER" == "cloudflare" ]]; then
            json_payload=$(echo "$base_payload" | jq -c \
                --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                '. + {max_tokens: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber)}'
            )
         elif [[ "$PROVIDER" == "nvidia" ]]; then
            if [[ "${MODEL_ID,,}" == *"deepseek"* || "${MODEL_ID,,}" == *"reason"* || "${MODEL_ID,,}" == *"nemotron"* || "${MODEL_ID,,}" == *"qwq"* ]]; then
                json_payload=$(echo "$base_payload" | jq -c \
                    --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                    --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                    '. + {max_tokens: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber), chat_template_kwargs: {thinking: true, reasoning_effort: "max"}}'
                )
            else
                json_payload=$(echo "$base_payload" | jq -c \
                    --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                    --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                    '. + {max_tokens: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber)}'
                )
            fi
         elif [[ "$PROVIDER" != "together" ]]; then
            json_payload=$(echo "$base_payload" | jq -c \
                --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                '. + {max_tokens: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber)}'
            )
         else
            json_payload="$base_payload"
         fi
    fi

    if [[ -z "$json_payload" ]]; then
        echo -e "${COLOR_ERROR}Error: Failed to create JSON payload. Rolling back.${COLOR_RESET}" >&2
        if [[ ${#chat_history[@]} -gt 0 ]]; then
            last_idx=$(( ${#chat_history[@]} - 1 ))
            last_role_raw=$(echo "${chat_history[$last_idx]}" | jq -r .role 2>/dev/null)
            if [[ "$last_role_raw" == "user" ]]; then
                unset 'chat_history[$last_idx]'
                chat_history=("${chat_history[@]}")
            fi
        fi
        continue
    fi

    echo -n -e "\r${COLOR_AI}AI:${COLOR_RESET} ${COLOR_INFO}(💬 Waiting for stream...)${COLOR_RESET}"

    # FIX: Use curl config file for auth header (hides key from ps)
    # FIX: Added --connect-timeout and --max-time to prevent infinite hangs
    chat_curl_config=""
    base_chat_curl_args=(-sS -L -N --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" -X POST "$CHAT_API_URL" -H "Content-Type: application/json" -H "Accept: application/json")
    if [[ -n "$CHAT_AUTH_HEADER" ]]; then
        chat_curl_config=$(create_curl_config "$CHAT_AUTH_HEADER")
        base_chat_curl_args+=(--config "$chat_curl_config")
    fi
    if [[ ${#CHAT_EXTRA_HEADERS[@]} -gt 0 ]]; then
        base_chat_curl_args+=("${CHAT_EXTRA_HEADERS[@]}")
    fi

    full_ai_response_text=""
    full_ai_thinking_text=""
    local_ai_message_json=""
    api_error_occurred=false
    stream_error_message=""
    stream_finish_reason=""
    first_chunk_received=false
    is_thinking=false
    in_thinking_display=false

    CURL_STDERR_TEMP=$(mktemp)
    exec {STREAM_FD}< <(curl "${base_chat_curl_args[@]}" -d @- <<< "$json_payload" 2>"$CURL_STDERR_TEMP")

    while IFS= read -r line <&${STREAM_FD}; do
        json_chunk=""

        if [[ "$line" == "data: "* ]]; then
            json_chunk="${line#data: }"
            if [[ "$json_chunk" == "[DONE]" ]]; then
                break
            fi
        elif [[ "$line" == "{"* ]]; then
            json_chunk="$line"
        fi

        [[ -z "$json_chunk" ]] && continue

        if ! echo "$json_chunk" | jq empty 2>/dev/null; then
            continue
        fi

        # FIX: Combined multiple jq calls into ONE to reduce process spawning (4-6x → 1x per chunk)
        if [[ "$IS_OPENAI_COMPATIBLE" == true ]]; then
            if [[ "$PROVIDER" == "ollama" ]]; then
                IFS=$'\x1f' read -r text_chunk thinking_chunk current_sfr chunk_error < <(
                    echo "$json_chunk" | jq -r '[
                        (.message.content // ""),
                        (.message.thinking // ""),
                        (if .done == true then (.done_reason // "stop") else "" end),
                        (.error // "")
                    ] | join("\u001f")'
                )
            else
                IFS=$'\x1f' read -r text_chunk thinking_chunk current_sfr chunk_error < <(
                    echo "$json_chunk" | jq -r '[
                        (.choices[0].delta.content // .choices[0].text // ""),
                        (.choices[0].delta.reasoning_content // .choices[0].delta.reasoning // ""),
                        (.choices[0].finish_reason // ""),
                        (.error.message // .error // .detail // "")
                    ] | join("\u001f")'
                )
            fi
        else
            # Gemini
            IFS=$'\x1f' read -r text_chunk thinking_chunk current_sfr chunk_error < <(
                echo "$json_chunk" | jq -r '[
                    (.candidates[0].content.parts[0].text // ""),
                    "",
                    (.candidates[0].finishReason // ""),
                    (.error.message // .promptFeedback.blockReason // "")
                ] | join("\u001f")'
            )
        fi

        # Error check
        if [[ -n "$chunk_error" && "$chunk_error" != "null" ]]; then
            stream_error_message="API Error: $chunk_error"
            api_error_occurred=true
            break
        fi

        # Store finish reason
        if [[ -n "$current_sfr" && "$current_sfr" != "null" && -z "$stream_finish_reason" ]]; then
             stream_finish_reason="$current_sfr"
        fi

        # UI update for first chunk
        if [[ "$first_chunk_received" == false && ( -n "$text_chunk" || -n "$thinking_chunk" ) ]]; then
            echo -ne "\r\033[K"; echo -n -e "${COLOR_AI}AI:${COLOR_RESET}  "
            first_chunk_received=true
        fi

        # Handle native thinking output
        if [[ -n "$thinking_chunk" && "$ENABLE_THINKING_OUTPUT" == true ]]; then
            full_ai_thinking_text+="$thinking_chunk"
            if [[ "$in_thinking_display" == false ]]; then
                echo -n -e "${COLOR_THINK}[Thinking] "
                in_thinking_display=true
            fi
            echo -n -e "${COLOR_THINK}${thinking_chunk}${COLOR_RESET}"
        fi

        # Print text with <think> tag handling
        if [[ -n "$text_chunk" ]]; then
            full_ai_response_text+="$text_chunk"
            processing_chunk="$text_chunk"
            while [[ -n "$processing_chunk" ]]; do
                if [[ "$is_thinking" == true ]]; then
                    if [[ "$processing_chunk" == *"</think"* ]]; then
                        before_tag="${processing_chunk%%</think*}"
                        after_tag="${processing_chunk#*</think}"
                        if [[ "$after_tag" == *">"* ]]; then
                            after_tag="${after_tag#*>}"
                        fi
                        if [[ "$ENABLE_THINKING_OUTPUT" == true ]]; then
                            echo -n "${before_tag}"
                            echo -n -e "${COLOR_RESET}\n"
                            echo -n -e "${COLOR_AI}"
                        fi
                        is_thinking=false
                        in_thinking_display=false
                        processing_chunk="$after_tag"
                    else
                        if [[ "$ENABLE_THINKING_OUTPUT" == true ]]; then
                            echo -n "${processing_chunk}"
                        fi
                        processing_chunk=""
                    fi
                else
                    if [[ "$processing_chunk" == *"<think"* ]]; then
                        before_tag="${processing_chunk%%<think*}"
                        after_tag="${processing_chunk#*<think}"
                        if [[ "$after_tag" == *">"* ]]; then
                            after_tag="${after_tag#*>}"
                        fi
                        echo -n -e "${COLOR_AI}${before_tag}"
                        if [[ "$ENABLE_THINKING_OUTPUT" == true ]]; then
                            echo -n -e "${COLOR_THINK}<think"
                            in_thinking_display=true
                        fi
                        is_thinking=true
                        processing_chunk="$after_tag"
                    else
                        if [[ "$in_thinking_display" == true ]]; then
                            echo -n -e "${COLOR_RESET}"
                            in_thinking_display=false
                        fi
                        echo -n -e "${COLOR_AI}${processing_chunk}"
                        processing_chunk=""
                    fi
                fi
            done
        fi

        # Check if stream is done
        if [[ "$IS_OPENAI_COMPATIBLE" == false && -n "$stream_finish_reason" && "$stream_finish_reason" != "null" ]]; then
            if [[ "$stream_finish_reason" == "SAFETY" || "$stream_finish_reason" == "RECITATION" || "$stream_finish_reason" == "OTHER" ]]; then
                 if [[ -z "$full_ai_response_text" ]]; then
                     stream_error_message="Stream ended (Reason: $stream_finish_reason). No content."
                     api_error_occurred=true
                 fi
            fi
            break
        fi

        if [[ "$PROVIDER" == "ollama" && "$stream_finish_reason" == "stop" ]]; then
            break
        fi
    done

    exec {STREAM_FD}<&-
    STREAM_FD=""

    # FIX: Clean up curl config temp file
    if [[ -n "$chat_curl_config" && -f "$chat_curl_config" ]]; then
        rm -f "$chat_curl_config"
    fi
    CURL_CONFIG_TEMP=""

    curl_stderr_content=$(cat "$CURL_STDERR_TEMP" 2>/dev/null || true)
    rm -f "$CURL_STDERR_TEMP"
    CURL_STDERR_TEMP=""

    # Post-stream processing
    if [[ "$first_chunk_received" == false && -z "$stream_error_message" ]]; then
        echo -ne "\r\033[K"
        if [[ -n "$curl_stderr_content" ]]; then
            stream_error_message="API call failed. $(truncate "$curl_stderr_content" 150)"
            api_error_occurred=true
            echo -e "${COLOR_AI}AI:${COLOR_RESET} ${COLOR_ERROR}$stream_error_message${COLOR_RESET}"
        else
            echo -e "${COLOR_AI}AI:${COLOR_RESET} ${COLOR_INFO}(No content in response)${COLOR_RESET}"
        fi
    else
        echo -e "${COLOR_RESET}"
        if [[ "$api_error_occurred" == true && -n "$stream_error_message" ]]; then
            echo -e "${COLOR_ERROR}$stream_error_message${COLOR_RESET}"
        fi
    fi

    # Check response length
    if [[ ${#full_ai_response_text} -gt $MAX_MESSAGE_LENGTH ]]; then
        echo -e "${COLOR_WARN}Warning: Response truncated (exceeded $MAX_MESSAGE_LENGTH chars)${COLOR_RESET}" >&2
        full_ai_response_text="${full_ai_response_text:0:$MAX_MESSAGE_LENGTH}"
    fi

    # Strip think tags for history
    ai_text=$(strip_think_tags "$full_ai_response_text")

    # Create AI message for history
    if [[ "$api_error_occurred" == false && -n "$ai_text" ]]; then
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            local_ai_message_json=$(jq -n --arg text "$ai_text" '{role: "model", parts: [{text: $text}]}')
        else
            local_ai_message_json=$(jq -n --arg content "$ai_text" '{role: "assistant", content: $content}')
        fi
        if ! echo "$local_ai_message_json" | jq empty 2>/dev/null; then
            local_ai_message_json=""
        fi
    else
        local_ai_message_json=""
    fi

    # Add to history or rollback
    if [[ -n "$local_ai_message_json" ]]; then
         chat_history+=("$local_ai_message_json")
    else
        if [[ ${#chat_history[@]} -gt 0 ]]; then
            last_idx_before_ai_response=$(( ${#chat_history[@]} - 1 ))
            last_role_check=$(echo "${chat_history[$last_idx_before_ai_response]}" | jq -r .role 2>/dev/null)
            if [[ "$last_role_check" == "user" ]]; then
                 echo -e "${COLOR_WARN}(Rolled back last user message due to error)${COLOR_RESET}" >&2
                 unset 'chat_history[$last_idx_before_ai_response]'
                 chat_history=("${chat_history[@]}")
            fi
        fi
    fi

    echo ""
done

echo "👋 Chat session ended."
exit 0
