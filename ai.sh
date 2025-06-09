#!/bin/bash
# Universal Chat CLI (Bash/curl/jq) - With Model Selection, HISTORY, SYSTEM PROMPT, STREAMING
# REQUIREMENTS: bash, curl, jq (must be pre-installed on the system)
# Supports: Gemini, OpenRouter, Groq, Together AI, Fireworks AI, Chutes AI, Cerebras AI, Novita AI
# To Run This Tool First Make It executable with $ chmod +x ai.sh
# Run This $ ./ai.sh provider [filter1] [filter2]... (e.g., ./ai.sh openrouter 8b)
# History, system prompt, and streaming are supported.
# Support for tool-calling in gemini google web serach with in built on off toggle.

set -e -E # Exit on error, inherit error traps

# --- Configuration ---
MAX_HISTORY_MESSAGES=20       # Keep the last N messages (user + ai). Adjust if needed.
DEFAULT_OAI_TEMPERATURE=1     # t = randomness: Higher = more creative, Lower = more predictable | allowed value 0-2
DEFAULT_OAI_MAX_TOKENS=8192   # Default max_tokens for OpenAI-compatible APIs
DEFAULT_OAI_TOP_P=0.9         # p = diversity: Higher = wider vocabulary, Lower = safer word choices | allowed value 0-1

# --- System Prompt Definition ---
# Instruct the AI to use the conversation history to maintain the ongoing task context.
# Set to "" if you want no system prompt.
SYSTEM_PROMPT="You are a helpful assistant running in a command-line interface."
# SYSTEM_PROMPT="" # Example: Disable system prompt

# --- Color Definitions --- Use 256-color
COLOR_RESET='\033[0m'
COLOR_USER='\033[38;5;81m'     # Bright cyan-blue
COLOR_AI='\033[38;5;156m'       # Soft green
COLOR_ERROR='\033[38;5;203m'    # Vivid red
COLOR_WARN='\033[38;5;221m'     # Soft yellow
COLOR_INFO='\033[38;5;159m'     # Light cyan
COLOR_BOLD='\033[1m'

##########################################################################
#                    !!! EDIT YOUR API KEYS HERE !!!                     #
#                    !!!        IMPORTANT        !!!                     #
##########################################################################
# Get keys from:
# Gemini: https://aistudio.google.com/app/apikey
# OpenRouter: https://openrouter.ai/keys
# Groq: https://console.groq.com/keys
# Together: https://api.together.ai/settings/api-keys
# Fireworks: https://fireworks.ai/api-keys
# Chutes: https://chutes.ai
# Cerebras: https://cloud.cerebras.ai/
# Novita: https://novita.ai/

GEMINI_API_KEY=""
OPENROUTER_API_KEY=""
GROQ_API_KEY=""
TOGETHER_API_KEY=""
FIREWORKS_API_KEY=""
CHUTES_API_KEY=""
CEREBRAS_API_KEY=""
NOVITA_API_KEY=""

# --- API Endpoints ---
# Chat Endpoints
GEMINI_CHAT_URL_BASE="https://generativelanguage.googleapis.com/v1beta/models/"
OPENROUTER_CHAT_URL="https://openrouter.ai/api/v1/chat/completions"
GROQ_CHAT_URL="https://api.groq.com/openai/v1/chat/completions"
TOGETHER_CHAT_URL="https://api.together.ai/v1/chat/completions"
FIREWORKS_CHAT_URL="https://api.fireworks.ai/inference/v1/chat/completions"
CHUTES_CHAT_URL="https://llm.chutes.ai/v1/chat/completions"
CEREBRAS_CHAT_URL="https://api.cerebras.ai/v1/chat/completions"
NOVITA_CHAT_URL="https://api.novita.ai/v3/openai/chat/completions"

# Model Listing Endpoints
GEMINI_MODELS_URL_BASE="https://generativelanguage.googleapis.com/v1beta/models"
OPENROUTER_MODELS_URL="https://openrouter.ai/api/v1/models"
GROQ_MODELS_URL="https://api.groq.com/openai/v1/models"
TOGETHER_MODELS_URL="https://api.together.ai/v1/models"
FIREWORKS_MODELS_URL="https://api.fireworks.ai/inference/v1/models"
CHUTES_MODELS_URL="https://llm.chutes.ai/v1/models"
CEREBRAS_MODELS_URL="https://api.cerebras.ai/v1/models"
NOVITA_MODELS_URL="https://api.novita.ai/v3/openai/models"

# --- Helper Functions ---

function print_usage() {
  echo -e ""
  echo -e "${COLOR_INFO}Usage: $0 <provider> [filter1] [filter2]...${COLOR_RESET}"
  echo -e ""
  echo -e "${COLOR_INFO}Description:${COLOR_RESET}"
  echo -e "  Starts an interactive chat session with the specified AI provider."
  echo -e "  You can add optional filter words (e.g., '8b', 'free', '32k') to narrow down the model list."
  echo -e "  It will fetch available models and let you choose one by number."
  echo -e ""
  echo -e "${COLOR_INFO}Supported Providers:${COLOR_RESET}"
  echo -e "  gemini, openrouter, groq, together, fireworks, chutes, cerebras, novita"
  echo -e ""
  echo -e "${COLOR_INFO}Note on Gemini Tool Calling:${COLOR_RESET}"
  echo -e "  If you select 'gemini' as the provider, you will be asked if you wish to"
  echo -e "  enable built-in online tool calling (web search, URL context)."
  echo -e "  This feature is only available for Gemini models."
  echo -e ""
  echo -e "${COLOR_INFO}Finding Model Identifiers (if needed manually):${COLOR_RESET}"
  echo -e "    ${COLOR_USER}Gemini:${COLOR_RESET}     https://ai.google.dev/models/gemini (Use 'Model name')"
  echo -e "    ${COLOR_USER}OpenRouter:${COLOR_RESET} https://openrouter.ai/models (Includes OpenAI, Anthropic, etc.)"
  echo -e "    ${COLOR_USER}Groq:${COLOR_RESET}       https://console.groq.com/docs/models"
  echo -e "    ${COLOR_USER}Together:${COLOR_RESET}   https://docs.together.ai/docs/inference-models (Look for Model ID/Name)"
  echo -e "    ${COLOR_USER}Fireworks:${COLOR_RESET}  https://fireworks.ai/models (Look for API ID, often like 'accounts/fireworks/models/...')"
  echo -e "    ${COLOR_USER}Chutes:${COLOR_RESET}     https://chutes.ai (Check their model documentation or use the script's model list)"
  echo -e "    ${COLOR_USER}Cerebras:${COLOR_RESET}   https://cloud.cerebras.ai (Check their model documentation or use the script's model list)"
  echo -e "    ${COLOR_USER}Novita:${COLOR_RESET}     https://docs.novita.ai (Check their model documentation or use the script's model list)"
  echo -e ""
  echo -e "${COLOR_INFO}Example Commands:${COLOR_RESET}"
  echo -e "  ${COLOR_AI}$0 gemini${COLOR_RESET}"
  echo -e "  ${COLOR_AI}$0 groq free${COLOR_RESET}                 # Filter for models containing 'free'"
  echo -e "  ${COLOR_AI}$0 openrouter 8b${COLOR_RESET}               # Filter for models containing '8b'"
  echo -e "  ${COLOR_AI}$0 together mixtral 32k${COLOR_RESET}        # Filter for models with 'mixtral' AND '32k'"
  echo -e "${COLOR_WARN}NOTE: Ensure API keys are set inside the script before running!${COLOR_RESET}"
}

# Checks if API key looks like a placeholder
function check_placeholder_key() {
    local key_value="$1"
    local provider_name="$2"
    local placeholder_found=false
    local message=""

    if [[ -z "$key_value" ]]; then
        placeholder_found=true
        message="is empty"
    elif [[ "$key_value" == "YOUR_"* ]] || \
         [[ "$key_value" == *"-HERE" ]] || \
         [[ "$key_value" == *"..." ]]; then
        placeholder_found=true
        message="appears to be a generic placeholder"
    elif [[ "$provider_name" == "gemini" && "$key_value" == "-" ]]; then
         placeholder_found=true
         message="is the default placeholder ('-')"
    elif [[ "$provider_name" == "openrouter" && "$key_value" == "sk-or-v1-" ]]; then
        placeholder_found=true
        message="is the default OpenRouter prefix placeholder"
     elif [[ "$provider_name" == "groq" && "$key_value" == "gsk_"* && ${#key_value} -lt 10 ]]; then # Catches incomplete Groq key
        placeholder_found=true
        message="appears to be an incomplete Groq key (starts with gsk_ but is too short)"
    # Note: Empty "" GROQ_API_KEY is caught by initial -z check
    elif [[ "$provider_name" == "fireworks" && "$key_value" == "fw-"* && ${#key_value} -lt 10 ]]; then # Catches incomplete Fireworks key
         placeholder_found=true
         message="appears to be an incomplete Fireworks key (starts with fw- but is too short)"
    # Note: Empty "" FIREWORKS_API_KEY is caught by initial -z check
    elif [[ "$provider_name" == "chutes" && "$key_value" == ".." ]]; then # Specific check for ".."
        placeholder_found=true
        message="is the default placeholder ('..')"
    elif [[ "$provider_name" == "cerebras" && "$key_value" == "csk-" ]]; then # Specific check for "csk-"
        placeholder_found=true
        message="is the default Cerebras prefix placeholder ('csk-')"
    elif [[ "$provider_name" == "novita" && ${#key_value} -lt 10 ]]; then # Catches incomplete Novita key
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
function truncate() {
    local s="$1"
    local max_chars="$2"
    if [[ ${#s} -gt $max_chars ]]; then
        echo "${s:0:$((max_chars-3))}...";
    else
        echo "$s";
    fi
}

# --- Argument Parsing ---
if [ "$#" -lt 1 ]; then
    echo -e "${COLOR_ERROR}Whoops: Follow the guide.${COLOR_RESET}" >&2
    print_usage
    exit 1
fi
PROVIDER=$(echo "$1" | tr '[:upper:]' '[:lower:]')
filters=("${@:2}") # Capture all arguments from the second one onwards as filters

# --- Dependency Check ---
if ! command -v curl &> /dev/null || ! command -v jq &> /dev/null; then
    echo -e "${COLOR_ERROR}Error: 'curl' and 'jq' are required. Please install them.${COLOR_RESET}" >&2
    exit 1
fi

# --- Global Tool Calling Flag (set interactively for Gemini) ---
ENABLE_TOOL_CALLING=false

# --- Get API Key and Check Placeholders ---
API_KEY=""
case "$PROVIDER" in
    gemini)     API_KEY="$GEMINI_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER" ;;
    openrouter) API_KEY="$OPENROUTER_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER" ;;
    groq)       API_KEY="$GROQ_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER" ;;
    together)   API_KEY="$TOGETHER_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER" ;;
    fireworks)  API_KEY="$FIREWORKS_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER" ;;
    chutes)     API_KEY="$CHUTES_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER" ;;
    cerebras)   API_KEY="$CEREBRAS_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER" ;;
    novita)     API_KEY="$NOVITA_API_KEY"; check_placeholder_key "$API_KEY" "$PROVIDER" ;;
    *)
        echo -e "${COLOR_ERROR}Error: Unknown provider '$PROVIDER'. Choose from: gemini, openrouter, groq, together, fireworks, chutes, cerebras, novita${COLOR_RESET}" >&2
        print_usage
        exit 1
        ;;
esac
key_check_status=$?

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
        JQ_QUERY='. | sort_by(.id) | .[].id' # Together AI lists models in a root array
        ;;
    fireworks)
        MODELS_URL="$FIREWORKS_MODELS_URL"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='.data[]? | select(.type == "chat_completion" or .supports_chat == true) | .id' # Updated for Fireworks flexibility
        ;;
    chutes)
        MODELS_URL="$CHUTES_MODELS_URL"
        MODELS_AUTH_HEADER="Authorization: Bearer ${API_KEY}"
        JQ_QUERY='.data | sort_by(.id) | .[].id'
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
esac

model_curl_args=(-sS -L -X GET "$MODELS_URL") # Added -S to show curl errors
[ -n "$MODELS_AUTH_HEADER" ] && model_curl_args+=(-H "$MODELS_AUTH_HEADER")
[ ${#MODELS_EXTRA_HEADERS[@]} -gt 0 ] && model_curl_args+=("${MODELS_EXTRA_HEADERS[@]}")

model_list_json=$(curl "${model_curl_args[@]}")
model_list_exit_code=$?

if [ $model_list_exit_code -ne 0 ]; then
    echo -e "${COLOR_ERROR}Error fetching models: curl command failed (Exit code: $model_list_exit_code).${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}Check network connection, API key validity/permissions, and endpoint ($MODELS_URL).${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}Raw response/error (if any from curl): $(truncate "$model_list_json" 200)${COLOR_RESET}" >&2
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

jq_stderr_output=""
mapfile -t available_models < <(jq -r "$JQ_QUERY" <<< "$model_list_json" 2> >(jq_stderr_output=$(cat); cat >&2))
jq_exit_code=$?

if [ $jq_exit_code -ne 0 ]; then
    echo -e "${COLOR_ERROR}Error: Failed to parse API response for provider '$PROVIDER'.${COLOR_RESET}" >&2
    echo -e "${COLOR_INFO}The API call succeeded, but the JQ query ('${COLOR_BOLD}$JQ_QUERY${COLOR_RESET}') might not match the response structure or jq itself failed.${COLOR_RESET}" >&2
    if [[ -n "$jq_stderr_output" ]]; then
      echo -e "${COLOR_ERROR}JQ Error Output:${COLOR_RESET}\n$jq_stderr_output" >&2
    fi
    echo -e "${COLOR_INFO}Raw API response (first 500 chars):${COLOR_RESET}" >&2
    echo "${model_list_json:0:500}" >&2
    exit 1
fi

# --- Filter models based on additional arguments ---
if [ ${#filters[@]} -gt 0 ]; then
    echo -e "${COLOR_INFO}Filtering models with terms: ${filters[*]}${COLOR_RESET}"
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
            if [[ ! "$model_lower" == *"$filter_lower"* ]]; then
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
    echo -e "${COLOR_ERROR}No models available." >&2
    if [ ${#filters[@]} -gt 0 ]; then
        echo -e "${COLOR_WARN}Your filter criteria (${filters[*]}) did not match any models from provider '${PROVIDER^^}'.${COLOR_RESET}" >&2
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

case "$PROVIDER" in
    gemini)
        # Use streamGenerateContent with alt=sse for Server-Sent Events
        CHAT_API_URL="${GEMINI_CHAT_URL_BASE}${MODEL_ID}:streamGenerateContent?key=${API_KEY}&alt=sse"
        IS_OPENAI_COMPATIBLE=false # Gemini uses "model" role, not "assistant"

        # --- Interactive prompt for tool calling for Gemini ---
        echo ""
        tool_choice_input=""
        while true; do
            read -r -p "$(echo -e "${COLOR_INFO}Do you want to enable online tool calling (web search, URL context) for Gemini? (y/n, 1/0): ${COLOR_RESET}")" tool_choice_input
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
        ;;
    openrouter|groq|together|fireworks|chutes|cerebras|novita)
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
            fireworks)  CHAT_API_URL="$FIREWORKS_CHAT_URL" ;;
            chutes)     CHAT_API_URL="$CHUTES_CHAT_URL" ;;
            cerebras)   CHAT_API_URL="$CEREBRAS_CHAT_URL" ;;
            novita)     CHAT_API_URL="$NOVITA_CHAT_URL" ;;
        esac
        ;;
esac

declare -a chat_history=()

if [[ -n "$SYSTEM_PROMPT" ]]; then
    system_message_json=""
    if [[ "$IS_OPENAI_COMPATIBLE" == true ]]; then # OpenAI compatible system prompt
        system_message_json=$(jq -n --arg content "$SYSTEM_PROMPT" '{role: "system", content: $content}')
    fi
    if [[ -n "$system_message_json" ]]; then
        chat_history+=("$system_message_json")
    elif [[ "$IS_OPENAI_COMPATIBLE" == false && -n "$SYSTEM_PROMPT" ]]; then
        # For Gemini, system prompt is handled by prepending to first user message.
        : # No object added to chat_history for Gemini's system prompt here.
    elif [[ -n "$SYSTEM_PROMPT" ]]; then # Failed to create JSON for OAI
        echo -e "${COLOR_WARN}Warning: Failed to create system prompt JSON (jq error?). System prompt may not be active.${COLOR_RESET}" >&2
    fi
fi


echo -e "--- ${COLOR_INFO}Starting Chat${COLOR_RESET} ---"
echo -e "${COLOR_INFO}Provider:${COLOR_RESET}      ${PROVIDER^^}"
echo -e "${COLOR_INFO}Model:${COLOR_RESET}         ${MODEL_ID}"
echo -e "${COLOR_INFO}History Limit:${COLOR_RESET} Last $MAX_HISTORY_MESSAGES messages (user+AI)"
if [[ "$IS_OPENAI_COMPATIBLE" == true ]]; then
    echo -e "${COLOR_INFO}Temp/MaxTokens/TopP (OAI):${COLOR_RESET} $DEFAULT_OAI_TEMPERATURE / $DEFAULT_OAI_MAX_TOKENS / $DEFAULT_OAI_TOP_P"
fi

if [[ -n "$SYSTEM_PROMPT" ]]; then
     if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then
        echo -e "${COLOR_INFO}System Prompt:${COLOR_RESET}   Set (will be prepended to first user message for Gemini)"
     elif [[ ${#chat_history[@]} -gt 0 && "$(echo "${chat_history[0]}" | jq -r .role 2>/dev/null)" == "system" ]]; then
        echo -e "${COLOR_INFO}System Prompt:${COLOR_RESET}   Active (OpenAI-compatible format)"
     else
         echo -e "${COLOR_WARN}System Prompt:${COLOR_RESET}   Set but seems inactive in history array (OpenAI specific).${COLOR_RESET}"
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

echo -e "Enter your prompt below. Type ${COLOR_BOLD}'quit'${COLOR_RESET} or ${COLOR_BOLD}'exit'${COLOR_RESET} to end session."
echo -e "----------------------------------------"

first_user_message=true

while true; do
    if [[ -t 0 && $- == *i* ]] && builtin command -v read -e &> /dev/null; then
         # Interactive mode with readline support
         read -r -e -p "$(echo -e "${COLOR_USER}You:${COLOR_RESET} ")" user_input
         # Add to shell history if input is not empty
         [[ -n "$user_input" ]] && history -s "$user_input"
    else
         # Non-interactive or no readline support
         read -r -p "$(echo -e "${COLOR_USER}You:${COLOR_RESET} ")" user_input
    fi


    if [[ "$user_input" == "quit" || "$user_input" == "exit" ]]; then
        echo "Exiting chat."
        break
    fi
    if [[ -z "$user_input" ]]; then
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
        user_message_json=$(jq -n --arg text "$user_prompt_text" \
            '{role: "user", parts: [{text: $text}]}')
    else # OpenAI-Compatible
        user_message_json=$(jq -n --arg content "$user_prompt_text" \
            '{role: "user", content: $content}')
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
    # Total allowed entries depends on whether there is a system message at history[0]
    # If system_offset is 1, history size can be MAX_HISTORY_MESSAGES + 1
    # If system_offset is 0, history size can be MAX_HISTORY_MESSAGES
    effective_max_history_entries=$(( allowed_conversational_messages + system_offset ))

    if [[ $current_history_size -gt $effective_max_history_entries ]]; then
        elements_to_remove=$((current_history_size - effective_max_history_entries))
        if [[ $system_offset -eq 1 ]]; then # Keep system prompt, remove from user/AI messages
            chat_history=("${chat_history[0]}" "${chat_history[@]:(1 + elements_to_remove)}")
        else # No system prompt, remove from the beginning
            chat_history=("${chat_history[@]:$elements_to_remove}")
        fi
    fi


    history_json_array=$(printf '%s\n' "${chat_history[@]}" | jq -sc 'map(select(. != null))')
    if [[ -z "$history_json_array" || "$history_json_array" == "null" || "$history_json_array" == "[]" ]]; then
        echo -e "${COLOR_ERROR}Error: Failed to create valid JSON array from history. Rolling back last user message.${COLOR_RESET}" >&2
        if [ ${#chat_history[@]} -gt 0 ]; then
             last_idx=$(( ${#chat_history[@]} - 1 ))
             # Ensure it's a user message we're removing (the one just added)
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
            --arg temperature_str "1.0" \
            --arg max_tokens_str "4096" \
            '{
                contents: $contents,
                generationConfig: {
                    temperature: ($temperature_str | tonumber),
                    maxOutputTokens: ($max_tokens_str | tonumber)
                }
            }'
        )
        # Conditionally add the tools array if ENABLE_TOOL_CALLING is true
        if [[ "$ENABLE_TOOL_CALLING" == true ]]; then
            json_payload=$(echo "$json_payload" | jq '. + {tools: [{"urlContext": {}}, {"googleSearch": {}}]}')
        fi
    else # OpenAI-Compatible payload (add stream:true)
         json_payload=$(jq -n \
            --arg model "$MODEL_ID" \
            --argjson messages "$history_json_array" \
            --arg temperature_str "$DEFAULT_OAI_TEMPERATURE" \
            --arg max_tokens_str "$DEFAULT_OAI_MAX_TOKENS" \
            --arg top_p_str "$DEFAULT_OAI_TOP_P" \
            '{
                model: $model,
                messages: $messages,
                temperature: ($temperature_str | tonumber),
                max_tokens: ($max_tokens_str | tonumber),
                top_p: ($top_p_str | tonumber),
                stream: true
             }'
            )
    fi

    if [ -z "$json_payload" ]; then
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

    echo -n -e "\r${COLOR_AI}AI:${COLOR_RESET} ${COLOR_INFO}(Waiting for stream...)${COLOR_RESET}"

    # Base curl arguments for chat
    base_chat_curl_args=(-sS -L -N -X POST "$CHAT_API_URL" -H "Content-Type: application/json" -H "Accept: application/json") # -N for stream, -S for curl errors
    [ -n "$CHAT_AUTH_HEADER" ] && base_chat_curl_args+=(-H "$CHAT_AUTH_HEADER")
    [ ${#CHAT_EXTRA_HEADERS[@]} -gt 0 ] && base_chat_curl_args+=("${CHAT_EXTRA_HEADERS[@]}")
    base_chat_curl_args+=(-d "$json_payload")

    # --- DEBUG ---
    # echo -e "\n${COLOR_WARN}DEBUG: Payload for ${PROVIDER^^}:${COLOR_RESET}"
    # echo "$json_payload" | jq .
    # echo -e "${COLOR_WARN}DEBUG: Curl Command:${COLOR_RESET}"
    # printf "curl"; printf " '%s'" "${base_chat_curl_args[@]}"; echo -e "\n"
    # --- END DEBUG ---

    full_ai_response_text=""
    local_ai_message_json="" # For this turn's AI response
    api_error_occurred=false
    stream_error_message=""
    stream_finish_reason=""
    first_chunk_received=false

    curl_stderr_temp=$(mktemp)

    # Process substitution to read from curl's stdout line by line
    exec 3< <(curl "${base_chat_curl_args[@]}" 2>"$curl_stderr_temp")

    while IFS= read -r line <&3; do
        # OpenAI specific stream end marker (often followed by a final data packet with finish_reason)
        # Check if this is the OpenAI stream end marker for graceful exit from loop
        if [[ "$line" == "data: [DONE]" && "$IS_OPENAI_COMPATIBLE" == true ]]; then
            # The actual final chunk with finish_reason might still come after or before this.
            # This is more of a signal that no more *content* deltas will come.
            # Some APIs might send data: [DONE] and then close, others might send a final metadata chunk.
            # The loop should naturally end when curl exits anyway if [DONE] is the very last thing.
            # For robustness, if we see [DONE], we can assume stream is effectively over.
            break
        fi

        if [[ "$line" == "data: "* ]]; then
            json_chunk="${line#data: }"
            # Handle case where data line is empty (SSE keep-alive ping typically)
            if [[ -z "$json_chunk" ]]; then continue; fi

            if ! echo "$json_chunk" | jq empty 2>/dev/null ; then
                # Potentially log malformed JSON if verbose debugging is on, but generally skip.
                # echo -e "\n${COLOR_WARN}Warning: Malformed JSON data in stream: $(truncate "$json_chunk" 50)${COLOR_RESET}" >&2
                continue # Skip malformed chunk
            fi

            # Universal error check in SSE data (e.g. OAI .error, Gemini .error in the JSON payload)
            chunk_error=$(echo "$json_chunk" | jq -r '.error.message // .error // .detail // empty') # Check common error paths
            if [[ -n "$chunk_error" && "$chunk_error" != "null" ]]; then
                stream_error_message="API Error in stream: $chunk_error"
                api_error_occurred=true; break
            fi

            # Gemini specific blocking check (inside SSE data object's promptFeedback)
            if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then # Gemini
                block_reason=$(echo "$json_chunk" | jq -r '.promptFeedback.blockReason // empty')
                if [[ -n "$block_reason" && "$block_reason" != "null" ]]; then
                    stream_error_message="Content blocked by API (Reason: $block_reason)"
                    # Try to get partial text if any was sent before block_reason was emitted in this chunk.
                    partial_text_block=$(echo "$json_chunk" | jq -r '.candidates[0].content.parts[0].text // empty')
                    if [[ -n "$partial_text_block" ]]; then
                         if [[ "$first_chunk_received" == false ]]; then
                            echo -ne "\r\033[K"; echo -n -e "${COLOR_AI}AI:${COLOR_RESET}  ${COLOR_AI}"
                            first_chunk_received=true
                        fi
                        echo -n "$partial_text_block"; full_ai_response_text+="$partial_text_block"
                    fi
                    api_error_occurred=true; break
                fi
            fi

            # Extract text chunk and finish reason based on provider type
            text_chunk=""; current_sfr="" # current_stream_finish_reason
            if [[ "$IS_OPENAI_COMPATIBLE" == true ]]; then
                text_chunk=$(echo "$json_chunk" | jq -r '.choices[0].delta.content // empty')
                current_sfr=$(echo "$json_chunk" | jq -r '.choices[0].finish_reason // empty')
            else # Gemini (using alt=sse)
                text_chunk=$(echo "$json_chunk" | jq -r '.candidates[0].content.parts[0].text // empty')
                current_sfr=$(echo "$json_chunk" | jq -r '.candidates[0].finishReason // empty') # Sometimes in .candidates[0].finishReason
                 # Also check if finishReason is within a candidate's safetyRatings for some Gemini responses
                if [[ -z "$current_sfr" || "$current_sfr" == "null" ]]; then
                     current_sfr=$(echo "$json_chunk" | jq -r '.candidates[0].safetyRatings[]? | select(.blocked == true) | .category // empty' | head -n 1)
                     if [[ -n "$current_sfr" && "$current_sfr" != "null" ]]; then current_sfr="SAFETY"; fi # Normalize safety block to "SAFETY"
                fi

                # Check for tool calls in Gemini response IF tool calling is enabled
                if [[ "$ENABLE_TOOL_CALLING" == true ]]; then
                    tool_call_parts=$(echo "$json_chunk" | jq -c '.candidates[0].content.parts[] | select(.functionCall != null) // empty')
                    if [[ -n "$tool_call_parts" ]]; then
                        # This script currently doesn't execute tool calls.
                        # You would need to add logic here to parse and potentially execute the tool call.
                        # For now, we'll just log it.
                        if [[ "$first_chunk_received" == false ]]; then
                            echo -ne "\r\033[K"; echo -n -e "${COLOR_AI}AI:${COLOR_RESET}  ${COLOR_AI}"
                            first_chunk_received=true
                        fi
                        echo -e "\n${COLOR_WARN}AI requested tool call:${COLOR_RESET}" >&2
                        echo "$tool_call_parts" | jq . >&2 # Pretty print the tool call
                        echo -e "${COLOR_WARN}(This script does not automatically execute tool calls or return tool output to the model.)\n${COLOR_RESET}" >&2
                        # Do not append tool call JSON to full_ai_response_text, as it's not "text"
                        # For a true tool-using agent, you'd feed this tool output back to the model.
                    fi
                fi # End check for ENABLE_TOOL_CALLING for Gemini
            fi # End Gemini specific text/finish reason/tool call extraction

            # Store the first non-null finish reason encountered
            if [[ -n "$current_sfr" && "$current_sfr" != "null" && ( -z "$stream_finish_reason" || "$stream_finish_reason" == "null" ) ]]; then
                 stream_finish_reason="$current_sfr";
            fi

            # UI update for first received chunk
            if [[ "$first_chunk_received" == false && -n "$text_chunk" ]]; then
                echo -ne "\r\033[K"; echo -n -e "${COLOR_AI}AI:${COLOR_RESET}  ${COLOR_AI}" # Clear "Waiting..." and print AI prefix
                first_chunk_received=true
            fi
            # Print and accumulate text
            if [[ -n "$text_chunk" ]]; then
                echo -n "$text_chunk"; full_ai_response_text+="$text_chunk"
            fi

            # For Gemini, any non-null finishReason usually means the end of its content stream for that turn.
            if [[ "$IS_OPENAI_COMPATIBLE" == false && -n "$stream_finish_reason" && "$stream_finish_reason" != "null" ]]; then
                if [[ "$stream_finish_reason" == "SAFETY" || "$stream_finish_reason" == "RECITATION" || "$stream_finish_reason" == "OTHER" ]]; then
                     if [[ -z "$text_chunk" && -z "$full_ai_response_text" ]]; then # No text in this final chunk AND no prior text from this response
                         stream_error_message="Stream ended by API (Finish Reason: $stream_finish_reason). No content generated."
                         api_error_occurred=true; # Treat as hard error if no content came with it
                     elif [[ -z "$text_chunk" ]]; then # Some text already received, then a bad finish reason with no final text
                         stream_error_message="(Stream truncated by API. Finish Reason: $stream_finish_reason)"
                         # Not necessarily a 'hard' api_error_occurred for history, as partial content is useful
                     fi
                fi
                break # Gemini signals end effectively with a finish_reason in a data packet.
            fi
        # else: ignore non "data: " lines (comments, empty lines, :ping in SSE stream)
        fi
    done <&3
    exec 3<&- # Close file descriptor

    curl_stderr_content=$(cat "$curl_stderr_temp" 2>/dev/null)
    rm -f "$curl_stderr_temp"

    # --- Post-stream processing and UI update ---
    if [[ "$first_chunk_received" == false && -z "$stream_error_message" ]]; then # No data chunks ever received AND no stream error already flagged
        echo -ne "\r\033[K" # Clear "Waiting..." line
        if [[ -n "$curl_stderr_content" ]]; then # Curl itself failed or wrote to stderr
            stream_error_message="API call failed. $(truncate "$curl_stderr_content" 150)"
            api_error_occurred=true
            echo -e "${COLOR_AI}AI:${COLOR_RESET} ${COLOR_ERROR}$stream_error_message${COLOR_RESET}"
        else # No data, no curl error, no stream error. API just closed connection or sent empty stream without [DONE] or error JSON.
            echo -e "${COLOR_AI}AI:${COLOR_RESET} ${COLOR_INFO}(No content in response or empty stream ended prematurely)${COLOR_RESET}"
            # full_ai_response_text is already empty. This is not an "error" for history rollback unless desired to treat as such.
        fi
    elif [[ "$first_chunk_received" == true ]]; then # At least one chunk was received and printed
        echo -e "${COLOR_RESET}" # Reset color and ensure newline after the streamed AI response (AI color already active)
        if [[ "$api_error_occurred" == true && -n "$stream_error_message" ]]; then
            # Error occurred mid-stream or at the end after some content was printed
            echo -e "${COLOR_ERROR}$stream_error_message${COLOR_RESET}"
        else
            # Check and display noteworthy finish reasons if no explicit stream_error_message took precedence
            declare -A normal_finish_reasons=(
                ["stop"]=1 ["done"]=1 ["length"]=1 # Common OAI
                ["STOP"]=1 ["MAX_TOKENS"]=1 ["MODEL_LENGTH"]=1 # Common Gemini & others
                # "null" can be normal if API just ends stream without explicit reason and content is there
            )
            if [[ -n "$stream_finish_reason" && "$stream_finish_reason" != "null" && -z "${normal_finish_reasons[$stream_finish_reason]}" ]]; then
                echo -e "${COLOR_WARN}(Finish Reason: $stream_finish_reason)${COLOR_RESET}"
            fi
        fi
    elif [[ "$api_error_occurred" == true && -n "$stream_error_message" ]]; then # Error was flagged, but no chunks ever printed (e.g. immediate JSON error from API)
        echo -ne "\r\033[K" # Clear "Waiting..."
        echo -e "${COLOR_AI}AI:${COLOR_RESET} ${COLOR_ERROR}$stream_error_message${COLOR_RESET}"
    # else: Stream ended, no data, no specific stream error message set -- means likely clean empty stream end if not caught above
    fi

    ai_text="$full_ai_response_text" # Final accumulated text from stream

    # Create AI message JSON for history
    if [[ "$api_error_occurred" == false && -n "$ai_text" ]]; then # Only add to history if no error and text exists
        if [[ "$IS_OPENAI_COMPATIBLE" == false ]]; then # Gemini
            local_ai_message_json=$(jq -n --arg text "$ai_text" '{role: "model", parts: [{text: $text}]}')
        else # OpenAI-Compatible
            local_ai_message_json=$(jq -n --arg content "$ai_text" '{role: "assistant", content: $content}')
        fi
        if ! echo "$local_ai_message_json" | jq empty 2>/dev/null; then # Check if jq failed
            echo -e "${COLOR_WARN}Warning: Internal error creating AI message JSON for history. AI response not added to history.${COLOR_RESET}" >&2
            local_ai_message_json="" # Invalidate it
        fi
    else # Error occurred, or AI returned no text (even if not an "error", empty response isn't useful history)
        local_ai_message_json=""
    fi

    # Add AI's message to history OR rollback user's last message if AI failed/gave nothing
    if [[ -n "$local_ai_message_json" ]]; then
         chat_history+=("$local_ai_message_json")
    else
        # Error occurred, or AI returned no text. Roll back last user message.
        # (The $api_error_occurred || -z "$ai_text" condition)
        if [[ $current_history_size -gt 0 && "$full_ai_response_text" != *"Content blocked by API"* ]]; then # Don't show rollback message if content was blocked, already messaged.
            # Avoid rolling back if initial prompt was just a system context that didn't expect a response for history
            last_idx_before_ai_response=$(( ${#chat_history[@]} - 1 ))
            last_role_check=$(echo "${chat_history[$last_idx_before_ai_response]}" | jq -r .role 2>/dev/null)

            if [[ "$last_role_check" == "user" ]]; then # Check if the last entry *is* indeed the user message we mean to roll back
                 echo -e "${COLOR_WARN}(Rolling back last user message from history due to error or no AI response text)${COLOR_RESET}" >&2
                 unset 'chat_history[$last_idx_before_ai_response]'
                 chat_history=("${chat_history[@]}") # Re-index array to remove gap
            fi
        fi
    fi
    echo "" # Ensure a blank line before next user prompt input
done

echo "Chat session ended."
exit 0
