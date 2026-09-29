#!/bin/bash
# Universal Chat CLI (Bash/curl/jq/bc)
# REQUIREMENTS: bash 4.4+, curl, jq, bc, grep, sed, file, base64
# Supports: Gemini, OpenRouter, Groq, Together AI, Novita AI, Ollama (cloud or local),
#           NVIDIA NIM, Cloudflare Workers AI
#
# Run:   chmod +x ai.sh && ./ai.sh <provider> [filter]...
# e.g.:  ./ai.sh openrouter 32b     ./ai.sh gemini pro
#
# Commands inside chat: /help /history /save /load /clear /upload /image
#                       /clearimage /togglethinking  quit|exit

set -E -o pipefail

# --- Configuration ---
MAX_HISTORY_MESSAGES=20
MAX_MESSAGE_LENGTH=50000
DEFAULT_OAI_TEMPERATURE=0.9   # 0-2
DEFAULT_OAI_MAX_TOKENS=3000
DEFAULT_OAI_TOP_P=1.0         # 0-1
SESSION_DIR="${HOME}/.chat_sessions"
CURL_CONNECT_TIMEOUT=15
CURL_MAX_TIME=300

# --- Image support ---
CURRENT_IMAGE_PATH=""
CURRENT_IMAGE_BASE64=""
CURRENT_IMAGE_MIME=""
MAX_IMAGE_SIZE_MB=20
SUPPORTED_IMAGE_TYPES=("image/jpeg" "image/png" "image/webp" "image/gif" "image/avif" "image/heic" "image/heif" "image/jxl" "image/tiff")

ENABLE_THINKING_OUTPUT=true

SYSTEM_PROMPT="You are a helpful assistant running in a command-line interface."

# --- Colors (real ESC bytes, so they work with echo -e, printf %s and read -p) ---
COLOR_RESET=$'\033[0m'
COLOR_USER=$'\033[38;5;199m'
COLOR_AI=$'\033[38;5;40m'
COLOR_THINK=$'\033[38;5;214m'
COLOR_ERROR=$'\033[38;5;203m'
COLOR_WARN=$'\033[38;5;221m'
COLOR_INFO=$'\033[38;5;75m'
COLOR_BOLD=$'\033[1m'
COLOR_DIM=$'\033[2m'
COLOR_IMAGE=$'\033[38;5;208m'
COLOR_NVIDIA=$'\033[38;5;118m'
RL_S=$'\001'   # readline: start of non-printing sequence
RL_E=$'\002'   # readline: end of non-printing sequence

##########################################################################
#                    !!! EDIT YOUR API KEYS HERE !!!                     #
##########################################################################
GEMINI_API_KEY=""          # https://aistudio.google.com/app/apikey
OPENROUTER_API_KEY=""      # https://openrouter.ai/keys
GROQ_API_KEY=""            # https://console.groq.com/keys
TOGETHER_API_KEY=""        # https://api.together.ai/settings/api-keys
NOVITA_API_KEY=""          # https://novita.ai/
OLLAMA_API_KEY=""          # https://ollama.com/  (leave EMPTY to use local Ollama)
CLOUDFLARE_API_TOKEN=""    # https://developers.cloudflare.com/workers-ai/
CLOUDFLARE_ACCOUNT_ID=""
NVIDIA_API_KEY=""          # https://build.nvidia.com/

# --- Endpoints ---
GEMINI_CHAT_URL_BASE="https://generativelanguage.googleapis.com/v1beta/models/"
OPENROUTER_CHAT_URL="https://openrouter.ai/api/v1/chat/completions"
GROQ_CHAT_URL="https://api.groq.com/openai/v1/chat/completions"
TOGETHER_CHAT_URL="https://api.together.ai/v1/chat/completions"
NOVITA_CHAT_URL="https://api.novita.ai/v3/openai/chat/completions"
OLLAMA_CHAT_URL="https://ollama.com/api/chat"
NVIDIA_CHAT_URL="https://integrate.api.nvidia.com/v1/chat/completions"

GEMINI_MODELS_URL_BASE="https://generativelanguage.googleapis.com/v1beta/models"
OPENROUTER_MODELS_URL="https://openrouter.ai/api/v1/models"
GROQ_MODELS_URL="https://api.groq.com/openai/v1/models"
TOGETHER_MODELS_URL="https://api.together.ai/v1/models"
NOVITA_MODELS_URL="https://api.novita.ai/v3/openai/models"
OLLAMA_MODELS_URL="https://ollama.com/api/tags"
NVIDIA_MODELS_URL="https://integrate.api.nvidia.com/v1/models"

# Interactive shells get readline history (up-arrow recall)
if [[ -t 0 ]]; then set -o history; fi

# --- Validate numeric configuration ---
validate_numeric() {
    local value="$1" min="$2" max="$3" name="$4"
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
validate_numeric "$DEFAULT_OAI_TEMPERATURE" 0 2 "DEFAULT_OAI_TEMPERATURE" || exit 1
validate_numeric "$DEFAULT_OAI_TOP_P" 0 1 "DEFAULT_OAI_TOP_P" || exit 1
validate_numeric "$DEFAULT_OAI_MAX_TOKENS" 1 1000000 "DEFAULT_OAI_MAX_TOKENS" || exit 1

# --- Cleanup / traps ---
CURL_STDERR_TEMP=""
CURL_CONFIG_TEMP=""
STREAM_FD=""
CURL_PID=""
STREAM_INTERRUPTED=false

cleanup() {
    if [[ -n "${STREAM_FD:-}" ]]; then
        exec {STREAM_FD}<&- 2>/dev/null || true
    fi
    if [[ -n "${CURL_PID:-}" ]]; then
        kill "$CURL_PID" 2>/dev/null || true
    fi
    if [[ -n "${CURL_STDERR_TEMP:-}" && -f "$CURL_STDERR_TEMP" ]]; then rm -f "$CURL_STDERR_TEMP"; fi
    if [[ -n "${CURL_CONFIG_TEMP:-}" && -f "$CURL_CONFIG_TEMP" ]]; then rm -f "$CURL_CONFIG_TEMP"; fi
}

global_int_handler() {
    printf '\n%bInterrupted. Cleaning up...%b\n' "$COLOR_WARN" "$COLOR_RESET"
    cleanup
    exit 130
}

# During streaming Ctrl-C only stops the request; it does not end the session.
stream_int_handler() {
    STREAM_INTERRUPTED=true
    if [[ -n "${CURL_PID:-}" ]]; then
        if command -v pkill >/dev/null 2>&1; then pkill -P "$CURL_PID" 2>/dev/null || true; fi
        kill "$CURL_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT
trap global_int_handler INT TERM

# --- Help text ---
print_usage() {
    echo -e ""
    echo -e "${COLOR_INFO}Usage: $0 <provider> [filter]...${COLOR_RESET}"
    echo -e ""
    echo -e "${COLOR_INFO}Supported providers:${COLOR_RESET}"
    echo -e "  gemini, openrouter, groq, together, novita, ollama, cloudflare, ${COLOR_NVIDIA}nvidia${COLOR_RESET}"
    echo -e ""
    print_chat_help
    echo -e ""
    echo -e "${COLOR_INFO}Examples:${COLOR_RESET}"
    echo -e "  $0 gemini"
    echo -e "  $0 groq llama"
    echo -e "  $0 openrouter claude"
    echo -e "  $0 nvidia deepseek"
    echo -e ""
    echo -e "${COLOR_IMAGE}Images:${COLOR_RESET} JPEG, PNG, GIF, WebP, AVIF, HEIC, TIFF, JXL. Max ${MAX_IMAGE_SIZE_MB}MB."
    echo -e "${COLOR_WARN}NOTE: set your API keys inside the script before running.${COLOR_RESET}"
}

print_chat_help() {
    echo -e "${COLOR_INFO}── Available Commands ──────────────────────────────────${COLOR_RESET}"
    echo -e "  ${COLOR_BOLD}/help${COLOR_RESET}            - Show this help message"
    echo -e "  ${COLOR_BOLD}/history${COLOR_RESET}         - Show conversation history"
    echo -e "  ${COLOR_BOLD}/save <name>${COLOR_RESET}     - Save session to ~/.chat_sessions/<name>.json"
    echo -e "  ${COLOR_BOLD}/load <name>${COLOR_RESET}     - Load a saved session"
    echo -e "  ${COLOR_BOLD}/clear${COLOR_RESET}           - Delete all saved sessions"
    echo -e "  ${COLOR_BOLD}/upload <path>${COLOR_RESET}   - Attach an image to your next message"
    echo -e "  ${COLOR_BOLD}/image${COLOR_RESET}           - Show attached image info"
    echo -e "  ${COLOR_BOLD}/clearimage${COLOR_RESET}      - Remove the attached image"
    echo -e "  ${COLOR_BOLD}/togglethinking${COLOR_RESET}  - Toggle reasoning/thinking output"
    echo -e "  ${COLOR_BOLD}quit${COLOR_RESET} / ${COLOR_BOLD}exit${COLOR_RESET}   - End the session"
    echo -e "${COLOR_INFO}────────────────────────────────────────────────────────${COLOR_RESET}"
}

# --- Helpers ---
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

check_placeholder_key() {
    local key_value="$1" provider_name="$2" message=""

    if [[ "$provider_name" == "ollama" && -z "$key_value" ]]; then
        return 0   # local Ollama needs no key
    fi

    if [[ -z "$key_value" ]]; then
        message="is empty"
    elif [[ "$key_value" == "YOUR_"* || "$key_value" == *"-HERE" || "$key_value" == *"..." ]]; then
        message="appears to be a generic placeholder"
    elif [[ "$provider_name" == "gemini" && "$key_value" == "-" ]]; then
        message="is the default placeholder ('-')"
    elif [[ "$provider_name" == "openrouter" && "$key_value" == "sk-or-v1-" ]]; then
        message="is the default OpenRouter prefix placeholder"
    elif [[ "$provider_name" == "groq" && "$key_value" == "gsk_"* && ${#key_value} -lt 10 ]]; then
        message="appears to be an incomplete Groq key"
    elif [[ "$provider_name" =~ ^(novita|ollama|cloudflare|nvidia)$ && ${#key_value} -lt 10 ]]; then
        message="appears to be too short to be a valid key"
    fi

    if [[ -n "$message" ]]; then
        printf '%b!! WARNING: API key for provider %s %s.%b\n' "$COLOR_WARN" "${provider_name^^}" "$message" "$COLOR_RESET" >&2
        printf '%b!! Edit the script (%s) and put in your real key.%b\n' "$COLOR_WARN" "$0" "$COLOR_RESET" >&2
        return 1
    fi
    return 0
}

truncate() {
    local s="$1" max_chars="$2"
    if [[ ${#s} -gt $max_chars ]]; then
        printf '%s\n' "${s:0:$((max_chars-3))}..."
    else
        printf '%s\n' "$s"
    fi
}

strip_think_tags() {
    local text="$1" result="" remaining="$1"
    while [[ -n "$remaining" ]]; do
        if [[ "$remaining" == *"<think"* ]]; then
            result+="${remaining%%<think*}"
            remaining="${remaining#*<think}"
            if [[ "$remaining" == *">"* ]]; then remaining="${remaining#*>}"; fi
            if [[ "$remaining" == *"</think"* ]]; then
                remaining="${remaining#*</think}"
                if [[ "$remaining" == *">"* ]]; then remaining="${remaining#*>}"; fi
            else
                break
            fi
        else
            result+="$remaining"
            break
        fi
    done
    printf '%s\n' "$result"
}

regex_escape_ere() {
    printf '%s' "$1" | sed -e 's/[][(){}.^$*+?|\\]/\\&/g'
}

# Writes header lines to a private temp file so secrets never show up in `ps`.
# Sets the GLOBAL CURL_CONFIG_TEMP (not run in a subshell, so the trap can clean it).
make_curl_config() {
    CURL_CONFIG_TEMP=$(mktemp) || return 1
    chmod 600 "$CURL_CONFIG_TEMP"
    : > "$CURL_CONFIG_TEMP"
    local h
    for h in "$@"; do
        [[ -n "$h" ]] || continue
        h=${h//\\/\\\\}
        h=${h//\"/\\\"}
        printf 'header = "%s"\n' "$h" >> "$CURL_CONFIG_TEMP"
    done
}

# --- Image helpers ---
validate_image_file() {
    local file_path="$1"
    file_path="${file_path//\'/}"
    file_path="${file_path//\"/}"
    file_path="${file_path/#\~/$HOME}"

    if [[ ! -f "$file_path" ]]; then
        echo -e "${COLOR_ERROR}Error: File not found: $file_path${COLOR_RESET}" >&2
        return 1
    fi

    local file_size_bytes
    file_size_bytes=$(stat -c%s "$file_path" 2>/dev/null || stat -f%z "$file_path" 2>/dev/null)
    local max_size_bytes=$((MAX_IMAGE_SIZE_MB * 1024 * 1024))
    if [[ $file_size_bytes -gt $max_size_bytes ]]; then
        echo -e "${COLOR_ERROR}Error: Image too large ($((file_size_bytes / 1024 / 1024))MB). Max: ${MAX_IMAGE_SIZE_MB}MB${COLOR_RESET}" >&2
        return 1
    fi

    local mime_type
    mime_type=$(file -b --mime-type "$file_path" 2>/dev/null || file -I "$file_path" 2>/dev/null | cut -d';' -f1)

    local is_valid=false valid_type
    for valid_type in "${SUPPORTED_IMAGE_TYPES[@]}"; do
        if [[ "$mime_type" == "$valid_type" ]]; then is_valid=true; break; fi
    done
    if [[ "$is_valid" == false ]]; then
        echo -e "${COLOR_ERROR}Error: Unsupported image type: $mime_type${COLOR_RESET}" >&2
        return 1
    fi

    echo "$file_path|$mime_type"
    return 0
}

encode_image_to_base64() {
    local file_path="$1"
    if base64 -w 0 "$file_path" 2>/dev/null; then return 0; fi
    base64 "$file_path" 2>/dev/null | tr -d '\n'
}

clear_current_image() {
    CURRENT_IMAGE_PATH=""
    CURRENT_IMAGE_BASE64=""
    CURRENT_IMAGE_MIME=""
}

# --- Streaming output helpers ---
# Holds back a trailing partial "<think" / "</think" so tags split across chunks are detected.
TAG_BODY=""
TAG_HOLD=""
split_partial_tag() {
    local s="$1" tail
    local re='^<(/?(t(h(i(n(k[^>]*)?)?)?)?)?)?$'
    TAG_BODY="$s"
    TAG_HOLD=""
    if [[ "$s" == *"<"* ]]; then
        tail="<${s##*<}"
        if [[ "$tail" =~ $re ]]; then
            TAG_HOLD="$tail"
            TAG_BODY="${s%"$tail"}"
        fi
    fi
}

is_thinking=false
in_thinking_display=false
tag_carry=""

emit_answer() {
    [[ -n "$1" ]] || return 0
    if [[ "$in_thinking_display" == true ]]; then
        printf '%b\n%b' "$COLOR_RESET" "$COLOR_AI"
        in_thinking_display=false
    fi
    printf '%b%s' "$COLOR_AI" "$1"
}

emit_thinking() {
    [[ "$ENABLE_THINKING_OUTPUT" == true && -n "$1" ]] || return 0
    if [[ "$in_thinking_display" == false ]]; then
        printf '%b[Thinking] ' "$COLOR_THINK"
        in_thinking_display=true
    fi
    printf '%b%s' "$COLOR_THINK" "$1"
}

end_thinking_display() {
    if [[ "$in_thinking_display" == true ]]; then
        printf '%b\n' "$COLOR_RESET"
        in_thinking_display=false
    fi
}

process_text_chunk() {
    local chunk="${tag_carry}$1" rest
    tag_carry=""
    split_partial_tag "$chunk"
    chunk="$TAG_BODY"
    tag_carry="$TAG_HOLD"
    while [[ -n "$chunk" ]]; do
        if [[ "$is_thinking" == true ]]; then
            if [[ "$chunk" == *"</think"* ]]; then
                emit_thinking "${chunk%%</think*}"
                rest="${chunk#*</think}"
                rest="${rest#*>}"
                is_thinking=false
                end_thinking_display
                chunk="$rest"
            else
                emit_thinking "$chunk"
                chunk=""
            fi
        else
            if [[ "$chunk" == *"<think"* ]]; then
                emit_answer "${chunk%%<think*}"
                rest="${chunk#*<think}"
                rest="${rest#*>}"
                is_thinking=true
                chunk="$rest"
            else
                emit_answer "$chunk"
                chunk=""
            fi
        fi
    done
}

truncate_history() {
    local total=${#chat_history[@]} offset=0
    if [[ $total -gt 0 ]] && [[ "$(jq -r '.role' <<< "${chat_history[0]}" 2>/dev/null)" == "system" ]]; then
        offset=1
    fi
    local max=$(( MAX_HISTORY_MESSAGES + offset ))
    (( total > max )) || return 0

    local remove=$(( total - max ))
    local head=()
    if (( offset )); then head=("${chat_history[0]}"); fi
    local rest=("${chat_history[@]:$((offset + remove))}")

    # The conversation must not start with an assistant/model message.
    while (( ${#rest[@]} > 0 )) && [[ "$(jq -r '.role' <<< "${rest[0]}" 2>/dev/null)" != "user" ]]; do
        rest=("${rest[@]:1}")
    done
    chat_history=("${head[@]}" "${rest[@]}")
}

rollback_last_user_message() {
    if [[ ${#chat_history[@]} -gt 0 ]]; then
        local last_idx=$(( ${#chat_history[@]} - 1 ))
        local role
        role=$(jq -r '.role' <<< "${chat_history[$last_idx]}" 2>/dev/null)
        if [[ "$role" == "user" ]]; then
            unset 'chat_history[$last_idx]'
            chat_history=("${chat_history[@]}")
            return 0
        fi
    fi
    return 1
}

# --- Argument parsing ---
if [[ "$#" -lt 1 ]]; then
    echo -e "${COLOR_ERROR}Error: provider required.${COLOR_RESET}" >&2
    print_usage
    exit 1
fi
if [[ "$1" == "-h" || "$1" == "--help" ]]; then print_usage; exit 0; fi

PROVIDER=$(echo "$1" | tr '[:upper:]' '[:lower:]')
filters=("${@:2}")

# --- Dependency check ---
missing_commands=()
for cmd in curl jq bc grep sed file base64; do
    command -v "$cmd" &>/dev/null || missing_commands+=("$cmd")
done
if [[ ${#missing_commands[@]} -ne 0 ]]; then
    echo -e "${COLOR_ERROR}Error: required command(s) not found: ${missing_commands[*]}${COLOR_RESET}" >&2
    exit 1
fi

# --- API key ---
API_KEY=""
key_check_status=0
case "$PROVIDER" in
    gemini)     API_KEY="$GEMINI_API_KEY" ;;
    openrouter) API_KEY="$OPENROUTER_API_KEY" ;;
    groq)       API_KEY="$GROQ_API_KEY" ;;
    together)   API_KEY="$TOGETHER_API_KEY" ;;
    novita)     API_KEY="$NOVITA_API_KEY" ;;
    ollama)     API_KEY="$OLLAMA_API_KEY" ;;
    nvidia)     API_KEY="$NVIDIA_API_KEY" ;;
    cloudflare)
        if [[ -z "$CLOUDFLARE_ACCOUNT_ID" ]]; then
            echo -e "${COLOR_WARN}!! CLOUDFLARE_ACCOUNT_ID is empty. Edit the script ($0).${COLOR_RESET}" >&2
            exit 1
        fi
        API_KEY="$CLOUDFLARE_API_TOKEN"
        ;;
    *)
        echo -e "${COLOR_ERROR}Error: unknown provider '$PROVIDER'.${COLOR_RESET}" >&2
        print_usage
        exit 1
        ;;
esac
check_placeholder_key "$API_KEY" "$PROVIDER" || key_check_status=1
if [[ "$key_check_status" -ne 0 ]]; then exit 1; fi

# Ollama with no key => local server
if [[ "$PROVIDER" == "ollama" && -z "$API_KEY" ]]; then
    OLLAMA_CHAT_URL="http://localhost:11434/api/chat"
    OLLAMA_MODELS_URL="http://localhost:11434/api/tags"
    echo -e "${COLOR_INFO}No Ollama key set: using local server at localhost:11434.${COLOR_RESET}"
fi

# --- Fetch and select model ---
echo -e "${COLOR_INFO}Fetching available models for ${PROVIDER^^}...${COLOR_RESET}"
MODELS_URL=""
JQ_QUERY=""
MODELS_HEADERS=()

case "$PROVIDER" in
    gemini)
        MODELS_URL="${GEMINI_MODELS_URL_BASE}?pageSize=1000"
        MODELS_HEADERS+=("x-goog-api-key: ${API_KEY}")
        JQ_QUERY='.models[] | select(.supportedGenerationMethods[]? | contains("generateContent")) | .name | sub("models/";"") | select(length>0)'
        ;;
    openrouter)
        MODELS_URL="$OPENROUTER_MODELS_URL"
        MODELS_HEADERS+=("Authorization: Bearer ${API_KEY}" "HTTP-Referer: urn:chatcli:bash")
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    groq)
        MODELS_URL="$GROQ_MODELS_URL"
        MODELS_HEADERS+=("Authorization: Bearer ${API_KEY}")
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    together)
        MODELS_URL="$TOGETHER_MODELS_URL"
        MODELS_HEADERS+=("Authorization: Bearer ${API_KEY}")
        JQ_QUERY='(if type=="array" then . else .data end) | sort_by(.id) | .[].id'
        ;;
    novita)
        MODELS_URL="$NOVITA_MODELS_URL"
        MODELS_HEADERS+=("Authorization: Bearer ${API_KEY}")
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    ollama)
        MODELS_URL="$OLLAMA_MODELS_URL"
        [[ -n "$API_KEY" ]] && MODELS_HEADERS+=("Authorization: Bearer ${API_KEY}")
        JQ_QUERY='.models[] | .name'
        ;;
    nvidia)
        MODELS_URL="$NVIDIA_MODELS_URL"
        MODELS_HEADERS+=("Authorization: Bearer ${API_KEY}")
        JQ_QUERY='.data | sort_by(.id) | .[].id'
        ;;
    cloudflare)
        MODELS_URL="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/models/search"
        MODELS_HEADERS+=("Authorization: Bearer ${API_KEY}")
        JQ_QUERY='.result | sort_by(.name) | .[].name'
        ;;
esac

model_curl_args=(-sS -L --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time 30 -X GET "$MODELS_URL")
if [[ ${#MODELS_HEADERS[@]} -gt 0 ]]; then
    make_curl_config "${MODELS_HEADERS[@]}"
    model_curl_args+=(--config "$CURL_CONFIG_TEMP")
fi

model_list_json=""
if ! model_list_json=$(curl "${model_curl_args[@]}"); then
    echo -e "${COLOR_ERROR}Error fetching models: curl failed.${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}Check network, API key, and endpoint ($MODELS_URL).${COLOR_RESET}" >&2
    exit 1
fi
if [[ -n "$CURL_CONFIG_TEMP" ]]; then rm -f "$CURL_CONFIG_TEMP"; CURL_CONFIG_TEMP=""; fi

if ! jq empty <<< "$model_list_json" 2>/dev/null; then
    echo -e "${COLOR_ERROR}Error: model list response was not valid JSON.${COLOR_RESET}" >&2
    printf '%bRaw response (first 200 chars): %s%b\n' "$COLOR_INFO" "$(truncate "$model_list_json" 200)" "$COLOR_RESET" >&2
    exit 1
fi

api_fetch_error=$(jq -r 'if type=="object" then (.error.message? // .error.code? // .message? // .detail? // (.error | strings)? // empty) else empty end' <<< "$model_list_json" 2>/dev/null)
if [[ -n "$api_fetch_error" && "$api_fetch_error" != "null" ]]; then
    printf '%bAPI error during model fetch: %s%b\n' "$COLOR_ERROR" "$api_fetch_error" "$COLOR_RESET" >&2
    exit 1
fi

jq_err_file=$(mktemp)
models_out=$(jq -r "$JQ_QUERY" <<< "$model_list_json" 2>"$jq_err_file")
jq_exit_code=$?
jq_stderr_output=$(cat "$jq_err_file" 2>/dev/null || true)
rm -f "$jq_err_file"

available_models=()
if [[ -n "$models_out" ]]; then mapfile -t available_models <<< "$models_out"; fi

if [[ $jq_exit_code -ne 0 ]] || [[ ${#available_models[@]} -eq 0 ]]; then
    echo -e "${COLOR_ERROR}Error: no models found for provider '$PROVIDER'.${COLOR_RESET}" >&2
    [[ -n "$jq_stderr_output" ]] && printf 'JQ error: %s\n' "$jq_stderr_output" >&2
    echo "${model_list_json:0:500}" >&2
    exit 1
fi

# --- Filter models ---
if [[ ${#filters[@]} -gt 0 ]]; then
    echo -e "${COLOR_INFO}Filtering models with terms: ${filters[*]}${COLOR_RESET}"
    filtered_models=()
    filters_lower=()
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
        [[ "$is_match" == true ]] && filtered_models+=("$model")
    done
    available_models=("${filtered_models[@]}")
fi

if [[ ${#available_models[@]} -eq 0 ]]; then
    echo -e "${COLOR_ERROR}No models available.${COLOR_RESET}" >&2
    if [[ ${#filters[@]} -gt 0 ]]; then
        echo -e "${COLOR_WARN}Filter (${filters[*]}) matched nothing from ${PROVIDER^^}.${COLOR_RESET}" >&2
    fi
    exit 1
fi

MODEL_ID=""
if [[ ${#available_models[@]} -eq 1 ]]; then
    MODEL_ID="${available_models[0]}"
    echo -e "${COLOR_INFO}Auto-selecting only matching model.${COLOR_RESET}"
else
    echo -e "${COLOR_INFO}Available models for ${PROVIDER^^}:${COLOR_RESET}"
    for i in "${!available_models[@]}"; do
        printf "  ${COLOR_BOLD}%3d${COLOR_RESET}. %s\n" $((i+1)) "${available_models[$i]}"
    done
    echo ""
    while true; do
        read -r -p "${RL_S}${COLOR_INFO}${RL_E}Select model by number: ${RL_S}${COLOR_RESET}${RL_E}" choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && [[ "$choice" -ge 1 ]] && [[ "$choice" -le ${#available_models[@]} ]]; then
            MODEL_ID="${available_models[$((choice-1))]}"
            break
        else
            echo -e "${COLOR_WARN}Enter a number between 1 and ${#available_models[@]}.${COLOR_RESET}" >&2
        fi
    done
fi
printf '%bUsing model:%b %s\n\n' "$COLOR_INFO" "$COLOR_RESET" "$MODEL_ID"

CHAT_API_URL=""
CHAT_HEADERS=()
IS_OPENAI_COMPATIBLE=false
ENABLE_TOOL_CALLING=false

if [[ "$PROVIDER" == "gemini" ]]; then
    while true; do
        read -r -p "${RL_S}${COLOR_INFO}${RL_E}Enable online tool calling (web search, URL context) for Gemini? (y/n): ${RL_S}${COLOR_RESET}${RL_E}" tool_choice_input
        tool_choice_input=$(echo "$tool_choice_input" | tr '[:upper:]' '[:lower:]')
        if [[ "$tool_choice_input" == "y" ]]; then ENABLE_TOOL_CALLING=true; echo -e "${COLOR_INFO}Tool calling enabled.${COLOR_RESET}"; break
        elif [[ "$tool_choice_input" == "n" ]]; then ENABLE_TOOL_CALLING=false; echo -e "${COLOR_INFO}Tool calling disabled.${COLOR_RESET}"; break
        else echo -e "${COLOR_WARN}Please enter 'y' or 'n'.${COLOR_RESET}" >&2; fi
    done
    echo ""
fi

case "$PROVIDER" in
    gemini)
        CHAT_API_URL="${GEMINI_CHAT_URL_BASE}${MODEL_ID}:streamGenerateContent?alt=sse"
        CHAT_HEADERS+=("x-goog-api-key: ${API_KEY}")
        IS_OPENAI_COMPATIBLE=false
        ;;
    openrouter|groq|together|novita|ollama|nvidia|cloudflare)
        IS_OPENAI_COMPATIBLE=true
        [[ -n "$API_KEY" ]] && CHAT_HEADERS+=("Authorization: Bearer ${API_KEY}")
        case "$PROVIDER" in
            openrouter)
                CHAT_API_URL="$OPENROUTER_CHAT_URL"
                CHAT_HEADERS+=("HTTP-Referer: urn:chatcli:bash" "X-Title: BashChatCLI")
                ;;
            groq)       CHAT_API_URL="$GROQ_CHAT_URL" ;;
            together)   CHAT_API_URL="$TOGETHER_CHAT_URL" ;;
            novita)     CHAT_API_URL="$NOVITA_CHAT_URL" ;;
            nvidia)     CHAT_API_URL="$NVIDIA_CHAT_URL" ;;
            ollama)     CHAT_API_URL="$OLLAMA_CHAT_URL" ;;
            cloudflare) CHAT_API_URL="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1/chat/completions" ;;
        esac
        ;;
esac

declare -a chat_history=()

initialize_history() {
    chat_history=()
    if [[ -n "$SYSTEM_PROMPT" && "$IS_OPENAI_COMPATIBLE" == true ]]; then
        local sys_json
        sys_json=$(jq -n --arg content "$SYSTEM_PROMPT" '{role: "system", content: $content}')
        [[ -n "$sys_json" ]] && chat_history+=("$sys_json")
    fi
}

validate_session() {
    local session_file="$1"
    if ! jq -e 'type == "array"' "$session_file" >/dev/null 2>&1; then
        echo -e "${COLOR_ERROR}Error: session file is not a valid JSON array.${COLOR_RESET}" >&2
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
              if ((.content? | type) == "string" or (.content? | type) == "array") then null
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
        echo -e "${COLOR_ERROR}Error: invalid session format - $validation_result${COLOR_RESET}" >&2
        return 1
    fi
    return 0
}

initialize_history

# --- Banner ---
echo -e "┌─────────────────────────────────────────────────────────────────────┐"
echo -e "│ ${COLOR_BOLD}AI Chat CLI (Bash)${COLOR_RESET}"
echo -e "├─────────────────────────────────────────────────────────────────────┤"
printf '│ %bProvider:%b      %s\n' "$COLOR_INFO" "$COLOR_RESET" "${PROVIDER^^}"
printf '│ %bModel:%b         %s\n' "$COLOR_INFO" "$COLOR_RESET" "$MODEL_ID"
printf '│ %bHistory limit:%b last %s messages\n' "$COLOR_INFO" "$COLOR_RESET" "$MAX_HISTORY_MESSAGES"
printf '│ %bMessage limit:%b %s characters\n' "$COLOR_INFO" "$COLOR_RESET" "$MAX_MESSAGE_LENGTH"
printf '│ %bTemp/Tokens:%b   %s / %s\n' "$COLOR_INFO" "$COLOR_RESET" "$DEFAULT_OAI_TEMPERATURE" "$DEFAULT_OAI_MAX_TOKENS"
if [[ -n "$SYSTEM_PROMPT" ]]; then
    printf '│ %bSystem prompt:%b Active\n' "$COLOR_INFO" "$COLOR_RESET"
else
    printf '│ %bSystem prompt:%b Inactive\n' "$COLOR_INFO" "$COLOR_RESET"
fi
if [[ "$PROVIDER" == "gemini" ]]; then
    if [[ "$ENABLE_TOOL_CALLING" == true ]]; then
        printf '│ %bTool calling:%b  Enabled\n' "$COLOR_INFO" "$COLOR_RESET"
    else
        printf '│ %bTool calling:%b  Disabled\n' "$COLOR_INFO" "$COLOR_RESET"
    fi
fi
if [[ "$ENABLE_THINKING_OUTPUT" == true ]]; then
    printf '│ %bThinking:%b      %bEnabled%b %b(/togglethinking)%b\n' "$COLOR_INFO" "$COLOR_RESET" "$COLOR_THINK" "$COLOR_RESET" "$COLOR_DIM" "$COLOR_RESET"
else
    printf '│ %bThinking:%b      Disabled %b(/togglethinking)%b\n' "$COLOR_INFO" "$COLOR_RESET" "$COLOR_DIM" "$COLOR_RESET"
fi
echo -e "├─────────────────────────────────────────────────────────────────────┤"
echo -e "│ Type ${COLOR_BOLD}quit${COLOR_RESET} to exit • ${COLOR_BOLD}/help${COLOR_RESET} for commands • Ctrl-C stops a response"
echo -e "└─────────────────────────────────────────────────────────────────────┘"
echo ""

# =====================================================================
# Main chat loop
# =====================================================================
while true; do
    prompt_prefix=""
    if [[ -n "$CURRENT_IMAGE_PATH" ]]; then
        prompt_prefix="[${RL_S}${COLOR_IMAGE}${RL_E}📎 $(basename "$CURRENT_IMAGE_PATH")${RL_S}${COLOR_RESET}${RL_E}] "
    fi
    prompt="${prompt_prefix}${RL_S}${COLOR_BOLD}${COLOR_USER}${RL_E}You:${RL_S}${COLOR_RESET}${RL_E} "

    user_input=""
    if [[ -t 0 ]]; then
        if ! read -r -e -p "$prompt" user_input; then echo ""; break; fi
        if [[ -n "$user_input" ]]; then history -s -- "$user_input" 2>/dev/null || true; fi
    else
        if ! read -r -p "$prompt" user_input; then echo ""; break; fi
    fi

    if [[ "$user_input" == "quit" || "$user_input" == "exit" ]]; then
        echo "Exiting chat."
        break
    fi

    ### --- Commands --- ###
    if [[ "$user_input" == /* ]]; then
        read -r cmd args <<< "$user_input"
        case "$cmd" in
            "/help")
                print_chat_help
                continue
                ;;
            "/upload")
                if [[ -z "${args:-}" ]]; then
                    echo -e "${COLOR_IMAGE}Usage: /upload <image_path>${COLOR_RESET}" >&2
                    continue
                fi
                echo -e "${COLOR_IMAGE}Validating image...${COLOR_RESET}" >&2
                if ! validation_result=$(validate_image_file "$args"); then continue; fi
                file_path="${validation_result%|*}"
                mime_type="${validation_result##*|}"
                echo -e "${COLOR_IMAGE}Encoding image...${COLOR_RESET}" >&2
                base64_data=$(encode_image_to_base64 "$file_path")
                if [[ -z "$base64_data" ]]; then
                    echo -e "${COLOR_ERROR}Error: failed to encode image${COLOR_RESET}" >&2
                    continue
                fi
                CURRENT_IMAGE_PATH="$file_path"
                CURRENT_IMAGE_BASE64="$base64_data"
                CURRENT_IMAGE_MIME="$mime_type"
                file_size_kb=$(( $(stat -c%s "$file_path" 2>/dev/null || stat -f%z "$file_path" 2>/dev/null) / 1024 ))
                printf '%b✓ Attached: %s (%s, %sKB)%b\n' "$COLOR_IMAGE" "$(basename "$file_path")" "$mime_type" "$file_size_kb" "$COLOR_RESET" >&2
                continue
                ;;
            "/image")
                if [[ -n "$CURRENT_IMAGE_PATH" ]]; then
                    printf '%bCurrent image: %s (%s)%b\n' "$COLOR_IMAGE" "$(basename "$CURRENT_IMAGE_PATH")" "$CURRENT_IMAGE_MIME" "$COLOR_RESET" >&2
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
                    echo -e "${COLOR_INFO}Thinking output disabled.${COLOR_RESET}" >&2
                else
                    ENABLE_THINKING_OUTPUT=true
                    echo -e "${COLOR_INFO}Thinking output enabled.${COLOR_RESET}" >&2
                fi
                continue
                ;;
            "/history")
                echo -e "${COLOR_INFO}── History (${#chat_history[@]} messages) ─────────────────────${COLOR_RESET}"
                if [[ ${#chat_history[@]} -eq 0 ]]; then
                    echo "  (empty)"
                else
                    printf '%s\n' "${chat_history[@]}" | jq -r '
                        [.role,
                         (if (.content | type) == "string" then .content
                          elif (.parts? | type) == "array" then (.parts[0].text // "[📎 image]")
                          elif (.content | type) == "array" then ((.content[] | select(.type == "text") | .text) // "[📎 image]")
                          else "[content]" end)
                        ] | @tsv' | while IFS=$'\t' read -r role content; do
                        case "$role" in
                            user)             c="$COLOR_USER" ;;
                            assistant|model)  c="$COLOR_AI" ;;
                            *)                c="$COLOR_WARN" ;;
                        esac
                        printf '  %b[%s]%b %s\n' "$c" "$role" "$COLOR_RESET" "$(truncate "$content" 500)"
                    done
                fi
                echo -e "${COLOR_INFO}────────────────────────────────────────────────────${COLOR_RESET}"
                continue
                ;;
            "/save")
                if [[ -z "${args:-}" ]]; then echo -e "${COLOR_WARN}Usage: /save <session_name>${COLOR_RESET}" >&2; continue; fi
                validate_session_name "$args" || continue
                mkdir -p "$SESSION_DIR"
                chmod 700 "$SESSION_DIR"
                session_file="${SESSION_DIR}/${args}.json"
                ( umask 077; printf '%s\n' "${chat_history[@]}" | jq -s . > "$session_file" )
                chmod 600 "$session_file"
                printf '%bSession saved to: %s%b\n' "$COLOR_INFO" "$session_file" "$COLOR_RESET"
                continue
                ;;
            "/load")
                if [[ -z "${args:-}" ]]; then echo -e "${COLOR_WARN}Usage: /load <session_name>${COLOR_RESET}" >&2; continue; fi
                validate_session_name "$args" || continue
                session_file="${SESSION_DIR}/${args}.json"
                if [[ ! -f "$session_file" ]]; then
                    printf '%bError: session file not found: %s%b\n' "$COLOR_ERROR" "$session_file" "$COLOR_RESET" >&2
                    continue
                fi
                if ! validate_session "$session_file"; then continue; fi
                mapfile -t chat_history < <(jq -c '.[]' "$session_file")
                printf '%bSession loaded from: %s (%s messages)%b\n' "$COLOR_INFO" "$session_file" "${#chat_history[@]}" "$COLOR_RESET"
                continue
                ;;
            "/clear")
                if [[ ! -d "$SESSION_DIR" ]] || [[ -z "$(ls -A "$SESSION_DIR"/*.json 2>/dev/null)" ]]; then
                    echo -e "${COLOR_INFO}No saved sessions to clear.${COLOR_RESET}" >&2
                    continue
                fi
                printf '%bThis will permanently delete all saved sessions in %s:%b\n' "$COLOR_WARN" "$SESSION_DIR" "$COLOR_RESET" >&2
                ls -1 "${SESSION_DIR}"/*.json 2>/dev/null | xargs -n1 basename | sed 's/\.json$//' >&2
                read -r -p "${RL_S}${COLOR_WARN}${RL_E}Are you sure? (y/N): ${RL_S}${COLOR_RESET}${RL_E}" confirm
                if [[ "$confirm" =~ ^[Yy]$ ]]; then
                    find "$SESSION_DIR" -maxdepth 1 -type f -name "*.json" -delete
                    echo -e "${COLOR_INFO}All saved sessions cleared.${COLOR_RESET}"
                else
                    echo -e "${COLOR_INFO}Cancelled.${COLOR_RESET}"
                fi
                continue
                ;;
            *)
                printf '%bUnknown command %s. Type /help.%b\n' "$COLOR_WARN" "$cmd" "$COLOR_RESET" >&2
                continue
                ;;
        esac
    fi

    if [[ -z "$user_input" && -z "$CURRENT_IMAGE_BASE64" ]]; then continue; fi
    if [[ -z "$user_input" && -n "$CURRENT_IMAGE_BASE64" ]]; then user_input="Describe this image in detail."; fi
    if [[ ${#user_input} -gt $MAX_MESSAGE_LENGTH ]]; then
        echo -e "${COLOR_ERROR}Error: message too long (${#user_input} chars). Max: $MAX_MESSAGE_LENGTH${COLOR_RESET}" >&2
        continue
    fi

    echo -e "${COLOR_INFO}[Sending...]${COLOR_RESET}" >&2

    # --- Build user message (images use --rawfile: base64 can exceed argv limits) ---
    user_message_json=""
    if [[ -n "$CURRENT_IMAGE_BASE64" ]]; then
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            user_message_json=$(jq -n \
                --arg text "$user_input" \
                --arg mime "$CURRENT_IMAGE_MIME" \
                --rawfile data <(printf '%s' "$CURRENT_IMAGE_BASE64") \
                '{role: "user", parts: [{text: $text}, {inlineData: {mimeType: $mime, data: $data}}]}')
        elif [[ "$PROVIDER" == "ollama" ]]; then
            user_message_json=$(jq -n \
                --arg content "$user_input" \
                --rawfile image_data <(printf '%s' "$CURRENT_IMAGE_BASE64") \
                '{role: "user", content: $content, images: [$image_data]}')
        else
            user_message_json=$(jq -n \
                --arg text "$user_input" \
                --arg mime "$CURRENT_IMAGE_MIME" \
                --rawfile data <(printf '%s' "$CURRENT_IMAGE_BASE64") \
                '{role: "user", content: [{type: "text", text: $text}, {type: "image_url", image_url: {url: ("data:" + $mime + ";base64," + $data)}}]}')
        fi
        if [[ -n "$user_message_json" ]]; then clear_current_image; fi
    else
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            user_message_json=$(jq -n --arg text "$user_input" '{role: "user", parts: [{text: $text}]}')
        else
            user_message_json=$(jq -n --arg content "$user_input" '{role: "user", content: $content}')
        fi
    fi

    if [[ -z "$user_message_json" ]]; then
        echo -e "${COLOR_ERROR}Error: failed to create user message JSON. Image kept; try again.${COLOR_RESET}" >&2
        continue
    fi
    chat_history+=("$user_message_json")

    truncate_history

    history_json_array=$(printf '%s\n' "${chat_history[@]}" | jq -sc 'map(select(. != null))')
    if [[ -z "$history_json_array" || "$history_json_array" == "null" || "$history_json_array" == "[]" ]]; then
        echo -e "${COLOR_ERROR}Error: failed to serialize history. Rolling back.${COLOR_RESET}" >&2
        rollback_last_user_message
        continue
    fi

    # --- Build payload (history goes through stdin, never argv) ---
    json_payload=""
    if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
        json_payload=$(jq -c -n \
            --arg temperature_str "$DEFAULT_OAI_TEMPERATURE" \
            --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
            --arg top_p_str "$DEFAULT_OAI_TOP_P" \
            --arg sys "$SYSTEM_PROMPT" \
            'input as $contents
             | {contents: $contents,
                generationConfig: {temperature: ($temperature_str | tonumber),
                                   maxOutputTokens: ($max_tokens_str | tonumber),
                                   topP: ($top_p_str | tonumber)}}
             | if $sys != "" then . + {systemInstruction: {parts: [{text: $sys}]}} else . end' \
            <<< "$history_json_array")
        if [[ "$ENABLE_TOOL_CALLING" == true && -n "$json_payload" ]]; then
            json_payload=$(jq -c '. + {tools: [{"urlContext": {}}, {"googleSearch": {}}]}' <<< "$json_payload")
        fi
    else
        base_payload=$(jq -c -n \
            --arg model "$MODEL_ID" \
            --arg temperature_str "$DEFAULT_OAI_TEMPERATURE" \
            'input as $messages | {model: $model, messages: $messages, temperature: ($temperature_str | tonumber), stream: true}' \
            <<< "$history_json_array")

        if [[ -z "$base_payload" ]]; then
            json_payload=""
        elif [[ "$PROVIDER" == "ollama" ]]; then
            json_payload=$(jq -c \
                --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                --argjson think "$ENABLE_THINKING_OUTPUT" \
                '. + {think: $think, options: {num_predict: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber)}}' \
                <<< "$base_payload")
        elif [[ "$PROVIDER" == "nvidia" ]]; then
            model_lc="${MODEL_ID,,}"
            if [[ "$ENABLE_THINKING_OUTPUT" == true && ( "$model_lc" == *deepseek* || "$model_lc" == *reason* || "$model_lc" == *nemotron* || "$model_lc" == *qwq* ) ]]; then
                json_payload=$(jq -c \
                    --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                    --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                    '. + {max_tokens: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber), chat_template_kwargs: {thinking: true, reasoning_effort: "max"}}' \
                    <<< "$base_payload")
            else
                json_payload=$(jq -c \
                    --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                    --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                    '. + {max_tokens: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber)}' \
                    <<< "$base_payload")
            fi
        elif [[ "$PROVIDER" == "together" ]]; then
            json_payload="$base_payload"
        else
            json_payload=$(jq -c \
                --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
                --arg top_p_str "$DEFAULT_OAI_TOP_P" \
                '. + {max_tokens: ($max_tokens_str | tonumber), top_p: ($top_p_str | tonumber)}' \
                <<< "$base_payload")
        fi
    fi

    if [[ -z "$json_payload" ]]; then
        echo -e "${COLOR_ERROR}Error: failed to create JSON payload. Rolling back.${COLOR_RESET}" >&2
        rollback_last_user_message
        continue
    fi

    printf '\r%bAI:%b %b(💬 Waiting for stream...)%b' "$COLOR_AI" "$COLOR_RESET" "$COLOR_INFO" "$COLOR_RESET"

    # --- Request (auth header lives in a private curl config, not on argv) ---
    make_curl_config "${CHAT_HEADERS[@]}"
    chat_curl_config="$CURL_CONFIG_TEMP"
    chat_curl_args=(-sS -L -N --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME"
                    -X POST "$CHAT_API_URL" -H "Content-Type: application/json" -H "Accept: application/json"
                    --config "$chat_curl_config")

    full_ai_response_text=""
    api_error_occurred=false
    stream_error_message=""
    stream_finish_reason=""
    first_chunk_received=false
    is_thinking=false
    in_thinking_display=false
    tag_carry=""
    err_buffer=""
    STREAM_INTERRUPTED=false

    CURL_STDERR_TEMP=$(mktemp)
    trap stream_int_handler INT
    exec {STREAM_FD}< <(curl "${chat_curl_args[@]}" --data-binary @- <<< "$json_payload" 2>"$CURL_STDERR_TEMP")
    CURL_PID=$!

    while IFS= read -r line <&"${STREAM_FD}" || [[ -n "$line" ]]; do
        [[ "$STREAM_INTERRUPTED" == true ]] && break
        line="${line%$'\r'}"
        json_chunk=""

        if [[ "$line" == "data: "* ]]; then
            json_chunk="${line#data: }"
            [[ "$json_chunk" == "[DONE]" ]] && break
        elif [[ "$line" == "data:"* ]]; then
            json_chunk="${line#data:}"
        elif [[ "$line" == "{"* ]]; then
            json_chunk="$line"
        fi

        # SSE comments/keep-alives and empty lines are ignored; anything else is
        # kept so a pretty-printed HTTP error body can be shown later.
        if [[ -z "$json_chunk" ]] || ! jq empty <<< "$json_chunk" 2>/dev/null; then
            if [[ -n "$line" && "$line" != :* && "$line" != event:* && "$line" != id:* && "$line" != retry:* && "$line" != data:* ]]; then
                if [[ ${#err_buffer} -lt 20000 ]]; then err_buffer+="$line"$'\n'; fi
            fi
            continue
        fi

        # One jq call per chunk; fields are \x1f-terminated so embedded newlines survive.
        text_chunk="" thinking_chunk="" current_sfr="" chunk_error=""
        if [[ "$IS_OPENAI_COMPATIBLE" == true ]]; then
            if [[ "$PROVIDER" == "ollama" ]]; then
                {
                    IFS= read -r -d $'\x1f' text_chunk
                    IFS= read -r -d $'\x1f' thinking_chunk
                    IFS= read -r -d $'\x1f' current_sfr
                    IFS= read -r -d $'\x1f' chunk_error
                } < <(jq -j '[
                        (.message.content // ""),
                        (.message.thinking // ""),
                        (if .done == true then (.done_reason // "stop") else "" end),
                        (.error // "")
                    ] | .[] | (tostring, "\u001f")' <<< "$json_chunk")
            else
                {
                    IFS= read -r -d $'\x1f' text_chunk
                    IFS= read -r -d $'\x1f' thinking_chunk
                    IFS= read -r -d $'\x1f' current_sfr
                    IFS= read -r -d $'\x1f' chunk_error
                } < <(jq -j '[
                        (.choices[0].delta.content // .choices[0].text // ""),
                        (.choices[0].delta.reasoning_content // .choices[0].delta.reasoning // ""),
                        (.choices[0].finish_reason // ""),
                        (.error.message? // .error? // .detail? // "")
                    ] | .[] | (tostring, "\u001f")' <<< "$json_chunk")
            fi
        else
            {
                IFS= read -r -d $'\x1f' text_chunk
                IFS= read -r -d $'\x1f' thinking_chunk
                IFS= read -r -d $'\x1f' current_sfr
                IFS= read -r -d $'\x1f' chunk_error
            } < <(jq -j '[
                    ([.candidates[0].content.parts[]? | select(.thought != true) | .text // ""] | join("")),
                    "",
                    (.candidates[0].finishReason // ""),
                    (.error.message? // .promptFeedback.blockReason? // "")
                ] | .[] | (tostring, "\u001f")' <<< "$json_chunk")
        fi

        if [[ -n "$chunk_error" && "$chunk_error" != "null" ]]; then
            stream_error_message="API error: $chunk_error"
            api_error_occurred=true
            break
        fi

        if [[ -n "$current_sfr" && "$current_sfr" != "null" && -z "$stream_finish_reason" ]]; then
            stream_finish_reason="$current_sfr"
        fi

        if [[ "$first_chunk_received" == false && ( -n "$text_chunk" || -n "$thinking_chunk" ) ]]; then
            printf '\r\033[K%bAI:%b  ' "$COLOR_AI" "$COLOR_RESET"
            first_chunk_received=true
        fi

        if [[ -n "$thinking_chunk" ]]; then emit_thinking "$thinking_chunk"; fi

        if [[ -n "$text_chunk" ]]; then
            full_ai_response_text+="$text_chunk"
            process_text_chunk "$text_chunk"
        fi

        if [[ "$IS_OPENAI_COMPATIBLE" == false && -n "$stream_finish_reason" ]]; then
            if [[ "$stream_finish_reason" == "SAFETY" || "$stream_finish_reason" == "RECITATION" || "$stream_finish_reason" == "OTHER" ]]; then
                if [[ -z "$full_ai_response_text" ]]; then
                    stream_error_message="Stream ended (reason: $stream_finish_reason). No content."
                    api_error_occurred=true
                fi
            fi
            break
        fi
        if [[ "$PROVIDER" == "ollama" && -n "$stream_finish_reason" ]]; then break; fi
    done

    # Flush any held-back partial tag that never completed
    if [[ -n "$tag_carry" && "$is_thinking" == false ]]; then emit_answer "$tag_carry"; fi
    tag_carry=""
    end_thinking_display

    exec {STREAM_FD}<&-
    STREAM_FD=""
    if [[ -n "$CURL_PID" ]]; then kill "$CURL_PID" 2>/dev/null || true; fi
    CURL_PID=""
    trap global_int_handler INT TERM

    rm -f "$chat_curl_config"
    CURL_CONFIG_TEMP=""
    curl_stderr_content=$(cat "$CURL_STDERR_TEMP" 2>/dev/null || true)
    rm -f "$CURL_STDERR_TEMP"
    CURL_STDERR_TEMP=""

    # --- Post-stream reporting ---
    if [[ "$STREAM_INTERRUPTED" == true ]]; then
        [[ "$first_chunk_received" == false ]] && printf '\r\033[K%bAI:%b ' "$COLOR_AI" "$COLOR_RESET"
        printf '\n%b(Response interrupted)%b\n' "$COLOR_WARN" "$COLOR_RESET"
    elif [[ "$first_chunk_received" == false && -z "$stream_error_message" ]]; then
        printf '\r\033[K'
        parsed_err=""
        if [[ -n "$err_buffer" ]]; then
            parsed_err=$(jq -r '(if type=="array" then .[0] else . end)
                | if type=="object" then (.error.message? // (.error | strings)? // .message? // .detail? // empty) else empty end' \
                <<< "$err_buffer" 2>/dev/null | head -n 1)
            if [[ -z "$parsed_err" ]]; then parsed_err=$(truncate "$(tr '\n' ' ' <<< "$err_buffer")" 300); fi
        fi
        if [[ -n "$parsed_err" ]]; then
            stream_error_message="API error: $parsed_err"
            api_error_occurred=true
        elif [[ -n "$curl_stderr_content" ]]; then
            stream_error_message="API call failed. $(truncate "$curl_stderr_content" 150)"
            api_error_occurred=true
        fi
        if [[ -n "$stream_error_message" ]]; then
            printf '%bAI:%b %b%s%b\n' "$COLOR_AI" "$COLOR_RESET" "$COLOR_ERROR" "$stream_error_message" "$COLOR_RESET"
        else
            printf '%bAI:%b %b(No content in response)%b\n' "$COLOR_AI" "$COLOR_RESET" "$COLOR_INFO" "$COLOR_RESET"
        fi
    else
        printf '%b\n' "$COLOR_RESET"
        if [[ "$api_error_occurred" == true && -n "$stream_error_message" ]]; then
            printf '%b%s%b\n' "$COLOR_ERROR" "$stream_error_message" "$COLOR_RESET"
        fi
    fi

    if [[ ${#full_ai_response_text} -gt $MAX_MESSAGE_LENGTH ]]; then
        echo -e "${COLOR_WARN}Warning: response truncated (exceeded $MAX_MESSAGE_LENGTH chars)${COLOR_RESET}" >&2
        full_ai_response_text="${full_ai_response_text:0:$MAX_MESSAGE_LENGTH}"
    fi

    ai_text=$(strip_think_tags "$full_ai_response_text")

    # --- Save reply to history, or roll back the user message ---
    local_ai_message_json=""
    if [[ "$api_error_occurred" == false && -n "$ai_text" ]]; then
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
            local_ai_message_json=$(jq -n --arg text "$ai_text" '{role: "model", parts: [{text: $text}]}')
        else
            local_ai_message_json=$(jq -n --arg content "$ai_text" '{role: "assistant", content: $content}')
        fi
    fi

    if [[ -n "$local_ai_message_json" ]]; then
        chat_history+=("$local_ai_message_json")
    else
        if rollback_last_user_message; then
            echo -e "${COLOR_WARN}(Rolled back last user message)${COLOR_RESET}" >&2
        fi
    fi

    echo ""
done

echo "👋 Chat session ended."
exit 0
