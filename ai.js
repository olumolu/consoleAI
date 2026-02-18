#!/usr/bin/env node
/**
 * Universal Chat CLI (Node.js) - With Model Selection, HISTORY, SYSTEM PROMPT, STREAMING, IMAGE SUPPORT, THINKING OUTPUT
 * REQUIREMENTS: Node.js v18+ (for native fetch)
 * Supports: Gemini, OpenRouter, Groq, Together AI, Cerebras AI, Novita AI, Ollama Cloud
 *
 * Usage: ./ai.js provider [filter]...
 * Example: ./ai.js openrouter 32b
 *
 * Commands inside chat:
 *  /history             - Show conversation history
 *  /save <name>         - Save current session
 *  /load <name>         - Load saved session
 *  /clear               - Clear all saved sessions
 *  /upload <path>       - Attach image
 *  /image               - Show attached image
 *  /clearimage          - Remove attached image
 *  /togglethinking      - Toggle thinking output display
 *  quit / exit          - Exit chat
 */

const fs = require('fs');
const path = require('path');
const readline = require('readline');

// --- Configuration ---
const CONFIG = {
    MAX_HISTORY_MESSAGES: 20,       // Keep the last N messages (user + ai). Adjust if needed.
    MAX_MESSAGE_LENGTH: 50000,      // Maximum length for a single message
    DEFAULT_OAI_TEMPERATURE: 0.7,   // t = randomness: Higher = more creative, Lower = more predictable | allowed value 0-2
    DEFAULT_OAI_MAX_TOKENS: 3000,   // Default max_tokens for OpenAI-compatible APIs
    DEFAULT_OAI_TOP_P: 0.9,         // p = diversity: Higher = wider vocabulary, Lower = safer word choices | allowed value 0-1
    SESSION_DIR: path.join(process.env.HOME || process.env.USERPROFILE || '.', '.chat_sessions'),
    MAX_IMAGE_SIZE_MB: 20,
    SUPPORTED_IMAGE_TYPES: ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
};

// --- System Prompt Definition ---
const SYSTEM_PROMPT = "You are a helpful assistant running in a command-line interface.";
// const SYSTEM_PROMPT = ""; // Example: Disable system prompt

// --- Thinking Output Configuration ---
let ENABLE_THINKING_OUTPUT = true;

// --- Color Definitions --- Use 256-color
const C = {
    RESET:  '\x1b[0m',
    USER:   '\x1b[38;5;199m',     // Bright Magenta
    AI:     '\x1b[38;5;40m',      // Bright green
    THINK:  '\x1b[38;5;214m',     // Soft orange
    ERROR:  '\x1b[38;5;203m',     // Vivid red
    WARN:   '\x1b[38;5;221m',     // Soft yellow
    INFO:   '\x1b[38;5;75m',      // Darker cyan-blue
    BOLD:   '\x1b[1m',
    IMAGE:  '\x1b[38;5;208m',     // Orange
    CLR:    '\x1b[2K\r'           // Clear current line
};

//////////////////////////////////////////////////////////////////////////
//                    !!! EDIT YOUR API KEYS HERE !!!                    //
//                    !!!        IMPORTANT        !!!                    //
//////////////////////////////////////////////////////////////////////////
const API_KEYS = {
    GEMINI:     "",  // https://aistudio.google.com/app/apikey
    OPENROUTER: "",  // https://openrouter.ai/keys
    GROQ:       "",  // https://console.groq.com/keys
    TOGETHER:   "",  // https://api.together.ai/settings/api-keys
    CEREBRAS:   "",  // https://cloud.cerebras.ai/
    NOVITA:     "",  // https://novita.ai/
    OLLAMA:     ""   // https://ollama.com/ (Optional for local)
};

// --- API Endpoints ---
const ENDPOINTS = {
    GEMINI: {
        CHAT_BASE: "https://generativelanguage.googleapis.com/v1beta/models/",
        MODELS:    "https://generativelanguage.googleapis.com/v1beta/models"
    },
    OPENROUTER: {
        CHAT:   "https://openrouter.ai/api/v1/chat/completions",
        MODELS: "https://openrouter.ai/api/v1/models"
    },
    GROQ: {
        CHAT:   "https://api.groq.com/openai/v1/chat/completions",
        MODELS: "https://api.groq.com/openai/v1/models"
    },
    TOGETHER: {
        CHAT:   "https://api.together.ai/v1/chat/completions",
        MODELS: "https://api.together.ai/v1/models"
    },
    CEREBRAS: {
        CHAT:   "https://api.cerebras.ai/v1/chat/completions",
        MODELS: "https://api.cerebras.ai/v1/models"
    },
    NOVITA: {
        CHAT:   "https://api.novita.ai/v3/openai/chat/completions",
        MODELS: "https://api.novita.ai/v3/openai/models"
    },
    OLLAMA: {
        CHAT:   "https://ollama.com/api/chat",
        // CHAT: "http://localhost:11434/api/chat",  // Uncomment for local Ollama
        MODELS: "https://ollama.com/api/tags"
        // MODELS: "http://localhost:11434/api/tags" // Uncomment for local Ollama
    }
};

// --- Global State ---
let chatHistory = [];
let currentImage = { path: "", base64: "", mime: "" };
let firstUserMessage = true;
let enableToolCalling = false;

// ========================================================================
//                          HELPER FUNCTIONS
// ========================================================================

/** Validate a numeric config value is within [min, max] */
function validateNumeric(value, min, max, name) {
    if (typeof value !== 'number' || isNaN(value)) {
        console.error(`${C.ERROR}Error: ${name} must be a number, got: ${value}${C.RESET}`);
        process.exit(1);
    }
    if (value < min || value > max) {
        console.error(`${C.ERROR}Error: ${name} must be between ${min} and ${max}, got: ${value}${C.RESET}`);
        process.exit(1);
    }
}

/** Truncates a string to a max length, adding ellipsis */
function truncate(str, maxChars) {
    if (!str) return "";
    return str.length > maxChars ? str.substring(0, maxChars - 3) + "..." : str;
}

/** Remove think tags from text (for history storage) */
function stripThinkTags(text) {
    if (!text) return "";
    let result = "";
    let remaining = text;

    while (remaining.length > 0) {
        const thinkStart = remaining.indexOf("<think");
        if (thinkStart !== -1) {
            result += remaining.substring(0, thinkStart);
            remaining = remaining.substring(thinkStart);
            // Skip past the opening tag
            const openEnd = remaining.indexOf(">");
            if (openEnd !== -1) {
                remaining = remaining.substring(openEnd + 1);
            }
            // Find closing tag
            const closeStart = remaining.indexOf("</think");
            if (closeStart !== -1) {
                remaining = remaining.substring(closeStart);
                const closeEnd = remaining.indexOf(">");
                if (closeEnd !== -1) {
                    remaining = remaining.substring(closeEnd + 1);
                }
            } else {
                // No closing tag, assume rest is thinking. Drop it for history.
                break;
            }
        } else {
            result += remaining;
            break;
        }
    }
    return result;
}

/** Validate session name - only allow alphanumeric, dash, underscore */
function validateSessionName(name) {
    if (!/^[a-zA-Z0-9_-]+$/.test(name)) {
        console.error(`${C.ERROR}Error: Session name can only contain letters, numbers, dash and underscore.${C.RESET}`);
        return false;
    }
    if (name.length > 100) {
        console.error(`${C.ERROR}Error: Session name is too long (max 100 characters).${C.RESET}`);
        return false;
    }
    return true;
}

/** Checks if API key looks like a placeholder */
function checkPlaceholderKey(keyValue, providerName) {
    let placeholderFound = false;
    let message = "";

    if (!keyValue || keyValue.length === 0) {
        placeholderFound = true;
        message = "is empty";
    } else if (keyValue.startsWith("YOUR_") || keyValue.includes("-HERE") || keyValue.includes("...")) {
        placeholderFound = true;
        message = "appears to be a generic placeholder";
    } else if (providerName === "gemini" && keyValue === "-") {
        placeholderFound = true;
        message = "is the default placeholder ('-')";
    } else if (providerName === "openrouter" && keyValue === "sk-or-v1-") {
        placeholderFound = true;
        message = "is the default OpenRouter prefix placeholder";
    } else if (providerName === "groq" && keyValue.startsWith("gsk_") && keyValue.length < 10) {
        placeholderFound = true;
        message = "appears to be an incomplete Groq key (starts with gsk_ but is too short)";
    } else if (providerName === "cerebras" && keyValue === "csk-") {
        placeholderFound = true;
        message = "is the default Cerebras prefix placeholder ('csk-')";
    } else if ((providerName === "novita" || providerName === "ollama") && keyValue.length < 10) {
        placeholderFound = true;
        message = "appears to be too short to be a valid key";
    }

    if (placeholderFound) {
        console.error(`${C.WARN}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${C.RESET}`);
        console.error(`${C.WARN}!! WARNING: API Key for provider '${providerName.toUpperCase()}' ${message}.${C.RESET}`);
        console.error(`${C.WARN}!! Please edit the script and replace it with your actual key.${C.RESET}`);
        console.error(`${C.WARN}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${C.RESET}`);
        return false;
    }
    return true;
}

/** Escape ERE metacharacters for word boundary regex matching */
function regexEscapeERE(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Print full usage / help text */
function printUsage() {
    const me = path.basename(process.argv[1] || 'ai.js');
    console.log(`
${C.INFO}Usage: ${me} <provider>${C.RESET}
${C.INFO}Usage: ${me} <provider> [filter]...${C.RESET}

${C.INFO}Description:${C.RESET}
  🤖 Starts an interactive chat session with the specified AI provider,
  maintaining conversation history, using a system prompt (if applicable),
  and streaming responses token by token.
  It will fetch available models and let you choose one by number.
  Now with image/multimodal support for vision-capable models!
  Now with thinking output support for reasoning models!

${C.INFO}Supported Providers:${C.RESET}
  gemini, openrouter, groq, together, cerebras, novita, ollama

${C.INFO}Chat Commands:${C.RESET}
  ${C.BOLD}/history${C.RESET}         - Show conversation history
  ${C.BOLD}/save <name>${C.RESET}     - Save current session to file
  ${C.BOLD}/load <name>${C.RESET}     - Load saved session from file
  ${C.BOLD}/clear${C.RESET}           - Clear all saved sessions
  ${C.BOLD}/upload <path>${C.RESET}   - Attach image to next message ${C.IMAGE}(New!)${C.RESET}
  ${C.BOLD}/image${C.RESET}           - Show currently attached image ${C.IMAGE}(New!)${C.RESET}
  ${C.BOLD}/clearimage${C.RESET}      - Remove attached image ${C.IMAGE}(New!)${C.RESET}
  ${C.BOLD}/togglethinking${C.RESET}  - Toggle thinking output display ${C.THINK}(New!)${C.RESET}
  ${C.BOLD}quit${C.RESET} or ${C.BOLD}exit${C.RESET}   - Exit chat

${C.INFO}Finding Model Identifiers (if needed manually):${C.RESET}
    ${C.BOLD}${C.USER}Gemini:${C.RESET}     https://ai.google.dev/models/gemini
    ${C.BOLD}${C.USER}OpenRouter:${C.RESET} https://openrouter.ai/models
    ${C.BOLD}${C.USER}Groq:${C.RESET}       https://console.groq.com/docs/models
    ${C.BOLD}${C.USER}Together:${C.RESET}   https://docs.together.ai/docs/inference-models
    ${C.BOLD}${C.USER}Cerebras:${C.RESET}   https://cloud.cerebras.ai
    ${C.BOLD}${C.USER}Novita:${C.RESET}     https://docs.novita.ai
    ${C.BOLD}${C.USER}Ollama:${C.RESET}     https://ollama.com/library

${C.BOLD}${C.INFO}Example Commands:${C.RESET}
  ${C.BOLD}${C.AI}${me} gemini${C.RESET}
  ${C.BOLD}${C.AI}${me} groq${C.RESET}
  ${C.BOLD}${C.AI}${me} together${C.RESET}
  ${C.BOLD}${C.AI}${me} openrouter${C.RESET}
  ${C.BOLD}${C.AI}${me} cerebras${C.RESET}
  ${C.BOLD}${C.AI}${me} novita${C.RESET}
  ${C.BOLD}${C.AI}${me} ollama${C.RESET}

${C.IMAGE}Image Support:${C.RESET}
  Supports JPEG, PNG, GIF, WebP. Max ${CONFIG.MAX_IMAGE_SIZE_MB}MB per image.
  Usage: ${C.BOLD}/upload ~/photo.jpg${C.RESET}, then type your question.

${C.THINK}Thinking Output:${C.RESET}
  Displays reasoning/thinking content from supported models in orange.
  Toggle with ${C.BOLD}/togglethinking${C.RESET} during chat.
${C.WARN}NOTE: Ensure API keys are set inside the script before running!${C.RESET}`);
}

// ========================================================================
//                          IMAGE FUNCTIONS
// ========================================================================

/** Get MIME type from file extension */
function getMimeType(filePath) {
    const ext = path.extname(filePath).toLowerCase();
    const types = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'
    };
    return types[ext] || 'application/octet-stream';
}

/** Validate image file: exists, size, type. Returns {path, mime, sizeKB} or null */
function validateImageFile(filePath) {
    // Remove quotes if present
    filePath = filePath.replace(/['"]/g, '');

    try {
        if (!fs.existsSync(filePath)) {
            console.error(`${C.ERROR}Error: File not found: ${filePath}${C.RESET}`);
            return null;
        }

        const stats = fs.statSync(filePath);
        const sizeMB = stats.size / (1024 * 1024);
        const maxBytes = CONFIG.MAX_IMAGE_SIZE_MB * 1024 * 1024;

        if (stats.size > maxBytes) {
            console.error(`${C.ERROR}Error: Image too large (${Math.floor(sizeMB)}MB). Max: ${CONFIG.MAX_IMAGE_SIZE_MB}MB${C.RESET}`);
            return null;
        }

        const mime = getMimeType(filePath);
        if (!CONFIG.SUPPORTED_IMAGE_TYPES.includes(mime)) {
            console.error(`${C.ERROR}Error: Unsupported image type: ${mime}${C.RESET}`);
            return null;
        }

        return { path: filePath, mime, sizeKB: Math.round(stats.size / 1024) };
    } catch (e) {
        console.error(`${C.ERROR}Error validating image: ${e.message}${C.RESET}`);
        return null;
    }
}

/** Encode image file to base64 */
function encodeImageToBase64(filePath) {
    filePath = filePath.replace(/['"]/g, '');
    try {
        return fs.readFileSync(filePath).toString('base64');
    } catch (e) {
        console.error(`${C.ERROR}Error reading image: ${e.message}${C.RESET}`);
        return null;
    }
}

function clearCurrentImage() {
    currentImage = { path: "", base64: "", mime: "" };
}

// ========================================================================
//                       MODEL FETCHING & FILTERING
// ========================================================================

/** Fetch models list from provider API */
async function fetchModels(provider, apiKey) {
    let url = "";
    let headers = {};
    let extractModels; // function(data) => string[]

    switch (provider) {
        case "gemini":
            url = `${ENDPOINTS.GEMINI.MODELS}?key=${apiKey}`;
            extractModels = (data) => {
                if (!data.models) return [];
                return data.models
                    .filter(m => m.supportedGenerationMethods && m.supportedGenerationMethods.some(method => method.includes("generateContent")))
                    .map(m => m.name.replace("models/", ""))
                    .filter(n => n.length > 0);
            };
            break;
        case "openrouter":
            url = ENDPOINTS.OPENROUTER.MODELS;
            headers = { "Authorization": `Bearer ${apiKey}`, "HTTP-Referer": "urn:chatcli:nodejs" };
            extractModels = (data) => (data.data || []).sort((a, b) => a.id.localeCompare(b.id)).map(m => m.id);
            break;
        case "groq":
            url = ENDPOINTS.GROQ.MODELS;
            headers = { "Authorization": `Bearer ${apiKey}` };
            extractModels = (data) => (data.data || []).sort((a, b) => a.id.localeCompare(b.id)).map(m => m.id);
            break;
        case "together":
            url = ENDPOINTS.TOGETHER.MODELS;
            headers = { "Authorization": `Bearer ${apiKey}` };
            extractModels = (data) => {
                const arr = Array.isArray(data) ? data : (data.data || []);
                return arr.sort((a, b) => a.id.localeCompare(b.id)).map(m => m.id);
            };
            break;
        case "cerebras":
            url = ENDPOINTS.CEREBRAS.MODELS;
            headers = { "Authorization": `Bearer ${apiKey}` };
            extractModels = (data) => (data.data || []).sort((a, b) => a.id.localeCompare(b.id)).map(m => m.id);
            break;
        case "novita":
            url = ENDPOINTS.NOVITA.MODELS;
            headers = { "Authorization": `Bearer ${apiKey}` };
            extractModels = (data) => (data.data || []).sort((a, b) => a.id.localeCompare(b.id)).map(m => m.id);
            break;
        case "ollama":
            url = ENDPOINTS.OLLAMA.MODELS;
            if (apiKey) headers = { "Authorization": `Bearer ${apiKey}` };
            extractModels = (data) => (data.models || []).map(m => m.name);
            break;
    }

    try {
        const res = await fetch(url, { headers });
        if (!res.ok) {
            const text = await res.text();
            console.error(`${C.ERROR}Error fetching models: HTTP ${res.status}: ${truncate(text, 200)}${C.RESET}`);
            console.error(`${C.INFO}Check network connection, API key validity/permissions, and endpoint (${url}).${C.RESET}`);
            return null;
        }

        const text = await res.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            console.error(`${C.ERROR}Error: API response for model list was not valid JSON.${C.RESET}`);
            console.error(`${C.INFO}Raw response (first 200 chars): ${truncate(text, 200)}${C.RESET}`);
            return null;
        }

        // Check for API-level errors in the JSON
        const apiError = data?.error?.message || data?.error?.code || data?.message || data?.detail || data?.error;
        if (apiError && typeof apiError === 'string' && apiError !== 'null') {
            console.error(`${C.ERROR}API Error during model fetch: ${apiError}${C.RESET}`);
            console.error(`${C.INFO}Check API key permissions and validity for provider '${provider.toUpperCase()}'.${C.RESET}`);
            console.error(`${C.INFO}Raw response (first 200 chars): ${truncate(text, 200)}${C.RESET}`);
            return null;
        }

        const models = extractModels(data);
        return models;
    } catch (e) {
        console.error(`${C.ERROR}Error fetching models: ${e.message}${C.RESET}`);
        console.error(`${C.INFO}Check network connection, API key validity/permissions, and endpoint (${url}).${C.RESET}`);
        return null;
    }
}

/** Filter models with word-boundary matching (same logic as bash version) */
function filterModels(models, filters) {
    if (!filters || filters.length === 0) return models;

    console.log(`${C.INFO}Filtering models with terms: ${filters.join(' ')}${C.RESET}`);
    console.log(`${C.INFO}Using word boundary matching for better precision${C.RESET}`);

    return models.filter(model => {
        const modelLower = model.toLowerCase();
        return filters.every(filter => {
            const escFilter = regexEscapeERE(filter.toLowerCase());
            const regex = new RegExp(`(^|[^a-z0-9])${escFilter}([^a-z0-9]|$)`);
            return regex.test(modelLower);
        });
    });
}

// ========================================================================
//                        HISTORY MANAGEMENT
// ========================================================================

/** Initialize history with system prompt */
function initializeHistory(isOpenAICompatible) {
    chatHistory = [];
    if (SYSTEM_PROMPT) {
        if (isOpenAICompatible) {
            chatHistory.push({ role: "system", content: SYSTEM_PROMPT });
        }
        // For Gemini, system prompt is handled by prepending to first user message.
    }
}

/** Save session to file */
function saveSession(name) {
    try {
        if (!fs.existsSync(CONFIG.SESSION_DIR)) {
            fs.mkdirSync(CONFIG.SESSION_DIR, { recursive: true });
        }
        const filePath = path.join(CONFIG.SESSION_DIR, `${name}.json`);
        fs.writeFileSync(filePath, JSON.stringify(chatHistory, null, 2));
        console.log(`${C.INFO}Session saved to: ${filePath}${C.RESET}`);
    } catch (e) {
        console.error(`${C.ERROR}Error saving session: ${e.message}${C.RESET}`);
    }
}

/** Validate a session file before loading */
function validateSession(filePath) {
    try {
        const raw = fs.readFileSync(filePath, 'utf8');
        let data;
        try {
            data = JSON.parse(raw);
        } catch (e) {
            console.error(`${C.ERROR}Error: Session file is not valid JSON.${C.RESET}`);
            return false;
        }

        if (!Array.isArray(data)) {
            console.error(`${C.ERROR}Error: Session file is not a valid JSON array.${C.RESET}`);
            return false;
        }

        for (const msg of data) {
            if (typeof msg !== 'object' || msg === null) {
                console.error(`${C.ERROR}Error: Invalid session format - Item is not an object${C.RESET}`);
                return false;
            }
            if (!msg.role) {
                console.error(`${C.ERROR}Error: Invalid session format - Missing role field${C.RESET}`);
                return false;
            }
            if (msg.role === 'system') {
                if (typeof msg.content !== 'string') {
                    console.error(`${C.ERROR}Error: Invalid session format - System message missing string .content${C.RESET}`);
                    return false;
                }
            } else if (['user', 'assistant', 'model'].includes(msg.role)) {
                if (typeof msg.content !== 'string' && !Array.isArray(msg.parts) && !Array.isArray(msg.content)) {
                    console.error(`${C.ERROR}Error: Invalid session format - Message missing .content (OpenAI) or .parts (Gemini)${C.RESET}`);
                    return false;
                }
            } else {
                console.error(`${C.ERROR}Error: Invalid session format - Unknown role: ${msg.role}${C.RESET}`);
                return false;
            }
        }
        return true;
    } catch (e) {
        console.error(`${C.ERROR}Error reading session file: ${e.message}${C.RESET}`);
        return false;
    }
}

/** Load session from file */
function loadSession(name) {
    try {
        if (!fs.existsSync(CONFIG.SESSION_DIR)) {
            fs.mkdirSync(CONFIG.SESSION_DIR, { recursive: true });
        }
        const filePath = path.join(CONFIG.SESSION_DIR, `${name}.json`);
        if (!fs.existsSync(filePath)) {
            console.error(`${C.ERROR}Error: Session file not found: ${filePath}${C.RESET}`);
            return false;
        }

        if (!validateSession(filePath)) {
            console.error(`${C.ERROR}Session file is corrupted or invalid. Cannot load.${C.RESET}`);
            return false;
        }

        const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
        chatHistory = data;
        firstUserMessage = false;
        console.log(`${C.INFO}Session loaded from: ${filePath}${C.RESET}`);
        console.log(`${C.INFO}Loaded ${chatHistory.length} messages. Use /history to see the conversation.${C.RESET}`);
        return true;
    } catch (e) {
        console.error(`${C.ERROR}Error loading session: ${e.message}${C.RESET}`);
        return false;
    }
}

/** Clear all saved sessions */
function clearSessions(rl) {
    return new Promise((resolve) => {
        if (!fs.existsSync(CONFIG.SESSION_DIR)) {
            console.error(`${C.INFO}No saved sessions to clear.${C.RESET}`);
            resolve();
            return;
        }

        let sessionFiles;
        try {
            sessionFiles = fs.readdirSync(CONFIG.SESSION_DIR).filter(f => f.endsWith('.json'));
        } catch (e) {
            console.error(`${C.INFO}No saved sessions to clear.${C.RESET}`);
            resolve();
            return;
        }

        if (sessionFiles.length === 0) {
            console.error(`${C.INFO}No saved sessions to clear.${C.RESET}`);
            resolve();
            return;
        }

        console.error(`${C.WARN}This will permanently delete all saved chat sessions in ${CONFIG.SESSION_DIR}:${C.RESET}`);
        sessionFiles.forEach(f => console.error(f.replace('.json', '')));

        rl.question(`${C.WARN}Are you sure you want to proceed? (y/N): ${C.RESET}`, (confirm) => {
            if (confirm.toLowerCase() === 'y') {
                for (const f of sessionFiles) {
                    try {
                        fs.unlinkSync(path.join(CONFIG.SESSION_DIR, f));
                    } catch (e) { /* ignore */ }
                }
                console.log(`${C.INFO}All saved sessions have been cleared.${C.RESET}`);
            } else {
                console.log(`${C.INFO}Clear operation cancelled.${C.RESET}`);
            }
            resolve();
        });
    });
}

// ========================================================================
//                    HISTORY TRUNCATION LOGIC
// ========================================================================

function truncateHistory(isOpenAICompatible) {
    const currentSize = chatHistory.length;
    let systemOffset = 0;
    if (isOpenAICompatible && chatHistory.length > 0 && chatHistory[0]?.role === 'system') {
        systemOffset = 1;
    }

    const allowedConversationalMessages = CONFIG.MAX_HISTORY_MESSAGES;
    const effectiveMaxEntries = allowedConversationalMessages + systemOffset;

    if (currentSize > effectiveMaxEntries) {
        let elementsToRemove = currentSize - effectiveMaxEntries;

        // Gemini needs user/model alternation; remove an even number to keep alignment
        if (!isOpenAICompatible) {
            if (elementsToRemove % 2 === 1) {
                elementsToRemove++;
            }
        }

        if (systemOffset === 1) {
            // Keep system prompt, remove from user/AI messages
            chatHistory = [chatHistory[0], ...chatHistory.slice(1 + elementsToRemove)];
        } else {
            chatHistory = chatHistory.slice(elementsToRemove);
        }
    }
}

// ========================================================================
//                        STREAMING HANDLER
// ========================================================================

async function handleStreamingResponse(provider, modelId, isOpenAICompatible, chatUrl, chatHeaders) {
    // Build the payload
    let payload = {};

    // Serialize history
    const historyArray = chatHistory.filter(h => h != null);
    if (historyArray.length === 0) {
        console.error(`${C.ERROR}Error: Empty history, cannot send request.${C.RESET}`);
        return;
    }

    if (!isOpenAICompatible) {
        // Gemini payload
        payload = {
            contents: historyArray.filter(m => m.role !== 'system'), // Gemini doesn't use system role
            generationConfig: {
                temperature: CONFIG.DEFAULT_OAI_TEMPERATURE,
                maxOutputTokens: CONFIG.DEFAULT_OAI_MAX_TOKENS,
                topP: CONFIG.DEFAULT_OAI_TOP_P
            }
        };
        if (enableToolCalling) {
            payload.tools = [{ "urlContext": {} }, { "googleSearch": {} }];
        }
    } else {
        // OpenAI-compatible payload
        let basePayload = {
            model: modelId,
            messages: historyArray,
            temperature: CONFIG.DEFAULT_OAI_TEMPERATURE,
            stream: true
        };

        if (provider === 'ollama') {
            payload = {
                ...basePayload,
                options: {
                    num_predict: CONFIG.DEFAULT_OAI_MAX_TOKENS,
                    top_p: CONFIG.DEFAULT_OAI_TOP_P
                }
            };
        } else if (provider !== 'together') {
            payload = {
                ...basePayload,
                max_tokens: CONFIG.DEFAULT_OAI_MAX_TOKENS,
                top_p: CONFIG.DEFAULT_OAI_TOP_P
            };
        } else {
            // Together: just base payload to avoid sensitivity
            payload = basePayload;
        }
    }

    process.stdout.write(`${C.AI}AI:${C.RESET} ${C.INFO}(💬 Waiting for stream...)${C.RESET}`);

    let fullAiResponseText = "";
    let fullAiThinkingText = "";
    let apiErrorOccurred = false;
    let streamErrorMessage = "";
    let streamFinishReason = "";
    let firstChunkReceived = false;
    let isThinking = false;
    let inThinkingDisplay = false;

    try {
        const res = await fetch(chatUrl, {
            method: 'POST',
            headers: chatHeaders,
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const errText = await res.text();
            process.stdout.write(C.CLR);
            console.log(`${C.AI}AI:${C.RESET} ${C.ERROR}API Error: ${res.status} ${truncate(errText, 200)}${C.RESET}`);
            apiErrorOccurred = true;
            streamErrorMessage = `HTTP ${res.status}`;
        } else {
            // Stream reading
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line

                for (const line of lines) {
                    let jsonChunk = "";

                    // Handle SSE format (starts with "data: ")
                    if (line.startsWith("data: ")) {
                        jsonChunk = line.substring(6).trim();
                        if (jsonChunk === "[DONE]") break;
                    } else if (line.startsWith("{")) {
                        // Ollama format - direct JSON
                        jsonChunk = line;
                    }

                    if (!jsonChunk) continue;

                    // Validate JSON
                    let chunk;
                    try {
                        chunk = JSON.parse(jsonChunk);
                    } catch (e) {
                        continue; // Skip invalid JSON
                    }

                    // Universal error check
                    const chunkError = chunk?.error?.message || chunk?.error || chunk?.detail;
                    if (chunkError && typeof chunkError === 'string' && chunkError !== 'null') {
                        streamErrorMessage = `API Error in stream: ${chunkError}`;
                        apiErrorOccurred = true;
                        break;
                    }

                    // Gemini-specific blocking check
                    if (!isOpenAICompatible) {
                        const blockReason = chunk?.promptFeedback?.blockReason;
                        if (blockReason && blockReason !== 'null') {
                            streamErrorMessage = `Content blocked by API (Reason: ${blockReason})`;
                            const partialText = chunk?.candidates?.[0]?.content?.parts?.[0]?.text;
                            if (partialText) {
                                if (!firstChunkReceived) {
                                    process.stdout.write(C.CLR);
                                    process.stdout.write(`${C.AI}AI:${C.RESET}  ${C.AI}`);
                                    firstChunkReceived = true;
                                }
                                process.stdout.write(partialText);
                                fullAiResponseText += partialText;
                            }
                            apiErrorOccurred = true;
                            break;
                        }
                    }

                    // Extract text chunk, thinking chunk, finish reason
                    let textChunk = "";
                    let thinkingChunk = "";
                    let currentSfr = "";

                    if (isOpenAICompatible) {
                        if (provider === 'ollama') {
                            textChunk = chunk?.message?.content || "";
                            thinkingChunk = chunk?.message?.thinking || "";
                            if (chunk?.done === true) {
                                currentSfr = "stop";
                            }
                        } else {
                            textChunk = chunk?.choices?.[0]?.delta?.content || chunk?.choices?.[0]?.text || "";
                            thinkingChunk = chunk?.choices?.[0]?.delta?.reasoning || "";
                            currentSfr = chunk?.choices?.[0]?.finish_reason || "";
                        }
                    } else {
                        // Gemini
                        textChunk = chunk?.candidates?.[0]?.content?.parts?.[0]?.text || "";
                        currentSfr = chunk?.candidates?.[0]?.finishReason || "";

                        if (!currentSfr || currentSfr === 'null') {
                            // Check safety ratings
                            const safetyBlocked = (chunk?.candidates?.[0]?.safetyRatings || [])
                                .find(r => r.blocked === true);
                            if (safetyBlocked) {
                                currentSfr = "SAFETY";
                            }
                        }

                        // Check for tool calls if enabled
                        if (enableToolCalling) {
                            const parts = chunk?.candidates?.[0]?.content?.parts || [];
                            const toolCalls = parts.filter(p => p.functionCall);
                            if (toolCalls.length > 0) {
                                if (!firstChunkReceived) {
                                    process.stdout.write(C.CLR);
                                    process.stdout.write(`${C.AI}AI:${C.RESET}  ${C.AI}`);
                                    firstChunkReceived = true;
                                }
                                console.error(`\n${C.WARN}AI requested tool call 🌐:${C.RESET}`);
                                toolCalls.forEach(tc => console.error(JSON.stringify(tc, null, 2)));
                                console.error(`${C.WARN}(This script does not automatically execute tool calls or return tool output to the model.)\n${C.RESET}`);
                            }
                        }
                    }

                    // Store finish reason
                    if (currentSfr && currentSfr !== 'null' && (!streamFinishReason || streamFinishReason === 'null')) {
                        streamFinishReason = currentSfr;
                    }

                    // UI update for first chunk
                    if (!firstChunkReceived && (textChunk || thinkingChunk)) {
                        process.stdout.write(C.CLR);
                        process.stdout.write(`${C.AI}AI:${C.RESET}  `);
                        firstChunkReceived = true;
                    }

                    // Handle native thinking output (Ollama format OR OpenAI reasoning)
                    if (thinkingChunk && ENABLE_THINKING_OUTPUT) {
                        fullAiThinkingText += thinkingChunk;
                        if (!inThinkingDisplay) {
                            process.stdout.write(`${C.THINK}[Thinking] `);
                            inThinkingDisplay = true;
                        }
                        process.stdout.write(`${C.THINK}${thinkingChunk}${C.RESET}`);
                    }

                    // Print and accumulate text with <think tag handling
                    if (textChunk) {
                        fullAiResponseText += textChunk;

                        let processingChunk = textChunk;
                        while (processingChunk.length > 0) {
                            if (isThinking) {
                                const closeIdx = processingChunk.indexOf("</think");
                                if (closeIdx !== -1) {
                                    const beforeTag = processingChunk.substring(0, closeIdx);
                                    let afterTag = processingChunk.substring(closeIdx + 7); // "</think" = 7 chars
                                    // Handle closing bracket
                                    const bracketIdx = afterTag.indexOf(">");
                                    if (bracketIdx !== -1) {
                                        afterTag = afterTag.substring(bracketIdx + 1);
                                    }

                                    if (ENABLE_THINKING_OUTPUT) {
                                        process.stdout.write(beforeTag);
                                    }
                                    if (ENABLE_THINKING_OUTPUT) {
                                        process.stdout.write(`${C.RESET}\n`);
                                        process.stdout.write(C.AI);
                                    }
                                    isThinking = false;
                                    inThinkingDisplay = false;
                                    processingChunk = afterTag;
                                } else {
                                    if (ENABLE_THINKING_OUTPUT) {
                                        process.stdout.write(processingChunk);
                                    }
                                    processingChunk = "";
                                }
                            } else {
                                const openIdx = processingChunk.indexOf("<think");
                                if (openIdx !== -1) {
                                    const beforeTag = processingChunk.substring(0, openIdx);
                                    let afterTag = processingChunk.substring(openIdx + 6); // "<think" = 6 chars
                                    // Handle opening bracket
                                    const bracketIdx = afterTag.indexOf(">");
                                    if (bracketIdx !== -1) {
                                        afterTag = afterTag.substring(bracketIdx + 1);
                                    }

                                    process.stdout.write(`${C.AI}${beforeTag}`);
                                    if (ENABLE_THINKING_OUTPUT) {
                                        process.stdout.write(`${C.THINK}<think`);
                                        inThinkingDisplay = true;
                                    }
                                    isThinking = true;
                                    processingChunk = afterTag;
                                } else {
                                    // End thinking display if we were in one and now getting regular content
                                    if (inThinkingDisplay) {
                                        process.stdout.write(C.RESET);
                                        inThinkingDisplay = false;
                                    }
                                    process.stdout.write(`${C.AI}${processingChunk}`);
                                    processingChunk = "";
                                }
                            }
                        }
                    }

                    // Check if Gemini stream is done with special finish reasons
                    if (!isOpenAICompatible && streamFinishReason && streamFinishReason !== 'null') {
                        if (['SAFETY', 'RECITATION', 'OTHER'].includes(streamFinishReason)) {
                            if (!textChunk && !fullAiResponseText) {
                                streamErrorMessage = `Stream ended by API (Finish Reason: ${streamFinishReason}). No content generated.`;
                                apiErrorOccurred = true;
                            } else if (!textChunk) {
                                streamErrorMessage = `(Stream truncated by API. Finish Reason: ${streamFinishReason})`;
                            }
                        }
                        break;
                    }

                    // For Ollama, check if done
                    if (provider === 'ollama' && streamFinishReason === 'stop') {
                        break;
                    }
                }

                if (apiErrorOccurred) break;
            }
        }
    } catch (e) {
        if (!firstChunkReceived) {
            process.stdout.write(C.CLR);
        }
        streamErrorMessage = `API call failed. ${truncate(e.message, 150)}`;
        apiErrorOccurred = true;
        console.log(`${C.AI}AI:${C.RESET} ${C.ERROR}${streamErrorMessage}${C.RESET}`);
    }

    // Post-stream processing
    if (!firstChunkReceived && !streamErrorMessage) {
        process.stdout.write(C.CLR);
        console.log(`${C.AI}AI:${C.RESET} ${C.INFO}(No content in response or empty stream ended prematurely)${C.RESET}`);
    } else if (firstChunkReceived) {
        process.stdout.write(`${C.RESET}\n`);
        if (apiErrorOccurred && streamErrorMessage) {
            console.log(`${C.ERROR}${streamErrorMessage}${C.RESET}`);
        }
    }

    // Check response length
    if (fullAiResponseText.length > CONFIG.MAX_MESSAGE_LENGTH) {
        console.error(`${C.WARN}Warning: AI response was truncated (exceeded ${CONFIG.MAX_MESSAGE_LENGTH} characters)${C.RESET}`);
        fullAiResponseText = fullAiResponseText.substring(0, CONFIG.MAX_MESSAGE_LENGTH);
    }

    // Strip think tags for history
    const aiText = stripThinkTags(fullAiResponseText);

    // Create AI message for history
    let localAiMessageJson = null;
    if (!apiErrorOccurred && aiText) {
        if (!isOpenAICompatible) {
            localAiMessageJson = { role: "model", parts: [{ text: aiText }] };
        } else {
            localAiMessageJson = { role: "assistant", content: aiText };
        }
    }

    // Add to history or rollback
    if (localAiMessageJson) {
        chatHistory.push(localAiMessageJson);
    } else {
        // Rollback last user message on error
        if (chatHistory.length > 0) {
            const lastMsg = chatHistory[chatHistory.length - 1];
            if (lastMsg?.role === 'user') {
                console.error(`${C.WARN}(Rolling back last user message from history due to error or no AI response text)${C.RESET}`);
                chatHistory.pop();
            }
        }
    }

    console.log(""); // Blank line after response
}

// ========================================================================
//                            READLINE HELPER
// ========================================================================

/** Prompt user for input and return the answer */
function askQuestion(rl, prompt) {
    return new Promise((resolve) => {
        rl.question(prompt, (answer) => {
            resolve(answer);
        });
    });
}

// ========================================================================
//                              MAIN
// ========================================================================

async function main() {
    // --- Validate Configuration ---
    validateNumeric(CONFIG.DEFAULT_OAI_TEMPERATURE, 0, 2, "DEFAULT_OAI_TEMPERATURE");
    validateNumeric(CONFIG.DEFAULT_OAI_TOP_P, 0, 1, "DEFAULT_OAI_TOP_P");
    validateNumeric(CONFIG.DEFAULT_OAI_MAX_TOKENS, 1, 1000000, "DEFAULT_OAI_MAX_TOKENS");

    // --- Argument Parsing ---
    const args = process.argv.slice(2);

    if (args.length < 1) {
        console.error(`${C.ERROR}Error: Invalid number of arguments.${C.RESET}`);
        printUsage();
        process.exit(1);
    }

    const provider = args[0].toLowerCase();
    const filters = args.slice(1);

    // --- Validate Provider ---
    const validProviders = ['gemini', 'openrouter', 'groq', 'together', 'cerebras', 'novita', 'ollama'];
    if (!validProviders.includes(provider)) {
        console.error(`${C.ERROR}Error: Unknown provider '${provider}'. Choose from: ${validProviders.join(', ')}${C.RESET}`);
        printUsage();
        process.exit(1);
    }

    // --- Get API Key and Check Placeholders ---
    const keyMap = {
        gemini: API_KEYS.GEMINI,
        openrouter: API_KEYS.OPENROUTER,
        groq: API_KEYS.GROQ,
        together: API_KEYS.TOGETHER,
        cerebras: API_KEYS.CEREBRAS,
        novita: API_KEYS.NOVITA,
        ollama: API_KEYS.OLLAMA
    };

    const apiKey = keyMap[provider];
    if (!checkPlaceholderKey(apiKey, provider)) {
        console.error(`${C.INFO}Exiting due to placeholder API key. Please edit the script and add your actual key for '${provider}'.${C.RESET}`);
        process.exit(1);
    }

    // --- Interactive prompt for Gemini tool calling ---
    if (provider === "gemini") {
        console.log("");
        const tempRl = readline.createInterface({ input: process.stdin, output: process.stdout });
        let toolAnswer;
        while (true) {
            toolAnswer = await askQuestion(tempRl, `${C.INFO}Enable online tool calling (web search, URL context) for Gemini? (y/n, 1/0): ${C.RESET}`);
            const tl = toolAnswer.toLowerCase();
            if (tl === 'y' || tl === '1') {
                enableToolCalling = true;
                console.log(`${C.INFO}Tool calling enabled.${C.RESET}`);
                break;
            } else if (tl === 'n' || tl === '0') {
                enableToolCalling = false;
                console.log(`${C.INFO}Tool calling disabled.${C.RESET}`);
                break;
            } else {
                console.error(`${C.WARN}Invalid input. Please enter 'y', 'n', '1', or '0'.${C.RESET}`);
            }
        }
        tempRl.close();
        console.log("");
    }

    // --- Fetch and Select Model ---
    console.log(`${C.INFO}Fetching available models for ${provider.toUpperCase()}...${C.RESET}`);
    let models = await fetchModels(provider, apiKey);

    if (!models || models.length === 0) {
        console.error(`${C.ERROR}Error: No models found or failed to parse successful API response for provider '${provider}'.${C.RESET}`);
        process.exit(1);
    }

    // --- Filter models ---
    if (filters.length > 0) {
        models = filterModels(models, filters);
    }

    if (models.length === 0) {
        console.error(`${C.ERROR}No models available.${C.RESET}`);
        if (filters.length > 0) {
            console.error(`${C.WARN}Your filter criteria (${filters.join(' ')}) did not match any models from provider '${provider.toUpperCase()}'.${C.RESET}`);
            console.error(`${C.INFO}Filters use word boundary matching (e.g., '3' matches 'gpt-3' but not '13b')${C.RESET}`);
        } else {
            console.error(`${C.WARN}No models were returned by the API for provider '${provider.toUpperCase()}'.${C.RESET}`);
        }
        process.exit(1);
    }

    let modelId = "";

    if (models.length === 1) {
        modelId = models[0];
        console.log(`${C.INFO}Auto-selecting only matching model.${C.RESET}`);
    } else {
        console.log(`${C.INFO}Available Models for ${provider.toUpperCase()}:${C.RESET}`);
        models.forEach((m, i) => {
            const num = String(i + 1).padStart(3, ' ');
            console.log(`  ${C.BOLD}${num}${C.RESET}. ${m}`);
        });
        console.log("");

        const selectRl = readline.createInterface({ input: process.stdin, output: process.stdout });
        while (true) {
            const choice = await askQuestion(selectRl, `${C.INFO}Select model by number: ${C.RESET}`);
            const idx = parseInt(choice);
            if (!isNaN(idx) && idx >= 1 && idx <= models.length) {
                modelId = models[idx - 1];
                break;
            } else {
                console.error(`${C.WARN}Invalid selection. Enter number between 1 and ${models.length}.${C.RESET}`);
            }
        }
        selectRl.close();
    }

    console.log(`${C.INFO}Using model:${C.RESET} ${modelId}`);
    console.log("");

    // --- Configure Chat URL and Headers ---
    let chatUrl = "";
    let chatHeaders = { "Content-Type": "application/json", "Accept": "application/json" };
    let isOpenAICompatible = false;

    switch (provider) {
        case "gemini":
            chatUrl = `${ENDPOINTS.GEMINI.CHAT_BASE}${modelId}:streamGenerateContent?key=${apiKey}&alt=sse`;
            isOpenAICompatible = false;
            break;
        case "openrouter":
            chatUrl = ENDPOINTS.OPENROUTER.CHAT;
            chatHeaders["Authorization"] = `Bearer ${apiKey}`;
            chatHeaders["HTTP-Referer"] = "urn:chatcli:nodejs";
            chatHeaders["X-Title"] = "NodeChatCLI";
            isOpenAICompatible = true;
            break;
        case "groq":
            chatUrl = ENDPOINTS.GROQ.CHAT;
            chatHeaders["Authorization"] = `Bearer ${apiKey}`;
            isOpenAICompatible = true;
            break;
        case "together":
            chatUrl = ENDPOINTS.TOGETHER.CHAT;
            chatHeaders["Authorization"] = `Bearer ${apiKey}`;
            isOpenAICompatible = true;
            break;
        case "cerebras":
            chatUrl = ENDPOINTS.CEREBRAS.CHAT;
            chatHeaders["Authorization"] = `Bearer ${apiKey}`;
            isOpenAICompatible = true;
            break;
        case "novita":
            chatUrl = ENDPOINTS.NOVITA.CHAT;
            chatHeaders["Authorization"] = `Bearer ${apiKey}`;
            isOpenAICompatible = true;
            break;
        case "ollama":
            chatUrl = ENDPOINTS.OLLAMA.CHAT;
            if (apiKey) chatHeaders["Authorization"] = `Bearer ${apiKey}`;
            isOpenAICompatible = true;
            break;
    }

    // --- Initialize History ---
    initializeHistory(isOpenAICompatible);

    // --- Print Startup Info ---
    console.log(`--- ${C.INFO}Starting Chat${C.RESET} ---`);
    console.log(`${C.INFO}Provider:${C.RESET}      ${provider.toUpperCase()}`);
    console.log(`${C.INFO}Model:${C.RESET}         ${modelId}`);
    console.log(`${C.INFO}History Limit:${C.RESET} Last ${CONFIG.MAX_HISTORY_MESSAGES} messages (user+AI)`);
    console.log(`${C.INFO}Message Limit:${C.RESET} ${CONFIG.MAX_MESSAGE_LENGTH} characters per message`);
    console.log(`${C.INFO}Temp/Tokens/TopP (Defaults):${C.RESET} ${CONFIG.DEFAULT_OAI_TEMPERATURE} / ${CONFIG.DEFAULT_OAI_MAX_TOKENS} / ${CONFIG.DEFAULT_OAI_TOP_P}`);

    if (SYSTEM_PROMPT) {
        if (!isOpenAICompatible) {
            console.log(`${C.INFO}System Prompt:${C.RESET}   Set (prepended to first user message for Gemini)`);
        } else if (chatHistory.length > 0 && chatHistory[0]?.role === 'system') {
            console.log(`${C.INFO}System Prompt:${C.RESET}   Active (OpenAI-compatible format)`);
        } else {
            console.log(`${C.WARN}System Prompt:${C.RESET}   Set but seems inactive.${C.RESET}`);
        }
    } else {
        console.log(`${C.INFO}System Prompt:${C.RESET}   Inactive (set to empty string)`);
    }

    // Display tool calling status if Gemini
    if (provider === "gemini") {
        if (enableToolCalling) {
            console.log(`${C.INFO}Tool Calling:${C.RESET}    ${C.BOLD}Enabled${C.RESET} (for Gemini models)`);
        } else {
            console.log(`${C.INFO}Tool Calling:${C.RESET}    Disabled (for Gemini models)`);
        }
    }

    // Display thinking output status
    if (ENABLE_THINKING_OUTPUT) {
        console.log(`${C.INFO}Thinking Output:${C.RESET} ${C.BOLD}${C.THINK}Enabled${C.RESET} (toggle with /togglethinking)`);
    } else {
        console.log(`${C.INFO}Thinking Output:${C.RESET} Disabled (toggle with /togglethinking)`);
    }

    console.log(`Enter prompt. Type ${C.BOLD}'quit'/'exit'${C.RESET}. Commands: ${C.BOLD}/history, /save <name>, /load <name>, /clear, /upload, /togglethinking${C.RESET}`);
    console.log(`---------------------------------------------------------------------------------------`);

    // --- Signal Handling ---
    process.on('SIGINT', () => {
        console.log(`\n${C.WARN}Interrupted. Cleaning up...${C.RESET}`);
        process.exit(130);
    });
    process.on('SIGTERM', () => {
        process.exit(143);
    });

    // --- Start REPL ---
    const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
        historySize: 1000
    });

    const chatLoop = async () => {
        while (true) {
            // Build prompt with image indicator
            let promptPrefix = "";
            if (currentImage.path) {
                promptPrefix = `[${C.INFO}📎 ${path.basename(currentImage.path)}${C.RESET}] `;
            }

            const userInput = await askQuestion(rl, `${promptPrefix}${C.BOLD}${C.USER}You:${C.RESET} `);

            // Exit commands
            if (userInput.toLowerCase() === 'quit' || userInput.toLowerCase() === 'exit') {
                console.log("Exiting chat.");
                break;
            }

            // --- Slash Commands ---
            if (userInput.startsWith('/')) {
                const spaceIdx = userInput.indexOf(' ');
                const cmd = spaceIdx !== -1 ? userInput.substring(0, spaceIdx) : userInput;
                const cmdArgs = spaceIdx !== -1 ? userInput.substring(spaceIdx + 1).trim() : "";

                switch (cmd) {
                    case '/upload': {
                        if (!cmdArgs) {
                            console.error(`${C.IMAGE}Usage: /upload <image_path>${C.RESET}`);
                            continue;
                        }
                        const cleanPath = cmdArgs.replace(/['"]/g, '');
                        console.error(`${C.IMAGE}Validating image...${C.RESET}`);
                        const validated = validateImageFile(cleanPath);
                        if (!validated) continue;

                        console.error(`${C.IMAGE}Encoding image...${C.RESET}`);
                        const b64 = encodeImageToBase64(validated.path);
                        if (!b64) {
                            console.error(`${C.ERROR}Error: Failed to encode image${C.RESET}`);
                            continue;
                        }

                        currentImage = { path: validated.path, base64: b64, mime: validated.mime };
                        console.error(`${C.IMAGE}✓ Attached: ${path.basename(validated.path)} (${validated.mime}, ${validated.sizeKB}KB)${C.RESET}`);
                        continue;
                    }
                    case '/image': {
                        if (currentImage.path) {
                            console.error(`${C.IMAGE}Current image: ${path.basename(currentImage.path)} (${currentImage.mime})${C.RESET}`);
                        } else {
                            console.error(`${C.IMAGE}No image attached.${C.RESET}`);
                        }
                        continue;
                    }
                    case '/clearimage': {
                        clearCurrentImage();
                        console.error(`${C.IMAGE}Image cleared.${C.RESET}`);
                        continue;
                    }
                    case '/togglethinking': {
                        ENABLE_THINKING_OUTPUT = !ENABLE_THINKING_OUTPUT;
                        if (ENABLE_THINKING_OUTPUT) {
                            console.error(`${C.INFO}Thinking output ${C.BOLD}${C.THINK}enabled${C.RESET}.`);
                        } else {
                            console.error(`${C.INFO}Thinking output ${C.BOLD}disabled${C.RESET}.`);
                        }
                        continue;
                    }
                    case '/history': {
                        console.log(`${C.INFO}--- Current Conversation History (${chatHistory.length} messages) ---${C.RESET}`);
                        if (chatHistory.length === 0) {
                            console.error("(History is empty)");
                        } else {
                            for (const msg of chatHistory) {
                                const role = msg.role;
                                let content = "";
                                if (typeof msg.content === 'string') {
                                    content = msg.content;
                                } else if (Array.isArray(msg.content)) {
                                    // OpenAI multimodal format
                                    const textPart = msg.content.find(p => p.type === 'text');
                                    content = textPart ? textPart.text : '[multimodal content]';
                                } else if (Array.isArray(msg.parts)) {
                                    content = msg.parts[0]?.text || '[parts content]';
                                }

                                let color = C.USER;
                                if (role === 'assistant' || role === 'model') color = C.AI;
                                else if (role === 'system') color = C.WARN;
                                console.error(`${color}[${role}]${C.RESET} ${truncate(content, 500)}`);
                            }
                        }
                        console.log(`${C.INFO}--------------------------------------------${C.RESET}`);
                        continue;
                    }
                    case '/save': {
                        if (!cmdArgs) {
                            console.error(`${C.WARN}Usage: /save <session_name>${C.RESET}`);
                            continue;
                        }
                        if (!validateSessionName(cmdArgs)) continue;
                        saveSession(cmdArgs);
                        continue;
                    }
                    case '/load': {
                        if (!cmdArgs) {
                            console.error(`${C.WARN}Usage: /load <session_name>${C.RESET}`);
                            continue;
                        }
                        if (!validateSessionName(cmdArgs)) continue;
                        loadSession(cmdArgs);
                        continue;
                    }
                    case '/clear': {
                        await clearSessions(rl);
                        continue;
                    }
                    default: {
                        // Unknown command, fall through to send as message
                        break;
                    }
                }
                // If we didn't continue, it's an unknown slash command, treat as normal message
                // Actually, the bash version just falls through for unknown commands too.
                // But the switch default breaks so we continue below as a normal message.
            }

            // Skip empty input (unless image is attached)
            if (!userInput && !currentImage.base64) {
                continue;
            }

            // Default prompt if only image attached
            let finalInput = userInput;
            if (!finalInput && currentImage.base64) {
                finalInput = "Describe this image in detail.";
            }

            // Check message length limit
            if (finalInput.length > CONFIG.MAX_MESSAGE_LENGTH) {
                console.error(`${C.ERROR}Error: Message too long (${finalInput.length} chars). Maximum is ${CONFIG.MAX_MESSAGE_LENGTH} characters.${C.RESET}`);
                console.error(`${C.INFO}Please shorten your message and try again.${C.RESET}`);
                continue;
            }

            console.error(`${C.INFO}[Sending...]${C.RESET}`);

            // --- Construct User Message ---
            let userPromptText = finalInput;
            let userMessageObj = {};

            if (currentImage.base64) {
                // Image attached
                if (!isOpenAICompatible) {
                    // Gemini format
                    if (firstUserMessage && SYSTEM_PROMPT) {
                        userPromptText = `${SYSTEM_PROMPT}\n\n${finalInput}`;
                    }
                    userMessageObj = {
                        role: "user",
                        parts: [
                            { text: userPromptText },
                            { inlineData: { mimeType: currentImage.mime, data: currentImage.base64 } }
                        ]
                    };
                } else if (provider === 'ollama') {
                    // Ollama native format with images array
                    userMessageObj = {
                        role: "user",
                        content: userPromptText,
                        images: [currentImage.base64]
                    };
                } else {
                    // OpenAI compatible format
                    userMessageObj = {
                        role: "user",
                        content: [
                            { type: "text", text: finalInput },
                            { type: "image_url", image_url: { url: `data:${currentImage.mime};base64,${currentImage.base64}` } }
                        ]
                    };
                }
                clearCurrentImage();
            } else {
                // Text only
                if (!isOpenAICompatible) {
                    // Gemini
                    if (firstUserMessage && SYSTEM_PROMPT) {
                        userPromptText = `${SYSTEM_PROMPT}\n\nUser: ${finalInput}`;
                    }
                    userMessageObj = { role: "user", parts: [{ text: userPromptText }] };
                } else {
                    userMessageObj = { role: "user", content: userPromptText };
                }
            }

            firstUserMessage = false;

            // Add user message to history
            chatHistory.push(userMessageObj);

            // History Truncation
            truncateHistory(isOpenAICompatible);

            // Send Request
            await handleStreamingResponse(provider, modelId, isOpenAICompatible, chatUrl, chatHeaders);
        }

        rl.close();
        console.log("👋 Chat session ended.");
    };

    await chatLoop();
}

main().catch(e => {
    console.error(`${C.ERROR}Fatal Error: ${e.message}${C.RESET}`);
    if (e.stack && process.env.DEBUG) {
        console.error(e.stack);
    }
    process.exit(1);
});
