#!/bin/bash
# Universal Chat CLI (Bash/curl/jq/bc) - With Model Selection, HISTORY, SYSTEM PROMPT, STREAMING
# REQUIREMENTS: bash, curl, jq, bc, grep, sed (must be pre-installed on the system)
# Supports: Gemini, OpenRouter, Groq, Together AI, Cerebras AI, Novita AI, Ollama Cloud
# To Run This Tool First Make It executable with $ chmod +x ai.sh
# Run This $ ./ai.sh provider
# filter support added [filter] ... (e.g., ./ai.sh openrouter 32b or ./ai.sh gemini pro)
# History, system prompt, and streaming are supported.
# /history for show conversation log and <think>...</think> in a different colour for better visual experience.
# Session management commands: /save <name>, /load <name>, /clear

# Error handling:
# -E: inherit ERR traps in functions/subshells (where applicable)
# -o pipefail: fail a pipeline if any command fails (not just the last)
set -E -o pipefail

# --- Configuration ---
MAX_HISTORY_MESSAGES=20       # Keep the last N messages (user + ai). Adjust if needed.
MAX_MESSAGE_LENGTH=50000      # Maximum length for a single message
DEFAULT_OAI_TEMPERATURE=0.7   # t = randomness: Higher = more creative, Lower = more predictable | allowed value 0-2
DEFAULT_OAI_MAX_TOKENS=3000   # Default max_tokens for OpenAI-compatible APIs
DEFAULT_OAI_TOP_P=0.9         # p = diversity: Higher = wider vocabulary, Lower = safer word choices | allowed value 0-1
SESSION_DIR="${HOME}/.chat_sessions"    # Directory for storing chat session history files.

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

    # Use bc for comparison but convert result to integer for bash
    if [ "$(echo "$value < $min" | bc)" = "1" ] || [ "$(echo "$value > $max" | bc)" = "1" ]; then
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
# Instruct the AI to use the conversation history to maintain the ongoing task context.
SYSTEM_PROMPT="You are a helpful assistant running in a command-line interface."
# SYSTEM_PROMPT="" # Example: Disable system prompt

# --- Color Definitions --- Use 256-color
COLOR_RESET='\033[0m'
COLOR_USER='\033[38;5;199m'     # Bright Magenta
COLOR_AI='\033[38;5;40m'        # Bright green
COLOR_THINK='\033[38;5;214m'    # Soft orange
COLOR_ERROR='\033[38;5;203m'    # Vivid red
COLOR_WARN='\033[38;5;221m'     # Soft yellow
COLOR_INFO='\033[38;5;75m'      # Darker cyan-blue
COLOR_BOLD='\033[1m'

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

# --- API Endpoints ---
# Chat Endpoints
GEMINI_CHAT_URL_BASE="https://generativelanguage.googleapis.com/v1beta/models/"
OPENROUTER_CHAT_URL="https://openrouter.ai/api/v1/chat/completions"
GROQ_CHAT_URL="https://api.groq.com/openai/v1/chat/completions"
TOGETHER_CHAT_URL="https://api.together.ai/v1/chat/completions"
CEREBRAS_CHAT_URL="https://api.cerebras.ai/v1/chat/completions"
NOVITA_CHAT_URL="https://api.novita.ai/v3/openai/chat/completions"
OLLAMA_CHAT_URL="https://ollama.com/api/chat"

# Model Listing Endpoints
GEMINI_MODELS_URL_BASE="https://generativelanguage.googleapis.com/v1beta/models"
OPENROUTER_MODELS_URL="https://openrouter.ai/api/v1/models"
GROQ_MODELS_URL="https://api.groq.com/openai/v1/models"
TOGETHER_MODELS_URL="https://api.together.ai/v1/models"
CEREBRAS_MODELS_URL="https://api.cerebras.ai/v1/models"
NOVITA_MODELS_URL="https://api.novita.ai/v3/openai/models"
OLLAMA_MODELS_URL="https://ollama.com/api/tags"

# --- Cleanup Trap ---
# Ensures temporary files are removed on script exit/interruption.
CURL_STDERR_TEMP=""
STREAM_FD=""

cleanup() {
    # Close any open file descriptors
    if [[ -n "${STREAM_FD:-}" ]] && [[ -e /proc/$$/fd/$STREAM_FD ]]; then
        exec {STREAM_FD}<&-
    fi
    # Remove temporary files
    if [[ -n "${CURL_STDERR_TEMP:-}" && -f "$CURL_STDERR_TEMP" ]]; then
        rm -f "$CURL_STDERR_TEMP"
    fi
}

# Enhanced signal handling
trap cleanup EXIT
trap 'echo -e "\n${COLOR_WARN}Interrupted. Cleaning up...${COLOR_RESET}"; cleanup; exit 130' INT TERM

# --- Helper Functions ---
function print_usage() {
  echo -e ""
  echo -e "${COLOR_INFO}Usage: $0 <provider>${COLOR_RESET}"
  echo -e "${COLOR_INFO}Usage: $0 <provider> [filter]...${COLOR_RESET}"
  echo -e ""
  echo -e "${COLOR_INFO}Description:${COLOR_RESET}"
  echo -e "  🤖 Starts an interactive chat session with the specified AI provider,"
  echo -e "  maintaining conversation history, using a system prompt (if applicable),"
  echo -e "  and streaming responses token by token."
  echo -e "  It will fetch available models and let you choose one by number."
  echo -e ""
  echo -e "${COLOR_INFO}Supported Providers:${COLOR_RESET}"
  echo -e "  gemini, openrouter, groq, together, cerebras, novita, ollama"
  echo -e ""
  echo -e "${COLOR_INFO}Finding Model Identifiers (if needed manually):${COLOR_RESET}"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Gemini:${COLOR_RESET}     https://ai.google.dev/models/gemini"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}OpenRouter:${COLOR_RESET} https://openrouter.ai/models"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Groq:${COLOR_RESET}       https://console.groq.com/docs/models"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Together:${COLOR_RESET}   https://docs.together.ai/docs/inference-models"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Cerebras:${COLOR_RESET}   https://cloud.cerebras.ai"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Novita:${COLOR_RESET}     https://docs.novita.ai"
  echo -e "    ${COLOR_BOLD}${COLOR_USER}Ollama:${COLOR_RESET}     https://ollama.com/library"
  echo -e ""
  echo -e "${COLOR_BOLD}${COLOR_INFO}Example Commands:${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 gemini${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 groq${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 together${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 openrouter${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 cerebras${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 novita${COLOR_RESET}"
  echo -e "  ${COLOR_BOLD}${COLOR_AI}$0 ollama${COLOR_RESET}"
  echo -e "${COLOR_WARN}NOTE: Ensure API keys are set inside the script before running!${COLOR_RESET}"
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
        if [[ "$remaining" == *"<think>"* ]]; then
            result+="${remaining%%<think>*}"
            remaining="${remaining#*<think>}"
            if [[ "$remaining" == *"</think>"* ]]; then
                remaining="${remaining#*</think>}"
            else
                result+="<think>$remaining"
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
    # Escapes: \ . ^ $ * + ? ( ) [ ] { } |
    printf '%s' "$1" | sed -e 's/[][(){}.^$*+?|\\]/\\&/g'
}

# --- Argument Parsing ---
if [ "$#" -lt 1 ]; then
    echo -e "${COLOR_ERROR}Error: Invalid number of arguments.${COLOR_RESET}" >&2
    print_usage
    exit 1
fi

PROVIDER=$(echo "$1" | tr '[:upper:]' '[:lower:]')
filters=("${@:2}") # Capture all arguments from the second one onwards as filters

# --- Dependency Check ---
required_commands=("curl" "jq" "bc" "grep" "sed")
missing_commands=()
for cmd in "${required_commands[@]}"; do
    if ! command -v "$cmd" &> /dev/null; then
        missing_commands+=("$cmd")
    fi
done
if [ ${#missing_commands[@]} -ne 0 ]; then
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
    *)
        echo -e "${COLOR_ERROR}Error: Unknown provider '$PROVIDER'. Choose from: gemini, openrouter, groq, together, cerebras, novita, ollama${COLOR_RESET}" >&2
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
        # FIX: Reverted to raw array handling for Together AI
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
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='.models[] | .name'
        ;;
esac

model_curl_args=(-sS -L -X GET "$MODELS_URL") # Added -S to show curl errors
[ -n "$MODELS_AUTH_HEADER" ] && model_curl_args+=(-H "$MODELS_AUTH_HEADER")
[ ${#MODELS_EXTRA_HEADERS[@]} -gt 0 ] && model_curl_args+=("${MODELS_EXTRA_HEADERS[@]}")

model_list_json=""
if ! model_list_json=$(curl "${model_curl_args[@]}"); then
    model_list_exit_code=$?
    echo -e "${COLOR_ERROR}Error fetching models: curl command failed (Exit code: $model_list_exit_code).${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}Check network connection, API key validity/permissions, and endpoint ($MODELS_URL).${COLOR_RESET}" >&2
    exit 1
fi

if ! echo "$model_list_json" | jq empty 2>/dev/null; then
    echo -e "${COLOR_ERROR}Error: API response for model list was not valid JSON.${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}Raw response (first 200 chars): $(truncate "$model_list_json" 200)${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}Check API endpoint ($MODELS_URL) and provider status.${COLOR_RESET}" >&2
    exit 1
fi

api_fetch_error=$(echo "$model_list_json" | jq -r 'if type=="object" then .error.message // .error.code // .message // .detail // .error // empty else empty end')
if [[ -n "$api_fetch_error" && "$api_fetch_error" != "null" ]]; then
     echo -e "${COLOR_ERROR}API Error during model fetch: $api_fetch_error${COLOR_RESET}" >&2
     echo -e "${COLOR_INFO}Check API key permissions and validity for provider '${PROVIDER^^}'.${COLOR_RESET}" >&2
     echo -e "${COLOR_INFO}Raw response (first 200 chars): $(truncate "$model_list_json" 200)${COLOR_RESET}" >&2
     exit 1
fi

# FIX: properly capture jq stderr
jq_stderr_output=""
jq_err_file=$(mktemp)
mapfile -t available_models < <(jq -r "$JQ_QUERY" <<< "$model_list_json" 2>"$jq_err_file")
jq_exit_code=$?
jq_stderr_output=$(cat "$jq_err_file" 2>/dev/null || true)
rm -f "$jq_err_file"

if [ $jq_exit_code -ne 0 ] || [ ${#available_models[@]} -eq 0 ]; then
    echo -e "${COLOR_ERROR}Error: No models found or failed to parse successful API response for provider '$PROVIDER'.${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}The API call succeeded, but the JQ query ('${COLOR_BOLD}$JQ_QUERY${COLOR_RESET}') might not match the response structure, produced no output, or jq itself failed.${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}JQ Exit Code was: ${jq_exit_code}${COLOR_RESET}" >&2
    if [[ -n "$jq_stderr_output" ]]; then
      echo -e "${COLOR_ERROR}JQ Error Output:${COLOR_RESET}\n$jq_stderr_output" >&2
    elif [[ ${#available_models[@]} -eq 0 && $jq_exit_code -eq 0 ]]; then
       echo -e "${COLOR_WARN}JQ ran successfully but produced no output. The query likely didn't find matching models in the response.${COLOR_RESET}" >&2
    fi
    echo -e "${COLOR_INFO}Raw API response (first 500 chars):${COLOR_RESET}" >&2
    echo "${model_list_json:0:500}" >&2
    exit 1
fi

# --- Filter models based on additional arguments with improved matching ---
if [ ${#filters[@]} -gt 0 ]; then
    echo -e "${COLOR_INFO}Filtering models with terms: ${filters[*]}${COLOR_RESET}"
    echo -e "${COLOR_INFO}Using word boundary matching for better precision${COLOR_RESET}"
    declare -a filtered_models=()
    # Convert all filters to lowercase once for efficiency
    declare -a filters_lower=()
    for filter in "${filters[@]}"; do
        filters_lower+=("$(echo "$filter" | tr '[:upper:]' '[:lower:]')")
    done

    for model in "${available_models[@]}"; do
        is_match=true
        model_lower=$(echo "$model" | tr '[:upper:]' '[:lower:]')

        for filter_lower in "${filters_lower[@]}"; do
            # Use word boundary matching for better precision
            # This will match "3" in "gpt-3" but not in "13b"
            # FIX: escape regex metacharacters in filter
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
    available_models=("${filtered_models[@]}") # Overwrite with filtered list
fi

# After potential filtering, check if any models are left
if [ ${#available_models[@]} -eq 0 ]; then
    echo -e "${COLOR_ERROR}No models available.${COLOR_RESET}" >&2
    if [ ${#filters[@]} -gt 0 ]; then
        echo -e "${COLOR_WARN}Your filter criteria (${filters[*]}) did not match any models from provider '${PROVIDER^^}'.${COLOR_RESET}" >&2
        echo -e "${COLOR_INFO}Filters use word boundary matching (e.g., '3' matches 'gpt-3' but not '13b')${COLOR_RESET}" >&2
    else
        echo -e "${COLOR_WARN}No models were returned by the API for provider '${PROVIDER^^}'.${COLOR_RESET}" >&2
    fi
    exit 1
fi

MODEL_ID=""

# --- Auto-select if only one model, otherwise prompt user ---
if [ ${#available_models[@]} -eq 1 ]; then
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
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le ${#available_models[@]} ]; then
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
IS_OPENAI_COMPATIBLE=false # Determines payload structure and history role names
ENABLE_TOOL_CALLING=false

# --- Interactive prompt for tool calling for Gemini ---
if [[ "$PROVIDER" == "gemini" ]]; then
    echo ""
    tool_choice_input=""
    while true; do
        read -r -p "$(echo -e "${COLOR_INFO}Enable online tool calling (web search, URL context) for Gemini? (y/n, 1/0): ${COLOR_RESET}")" tool_choice_input
        tool_choice_input=$(echo "$tool_choice_input" | tr '[:upper:]' '[:lower:]') # Convert to lowercase
        if [[ "$tool_choice_input" == "y" || "$tool_choice_input" == "1" ]]; then
            ENABLE_TOOL_CALLING=true
            echo -e "${COLOR_INFO}Tool calling enabled.${COLOR_RESET}"
            break
        elif [[ "$tool_choice_input" == "n" || "$tool_choice_input" == "0" ]]; then
            ENABLE_TOOL_CALLING=false
            echo -e "${COLOR_INFO}Tool calling disabled.${COLOR_RESET}"
            break
        else
            echo -e "${COLOR_WARN}Invalid input. Please enter 'y', 'n', '1', or '0'.${COLOR_RESET}" >&2
        fi
    done
    echo ""
fi

case "$PROVIDER" in
    gemini)
        # Use streamGenerateContent with alt=sse for Server-Sent Events
        CHAT_API_URL="${GEMINI_CHAT_URL_BASE}${MODEL_ID}:streamGenerateContent?key=${API_KEY}&alt=sse"
        IS_OPENAI_COMPATIBLE=false # Gemini uses "model" role, not "assistant"
        ;;
    openrouter|groq|together|cerebras|novita|ollama)
        CHAT_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        IS_OPENAI_COMPATIBLE=true # These use "assistant" role
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
            ollama)     CHAT_API_URL="$OLLAMA_CHAT_URL" ;;
        esac
        ;;
esac

declare -a chat_history=()

initialize_history() {
    chat_history=() # Clear the array
    if [[ -n "$SYSTEM_PROMPT" ]]; then
        if [[ "$IS_OPENAI_COMPATIBLE" == true ]]; then # OpenAI compatible system prompt
            system_message_json=$(jq -n --arg content "$SYSTEM_PROMPT" '{role: "system", content: $content}')
            if [[ -n "$system_message_json" ]]; then
                chat_history+=("$system_message_json")
            fi
        fi
        # For Gemini, system prompt is handled by prepending to first user message.
    fi
}

# Validate a loaded session file
# FIX: validate_session now accepts either OpenAI-style (content) OR Gemini-style (parts)
validate_session() {
    local session_file="$1"

    # Check if it's valid JSON array
    if ! jq -e 'type == "array"' "$session_file" >/dev/null 2>&1; then
        echo -e "${COLOR_ERROR}Error: Session file is not a valid JSON array.${COLOR_RESET}" >&2
        return 1
    fi

    # Check each message has required fields
    local validation_result
    validation_result=$(
        jq -r '
          map(
            if type != "object" then
              "Item is not an object"
            elif .role == null then
              "Missing role field"
            elif .role == "system" then
              if (.content? | type) != "string" then "System message missing string .content" else null end
            elif (.role == "user" or .role == "assistant" or .role == "model") then
              if ((.content? | type) == "string") then null
              elif (.parts? | type) == "array" then null
              else "Message missing .content (OpenAI) or .parts (Gemini)"
              end
            else
              "Unknown role"
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

initialize_history # Set up the initial history (with system prompt if applicable)

echo -e "--- ${COLOR_INFO}Starting Chat${COLOR_RESET} ---"
echo -e "${COLOR_INFO}Provider:${COLOR_RESET}      ${PROVIDER^^}"
echo -e "${COLOR_INFO}Model:${COLOR_RESET}         ${MODEL_ID}"
echo -e "${COLOR_INFO}History Limit:${COLOR_RESET} Last $MAX_HISTORY_MESSAGES messages (user+AI)"
echo -e "${COLOR_INFO}Message Limit:${COLOR_RESET} $MAX_MESSAGE_LENGTH characters per message"
echo -e "${COLOR_INFO}Temp/Tokens/TopP (Defaults):${COLOR_RESET} $DEFAULT_OAI_TEMPERATURE / $DEFAULT_OAI_MAX_TOKENS / $DEFAULT_OAI_TOP_P"

if [[ -n "$SYSTEM_PROMPT" ]]; then
     if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
        echo -e "${COLOR_INFO}System Prompt:${COLOR_RESET}   Set (prepended to first user message for Gemini)"
     elif [[ ${#chat_history[@]} -gt 0 && "$(echo "${chat_history[0]}" | jq -r .role 2>/dev/null)" == "system" ]]; then
        echo -e "${COLOR_INFO}System Prompt:${COLOR_RESET}   Active (OpenAI-compatible format)"
     else
         echo -e "${COLOR_WARN}System Prompt:${COLOR_RESET}   Set but seems inactive.${COLOR_RESET}"
     fi
else
    echo -e "${COLOR_INFO}System Prompt:${COLOR_RESET}   Inactive (set to empty string)"
fi

# Display tool calling status if it's Gemini
if [[ "$PROVIDER" == "gemini" ]]; then
    if [[ "$ENABLE_TOOL_CALLING" == true ]]; then
        echo -e "${COLOR_INFO}Tool Calling:${COLOR_RESET}    ${COLOR_BOLD}Enabled${COLOR_RESET} (for Gemini models)"
    else
        echo -e "${COLOR_INFO}Tool Calling:${COLOR_RESET}    Disabled (for Gemini models)"
    fi
fi

# Updated help text to include new session commands
echo -e "Enter prompt. Type ${COLOR_BOLD}'quit'/'exit'${COLOR_RESET}. Commands: ${COLOR_BOLD}/history, /save <name>, /load <name>, /clear${COLOR_RESET}"
echo -e "---------------------------------------------------------------------------------------"

first_user_message=true

while true; do
    # Readline support when interactive
    if [[ -t 0 ]]; then
         read -r -e -p "$(echo -e "${COLOR_BOLD}${COLOR_USER}You:${COLOR_RESET} ")" user_input
         # Add to shell history if input is not empty
         [[ -n "${user_input:-}" ]] && history -s "$user_input" 2>/dev/null || true
    else
         read -r -p "$(echo -e "${COLOR_BOLD}${COLOR_USER}You:${COLOR_RESET} ")" user_input
    fi

    if [[ "${user_input:-}" == "quit" || "${user_input:-}" == "exit" ]]; then
        echo "Exiting chat."
        break
    fi

    ### --- Session Management Logic --- ###
    if [[ "${user_input:-}" == /* ]]; then
        read -r cmd args <<< "$user_input"
        case "$cmd" in
            "/history")
                echo -e "${COLOR_INFO}--- Current Conversation History (${#chat_history[@]} messages) ---${COLOR_RESET}"
                if [ ${#chat_history[@]} -eq 0 ]; then
                    echo "(History is empty)" >&2
                else
                    printf '%s\n' "${chat_history[@]}" | jq -s -c '.[]' | while IFS= read -r msg; do
                        role=$(echo "$msg" | jq -r '.role')
                        content=$(echo "$msg" | jq -r '.content // .parts[0].text')

                        if [[ "$role" == "user" ]]; then
                            echo -e "${COLOR_USER}[$role]${COLOR_RESET} $(truncate "$content" 500)"
                        elif [[ "$role" == "assistant" || "$role" == "model" ]]; then
                            echo -e "${COLOR_AI}[$role]${COLOR_RESET} $(truncate "$content" 500)"
                        else # system
                            echo -e "${COLOR_WARN}[$role]${COLOR_RESET} $(truncate "$content" 500)"
                        fi
                    done >&2
                fi
                echo -e "${COLOR_INFO}--------------------------------------------${COLOR_RESET}"
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
                mkdir -p "$SESSION_DIR"
                session_file="${SESSION_DIR}/${args}.json"
                # Combine history array into a single JSON array and save
                printf '%s\n' "${chat_history[@]}" | jq -s . > "$session_file"
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

                # Create session directory if it doesn't exist
                mkdir -p "$SESSION_DIR"

                session_file="${SESSION_DIR}/${args}.json"
                if [[ ! -f "$session_file" ]]; then
                    echo -e "${COLOR_ERROR}Error: Session file not found: $session_file${COLOR_RESET}" >&2
                    continue
                fi

                # Validate session file before loading
                if ! validate_session "$session_file"; then
                    echo -e "${COLOR_ERROR}Session file is corrupted or invalid. Cannot load.${COLOR_RESET}" >&2
                    continue
                fi

                # Load JSON array from file into the chat_history bash array
                mapfile -t chat_history < <(jq -c '.[]' "$session_file")
                first_user_message=false # A loaded session is not a "first message"
                echo -e "${COLOR_INFO}Session loaded from: $session_file${COLOR_RESET}"
                echo -e "${COLOR_INFO}Loaded ${#chat_history[@]} messages. Use /history to see the conversation.${COLOR_RESET}"
                continue
                ;;
            "/clear")
                if [ ! -d "$SESSION_DIR" ] || [ -z "$(ls -A "$SESSION_DIR"/*.json 2>/dev/null)" ]; then
                    echo -e "${COLOR_INFO}No saved sessions to clear.${COLOR_RESET}" >&2
                    continue
                fi
                echo -e "${COLOR_WARN}This will permanently delete all saved chat sessions in ${SESSION_DIR}:${COLOR_RESET}" >&2
                # List files to be deleted
                ls -1 "${SESSION_DIR}"/*.json 2>/dev/null | xargs -n1 basename | sed 's/.json$//' >&2
                read -r -p "$(echo -e "${COLOR_WARN}Are you sure you want to proceed? (y/N): ${COLOR_RESET}")" confirm
                if [[ "$confirm" =~ ^[Yy]$ ]]; then
                    # Using find to be safer with filenames with special characters
                    find "$SESSION_DIR" -maxdepth 1 -type f -name "*.json" -delete
                    echo -e "${COLOR_INFO}All saved sessions have been cleared.${COLOR_RESET}"
                else
                    echo -e "${COLOR_INFO}Clear operation cancelled.${COLOR_RESET}"
                fi
                continue
                ;;
        esac
    fi

    if [[ -z "${user_input:-}" ]]; then
        continue
    fi

    # Check message length limit
    if [[ ${#user_input} -gt $MAX_MESSAGE_LENGTH ]]; then
        echo -e "${COLOR_ERROR}Error: Message too long (${#user_input} chars). Maximum is $MAX_MESSAGE_LENGTH characters.${COLOR_RESET}" >&2
        echo -e "${COLOR_INFO}Please shorten your message and try again.${COLOR_RESET}" >&2
        continue
    fi

    user_prompt_text="$user_input"
    # For Gemini, prepend system prompt to the first user message if set
    if [[ "$IS_OPENAI_COMPATIBLE" == false && -n "$SYSTEM_PROMPT" && "$first_user_message" == true ]]; then
        user_prompt_text="${SYSTEM_PROMPT}\n\nUser: ${user_input}"
    fi
    first_user_message=false # Mark that the first user message (potential system prompt carrier) has passed

    user_message_json=""
    if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then # Gemini
        user_message_json=$(jq -n --arg text "$user_prompt_text" '{role: "user", parts: [{text: $text}]}')
    else # OpenAI-Compatible
        user_message_json=$(jq -n --arg content "$user_prompt_text" '{role: "user", content: $content}')
    fi

    if [[ -z "$user_message_json" ]]; then
        echo -e "${COLOR_ERROR}Error: Failed to create user message JSON using jq. Skipping this turn.${COLOR_RESET}" >&2
        continue
    fi
    chat_history+=("$user_message_json")

    # History Truncation Logic
    current_history_size=${#chat_history[@]}
    system_offset=0
    if [[ "$IS_OPENAI_COMPATIBLE" == true && ${#chat_history[@]} -gt 0 && "$(echo "${chat_history[0]}" | jq -r .role 2>/dev/null)" == "system" ]]; then
         system_offset=1 # Account for system prompt not being part of MAX_HISTORY_MESSAGES count
    fi
    allowed_conversational_messages=$(( MAX_HISTORY_MESSAGES )) # user + AI messages
    effective_max_history_entries=$(( allowed_conversational_messages + system_offset ))

    if [[ $current_history_size -gt $effective_max_history_entries ]]; then
        elements_to_remove=$((current_history_size - effective_max_history_entries))

        # FIX: Gemini needs user/model alternation; remove an even number to keep alignment
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            if (( elements_to_remove % 2 == 1 )); then
                elements_to_remove=$((elements_to_remove + 1))
            fi
        fi

        if [[ $system_offset -eq 1 ]]; then # Keep system prompt, remove from user/AI messages
            chat_history=("${chat_history[0]}" "${chat_history[@]:(1 + ${elements_to_remove})}")
        else # No system prompt, remove from the beginning
            chat_history=("${chat_history[@]:${elements_to_remove}}")
        fi
    fi

    history_json_array=$(printf '%s\n' "${chat_history[@]}" | jq -sc 'map(select(. != null))')
    if [[ -z "$history_json_array" || "$history_json_array" == "null" || "$history_json_array" == "[]" ]]; then
        echo -e "${COLOR_ERROR}Error: Failed to create valid JSON array from history. Rolling back last user message.${COLOR_RESET}" >&2
        if [ ${#chat_history[@]} -gt 0 ]; then
             last_idx=$(( ${#chat_history[@]} - 1 ))
             last_role_raw=$(echo "${chat_history[$last_idx]}" | jq -r .role 2>/dev/null)
             if [[ "$last_role_raw" == "user" ]]; then
                 unset 'chat_history[$last_idx]'
                 chat_history=("${chat_history[@]}") # Re-index
             fi
        fi
        continue
    fi

    json_payload=""
    if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then # Gemini payload
        json_payload=$(jq -n --argjson contents "$history_json_array" \
            --arg temperature_str "$DEFAULT_OAI_TEMPERATURE" \
            --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
            --arg top_p_str "$DEFAULT_OAI_TOP_P" \
            '{contents: $contents, generationConfig: {temperature: ($temperature_str | tonumber), maxOutputTokens: ($max_tokens_str | tonumber), topP: ($top_p_str | tonumber)}}'
        )
        if [[ "$ENABLE_TOOL_CALLING" == true ]]; then
            json_payload=$(echo "$json_payload" | jq '. + {tools: [{"urlContext": {}}, {"googleSearch": {}}]}')
        fi
    else # OpenAI-Compatible payload
         ### --- Dynamic Payload Construction --- ###
         # Start with a base payload common to all OpenAI-compatible providers
         base_payload=$(jq -n \
            --arg model "$MODEL_ID" \
            --argjson messages "$history_json_array" \
            --arg temperature_str "$DEFAULT_OAI_TEMPERATURE" \
            '{
                model: $model,
                messages: $messages,
                temperature: ($temperature_str | tonumber),
                stream: true
            }'
         )

         # Ollama uses a specific format with options
         if [[ "$PROVIDER" == "ollama" ]]; then
            json_payload=$(echo "$base_payload" | jq \
                --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                '. + {
                    options: {
                        num_predict: ($max_tokens_str | tonumber),
                        top_p: ($top_p_str | tonumber)
                    }
                }'
            )
            
         # Conditionally add parameters for providers that support them.
         # TogetherAI, for example, can be sensitive to extra parameters on some models.
         elif [[ "$PROVIDER" != "together" ]]; then
            json_payload=$(echo "$base_payload" | jq \
                --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                '. + {
                    max_tokens: ($max_tokens_str | tonumber),
                    top_p: ($top_p_str | tonumber)
                }'
            )
         else
            # For Together, we'll try with just the base payload first
            json_payload="$base_payload"
         fi
    fi

    if [[ -z "$json_payload" ]]; then
        echo -e "${COLOR_ERROR}Error: Failed to create final JSON payload using jq. Rolling back last user message.${COLOR_RESET}" >&2
        if [ ${#chat_history[@]} -gt 0 ]; then
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

    # Base curl arguments for chat
    base_chat_curl_args=(-sS -L -N -X POST "$CHAT_API_URL" -H "Content-Type: application/json" -H "Accept: application/json")
    [ -n "$CHAT_AUTH_HEADER" ] && base_chat_curl_args+=(-H "$CHAT_AUTH_HEADER")
    [ ${#CHAT_EXTRA_HEADERS[@]} -gt 0 ] && base_chat_curl_args+=("${CHAT_EXTRA_HEADERS[@]}")
    base_chat_curl_args+=(-d "$json_payload")

    full_ai_response_text=""
    local_ai_message_json="" # For this turn's AI response
    api_error_occurred=false
    stream_error_message=""
    stream_finish_reason=""
    first_chunk_received=false

    # State variable for tracking if we are inside <think> tags. Reset for each turn.
    is_thinking=false

    CURL_STDERR_TEMP=$(mktemp)
    # Process substitution with proper file descriptor management
    exec {STREAM_FD}< <(curl "${base_chat_curl_args[@]}" 2>"$CURL_STDERR_TEMP")

    while IFS= read -r line <&${STREAM_FD}; do
        # Handle both standard SSE format and Ollama's newline-delimited JSON
        json_chunk=""

        # Check if this is SSE format (starts with "data: ")
        if [[ "$line" == "data: "* ]]; then
            json_chunk="${line#data: }"

            # OpenAI/standard stream end marker
            if [[ "$json_chunk" == "[DONE]" ]]; then
                break
            fi
        elif [[ "$line" == "{"* ]]; then
            # Ollama format - direct JSON without "data: " prefix
            json_chunk="$line"
        fi

        # Skip empty lines
        [[ -z "$json_chunk" ]] && continue

        # Validate JSON
        if ! echo "$json_chunk" | jq empty 2>/dev/null ; then
            continue
        fi

        # Universal error check
        chunk_error=$(echo "$json_chunk" | jq -r '.error.message // .error // .detail // empty')
        if [[ -n "$chunk_error" && "$chunk_error" != "null" ]]; then
            stream_error_message="API Error in stream: $chunk_error"
            api_error_occurred=true
            break
        fi

        # Gemini specific blocking check
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            block_reason=$(echo "$json_chunk" | jq -r '.promptFeedback.blockReason // empty')
            if [[ -n "$block_reason" && "$block_reason" != "null" ]]; then
                stream_error_message="Content blocked by API (Reason: $block_reason)"
                partial_text_block=$(echo "$json_chunk" | jq -r '.candidates[0].content.parts[0].text // empty')
                if [[ -n "$partial_text_block" ]]; then
                     if [[ "$first_chunk_received" == false ]]; then
                        echo -ne "\r\033[K"; echo -n -e "${COLOR_AI}AI:${COLOR_RESET}  ${COLOR_AI}"
                        first_chunk_received=true
                    fi
                    echo -n "$partial_text_block"; full_ai_response_text+="$partial_text_block"
                fi
                api_error_occurred=true
                break
            fi
        fi

        # Extract text chunk and finish reason
        text_chunk=""
        current_sfr=""

        if [[ "$IS_OPENAI_COMPATIBLE" == true ]]; then
            if [[ "$PROVIDER" == "ollama" ]]; then
                # Ollama specific format
                text_chunk=$(echo "$json_chunk" | jq -r '.message.content // empty')
                current_sfr=$(echo "$json_chunk" | jq -r '.done // empty')
                # Check if done is true
                if [[ "$current_sfr" == "true" ]]; then
                    current_sfr="stop"
                else
                    current_sfr=""
                fi
            else
                # Standard OpenAI format
                text_chunk=$(echo "$json_chunk" | jq -r '.choices[0].delta.content // .choices[0].text // empty')
                current_sfr=$(echo "$json_chunk" | jq -r '.choices[0].finish_reason // empty')
            fi
        else
            # Gemini
            text_chunk=$(echo "$json_chunk" | jq -r '.candidates[0].content.parts[0].text // empty')
            current_sfr=$(echo "$json_chunk" | jq -r '.candidates[0].finishReason // empty')

            if [[ -z "$current_sfr" || "$current_sfr" == "null" ]]; then
                 current_sfr=$(echo "$json_chunk" | jq -r '.candidates[0].safetyRatings[]? | select(.blocked == true) | .category // empty' | head -n 1)
                 if [[ -n "$current_sfr" && "$current_sfr" != "null" ]]; then
                     current_sfr="SAFETY"
                 fi
            fi

            # Check for tool calls if enabled
            if [[ "$ENABLE_TOOL_CALLING" == true ]]; then
                tool_call_parts=$(echo "$json_chunk" | jq -c '.candidates[0].content.parts[] | select(.functionCall != null) // empty')
                if [[ -n "$tool_call_parts" ]]; then
                    if [[ "$first_chunk_received" == false ]]; then
                        echo -ne "\r\033[K"; echo -n -e "${COLOR_AI}AI:${COLOR_RESET}  ${COLOR_AI}"
                        first_chunk_received=true
                    fi
                    echo -e "\n${COLOR_WARN}AI requested tool call 🌐:${COLOR_RESET}" >&2
                    echo "$tool_call_parts" | jq . >&2
                    echo -e "${COLOR_WARN}(This script does not automatically execute tool calls or return tool output to the model.)\n${COLOR_RESET}" >&2
                fi
            fi
        fi

        # Store finish reason
        if [[ -n "$current_sfr" && "$current_sfr" != "null" && ( -z "$stream_finish_reason" || "$stream_finish_reason" == "null" ) ]]; then
             stream_finish_reason="$current_sfr"
        fi

        # UI update for first chunk
        if [[ "$first_chunk_received" == false && -n "$text_chunk" ]]; then
            echo -ne "\r\033[K"; echo -n -e "${COLOR_AI}AI:${COLOR_RESET}  "
            first_chunk_received=true
        fi

        # Print and accumulate text with <think> tag handling
        if [[ -n "$text_chunk" ]]; then
            full_ai_response_text+="$text_chunk"

            processing_chunk="$text_chunk"
            while [[ -n "$processing_chunk" ]]; do
                if [[ "$is_thinking" == true ]]; then
                    if [[ "$processing_chunk" == *"</think>"* ]]; then
                        before_tag="${processing_chunk%%</think>*}"
                        after_tag="${processing_chunk#*</think>}"
                        echo -n "${before_tag}"
                        echo -n -e "</think>${COLOR_AI}"
                        is_thinking=false
                        processing_chunk="$after_tag"
                    else
                        echo -n "${processing_chunk}"
                        processing_chunk=""
                    fi
                else
                    if [[ "$processing_chunk" == *"<think>"* ]]; then
                        before_tag="${processing_chunk%%<think>*}"
                        after_tag="${processing_chunk#*<think>}"
                        echo -n "${before_tag}"
                        echo -n -e "${COLOR_THINK}<think>"
                        is_thinking=true
                        processing_chunk="$after_tag"
                    else
                        echo -n -e "${COLOR_AI}${processing_chunk}"
                        processing_chunk=""
                    fi
                fi
            done
        fi

        # Check if stream is done
        if [[ "$IS_OPENAI_COMPATIBLE" == false && -n "$stream_finish_reason" && "$stream_finish_reason" != "null" ]]; then
            if [[ "$stream_finish_reason" == "SAFETY" || "$stream_finish_reason" == "RECITATION" || "$stream_finish_reason" == "OTHER" ]]; then
                 if [[ -z "$text_chunk" && -z "$full_ai_response_text" ]]; then
                     stream_error_message="Stream ended by API (Finish Reason: $stream_finish_reason). No content generated."
                     api_error_occurred=true
                 elif [[ -z "$text_chunk" ]]; then
                     stream_error_message="(Stream truncated by API. Finish Reason: $stream_finish_reason)"
                 fi
            fi
            break
        fi

        # For Ollama, check if done
        if [[ "$PROVIDER" == "ollama" && "$stream_finish_reason" == "stop" ]]; then
            break
        fi
    done

    exec {STREAM_FD}<&-
    STREAM_FD=""

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
            echo -e "${COLOR_AI}AI:${COLOR_RESET} ${COLOR_INFO}(No content in response or empty stream ended prematurely)${COLOR_RESET}"
        fi
    else
        echo -e "${COLOR_RESET}"
        if [[ "$api_error_occurred" == true && -n "$stream_error_message" ]]; then
            echo -e "${COLOR_ERROR}$stream_error_message${COLOR_RESET}"
        fi
    fi

    # Check response length
    if [[ ${#full_ai_response_text} -gt $MAX_MESSAGE_LENGTH ]]; then
        echo -e "${COLOR_WARN}Warning: AI response was truncated (exceeded $MAX_MESSAGE_LENGTH characters)${COLOR_RESET}" >&2
        full_ai_response_text="${full_ai_response_text:0:$MAX_MESSAGE_LENGTH}"
    fi

    # Strip think tags
    ai_text=$(strip_think_tags "$full_ai_response_text")

    # Create AI message for history
    if [[ "$api_error_occurred" == false && -n "$ai_text" ]]; then
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            local_ai_message_json=$(jq -n --arg text "$ai_text" '{role: "model", parts: [{text: $text}]}')
        else
            local_ai_message_json=$(jq -n --arg content "$ai_text" '{role: "assistant", content: $content}')
        fi
        if ! echo "$local_ai_message_json" | jq empty 2>/dev/null; then
            echo -e "${COLOR_WARN}Warning: Internal error creating AI message JSON for history. AI response not added to history.${COLOR_RESET}" >&2
            local_ai_message_json=""
        fi
    else
        local_ai_message_json=""
    fi

    # Add to history or rollback
    if [[ -n "$local_ai_message_json" ]]; then
         chat_history+=("$local_ai_message_json")
    else
        if [[ ${#chat_history[@]} -gt 0 && "$full_ai_response_text" != *"Content blocked by API"* ]]; then
            last_idx_before_ai_response=$(( ${#chat_history[@]} - 1 ))
            last_role_check=$(echo "${chat_history[$last_idx_before_ai_response]}" | jq -r .role 2>/dev/null)
            if [[ "$last_role_check" == "user" ]]; then
                 echo -e "${COLOR_WARN}(Rolling back last user message from history due to error or no AI response text)${COLOR_RESET}" >&2
                 unset 'chat_history[$last_idx_before_ai_response]'
                 chat_history=("${chat_history[@]}")
            fi
        fi
    fi
    
    echo ""
done

echo "👋 Chat session ended."
exit 0
