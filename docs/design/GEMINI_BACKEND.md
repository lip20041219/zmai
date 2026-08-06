# Gemini Backend — Design Document

**Date**: 2026-07-17  
**Status**: Implemented, plugin class

---

## 1. Overview

New backend supporting **Google Gemini API** (`generativelanguage.googleapis.com`).

| Field | Value |
|---|---|
| Backend name | `gemini` |
| Provider | `gemini` (Google) |
| File | `src/zmai/gateway/backends/gemini.py` |
| Class | `GeminiBackend` |
| API base | `https://generativelanguage.googleapis.com/v1beta` |
| Auth | `GEMINI_API_KEY` (query param `?key=`) |
| Default model | `gemini-2.0-flash` |
| Capabilities | Streaming, Tool Use, System Prompt, Multi-turn |

---

## 2. API Format — Gemini vs Standard

### 2a. Auth

| Backend | Method |
|---|---|
| Claude | Header `x-api-key` |
| DeepSeek | Header `Authorization: Bearer` |
| **Gemini** | Query param `?key={api_key}` |

### 2b. Message Format

```
Standard (BackendRequest):             Gemini (native):
┌──────────────────────┐              ┌──────────────────────────┐
│ messages: [          │              │ contents: [              │
│   {role:"user",      │              │   {role:"user",          │
│    content:"..."}    │              │    parts:[{text:"..."}]} │
│ ]                    │              │ ]                        │
│ system_prompt:"..."  │              │ systemInstruction:       │
│                      │              │   {parts:[{text:"..."}]} │
└──────────────────────┘              └──────────────────────────┘
```

Key differences:
- `messages` → `contents`
- `role: "assistant"` → `role: "model"`
- Content is wrapped in `parts: [{text: "..."}]`
- System prompt is at top level `systemInstruction`, not in contents

### 2c. Tool Format

```
Standard:                              Gemini:
┌──────────────────────┐              ┌──────────────────────────┐
│ tools: [{            │              │ tools: [{                │
│   type:"function",   │              │   functionDeclarations:  │
│   function:{...}     │              │   [{name,desc,params}]   │
│ }]                   │              │ }]                       │
└──────────────────────┘              └──────────────────────────┘
```

Tool calls in response:

```
Standard:                              Gemini:
┌──────────────────────┐              ┌──────────────────────────┐
│ tool_calls: [{       │              │ parts: [{                │
│   id, name, args     │              │   functionCall: {        │
│ }]                   │              │     name, args           │
│                      │              │   }                      │
└──────────────────────┘              │ }]                       │
                                       └──────────────────────────┘
```

### 2d. Endpoints

| Operation | Endpoint |
|---|---|
| Invoke | `POST /models/{model}:generateContent?key={key}` |
| Stream | `POST /models/{model}:streamGenerateContent?key={key}&alt=sse` |

DeepSeek uses `/v1/chat/completions`. Claude uses `/v1/messages`. Gemini uses a different endpoint per model with RPC-style method names.

---

## 3. Implementation Details

### 3a. File

**`src/zmai/gateway/backends/gemini.py`** — 275 lines.

### 3b. Public Interface

```python
class GeminiBackend(Backend):
    name: str = "gemini"
    provider: str = "gemini"

    def __init__(self, config: dict | None = None)
    def invoke(self, request: BackendRequest) -> BackendResponse
    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]
    @property
    def capabilities(self) -> set[BackendCapability]
```

### 3c. Internal Methods

| Method | Purpose |
|---|---|
| `_build_request_body(request)` | Convert `BackendRequest` → Gemini API body |
| `_parse_response(data)` | Parse Gemini response → `BackendResponse` |
| `_parse_stream_event(parsed, index)` | Parse SSE chunk → `BackendEvent` (text/tool_call) |
| `_map_stop_reason(finish_reason)` | Gemini `STOP` → `end_turn`, `MAX_TOKENS` → `max_tokens` |
| `_tool_defs_to_gemini(td)` | Convert `ToolDefinition` → `functionDeclarations` format |

### 3d. Token Usage

| Gemini field | TokenUsage field |
|---|---|
| `usageMetadata.promptTokenCount` | `input_tokens` |
| `usageMetadata.candidatesTokenCount` | `output_tokens` |
| `usageMetadata.totalTokenCount` | (not mapped, used for logging) |

### 3e. Stop Reason Mapping

| Gemini finishReason | Standard stop_reason |
|---|---|
| `STOP` | `end_turn` |
| `MAX_TOKENS` | `max_tokens` |
| `SAFETY` | `end_turn` |
| `RECITATION` | `end_turn` |
| `OTHER` | `end_turn` |

---

## 4. Registration

### 4a. `BACKEND_METADATA` entry

```python
"gemini": {
    "label": "Gemini (Google)",
    "default_model": "gemini-2.0-flash",
    "env_api_key": "GEMINI_API_KEY",
    "env_model": "GEMINI_MODEL",
    "module": "zmai.gateway.backends.gemini",
    "class": "GeminiBackend",
    "verify_url": "https://generativelanguage.googleapis.com/v1beta/models",
    "verify_method": "GET",
}
```

**File**: `gateway/backends/__init__.py`

### 4b. `BACKEND_DEFAULT_CONFIG` entry

```python
"gemini": {
    "model": "gemini-2.0-flash",
    "base_url": "https://generativelanguage.googleapis.com/v1beta",
    "timeout": 120,
    "max_tokens": 4096,
    "temperature": 0.7,
}
```

**File**: `gateway/backends/__init__.py`

---

## 5. Usage

### Environment Variable

```bash
export GEMINI_API_KEY="AIza..."
```

### CLI

```bash
zmai --backend gemini "Explain quantum computing"
```

### zmai.json

```json
{
    "backends": {
        "gemini": {
            "model": "gemini-2.0-flash",
            "temperature": 0.3
        }
    }
}
```

### Auth

```bash
zmai auth update gemini
```

---

## 6. Files Changed

| File | Change | Type |
|---|---|---|
| `src/zmai/gateway/backends/gemini.py` | **New** — GeminiBackend class | Add |
| `src/zmai/gateway/backends/__init__.py` | Add import, BACKEND_METADATA, BACKEND_DEFAULT_CONFIG | Edit |
| `zmai.json` | Add gemini config example | Edit |

**Not modified**: Runtime, Gateway core (base.py, registry.py), Memory, Tool, Config, Auth, CLI.

---

## 7. Limitations

| Limitation | Reason | Future |
|---|---|---|
| No `max_retries` | Gemini API is RESTful, errors are final | Add if needed |
| No `STOP` sequence support | Gemini API supports `stopSequences`; already implemented in `_build_request_body` | Already done |
| `VISION` capability not declared | Not tested with image inputs | Add after verification |
| `STRUCTURED_OUTPUT` not declared | Gemini supports `responseMimeType: application/json` | Add after verification |
