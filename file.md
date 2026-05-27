# MIRA Backend — File Descriptions

Complete one-to-two line functional summaries for every file in the codebase.

---

## Root

| File | Description |
|------|-------------|
| `__init__.py` (system) | Main entry point for the `system` package; uses lazy imports for core components (Agent, Browser, LLM) to reduce startup time. |
| `config.py` | Pydantic-based settings loader that reads environment variables for API keys, directory paths, and feature flags. |
| `logging_config.py` | Configures the root logging system with a custom formatter that cleans up logger names and silences noisy third-party libraries (e.g., httpx, WS). |
| `observability.py` | Provides `@observe` and `@observe_debug` decorators for LMNR-based tracing; degrades gracefully to no-ops if LMNR is not installed. |
| `utils.py` | General-purpose utility helpers: async timing decorators, URL matching, singleton pattern, task creation with error handling, and misc string helpers. |

---

## `system/agent/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init that lazily re-exports the `Agent` class and related views. |
| `service.py` | Core `Agent` class that runs the multi-step browser automation loop: calls the LLM, parses actions, executes them, and manages the agent lifecycle. |
| `views.py` | Defines all core agent data structures including `AgentOutput`, `AgentState`, action definitions (click, type, navigate, etc.), history objects, and infinite-loop detection logic. |
| `prompts.py` | Constructs the system prompt and per-step user prompts for the LLM, including browser state summarization, file system context, and wallet data injection. |
| `judge.py` | Evaluates agent performance by prompting the LLM to compare the task requirements against the agent's final trajectory and output, returning a pass/fail verdict. |
| `workflow.py` | Records agent task trajectories as replay-able macros and executes them using semantic element matching; falls back to autonomous mode on mismatch. |
| `variable_detector.py` | Scans agent history and action parameters for sensitive or reusable personal data (email, address, phone) to support wallet-based auto-fill. |

---

## `system/agent/message_manager/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the message manager sub-package. |
| `service.py` | Manages the agent's full conversation context: injects system prompts, compacts long histories to keep context windows manageable, and filters sensitive data before sending to the LLM. |
| `views.py` | Pydantic models defining message history items (`MessageHistoryItem`) and the overall `MessageManagerState`. |
| `utils.py` | Utility functions for persisting conversation logs to disk. |

---

## `system/browser/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init re-exporting the primary browser classes. |
| `session.py` | The main `BrowserSession` class: manages the CDP connection lifecycle, launches/connects to Chrome, and dispatches all browser events. |
| `session_manager.py` | Tracks all active CDP targets and sessions; automatically recovers agent focus when the currently focused tab crashes or is closed. |
| `events.py` | Defines all typed `BaseEvent` subclasses for every browser interaction (click, navigate, scroll, type, screenshot, tab management, download, captcha, etc.) with configurable timeouts. |
| `watchdog_base.py` | Abstract `BaseWatchdog` base class providing the mechanics to attach/detach event handlers to a browser session with CDP reconnect-awareness and structured debug logging. |
| `views.py` | Data models for browser state: `BrowserStateSummary`, `BrowserStateHistory`, `TabInfo`, `PageInfo`, `BrowserError`, and `URLNotAllowedError`. |
| `python_highlights.py` | Draws dashed bounding boxes and index number overlays on top of a screenshot using PIL, colour-coded by element type (button, input, link, etc.) for LLM vision input. |
| `video_recorder.py` | Records browser activity to an MP4 file by writing base64-encoded screenshot frames to an imageio writer at a configured framerate. |
| `profile.py` | Defines `BrowserProfile` — a Pydantic model holding all browser launch settings: viewport size, proxy, user-agent, Chrome flags, and persistent profile paths. |
| `gif.py` *(if present)* | Legacy helper for assembling animated GIFs from screenshot frames; superseded by `video_recorder.py`. |

---

## `system/browser/watchdogs/`

| File | Description |
|------|-------------|
| `dom_watchdog.py` | Listens for navigation and state events to rebuild and cache the enhanced DOM tree; also manages screenshot capture and element highlighting for each agent step. |
| `network_watchdog.py` | Monitors network requests and responses to track pending requests, detect downloads, and expose recent network activity to the agent. |
| `dialog_watchdog.py` | Auto-dismisses or records browser dialogs (alert, confirm, prompt) and exposes their messages to the agent. |
| `storage_watchdog.py` | Saves and loads browser storage state (cookies, localStorage) to/from disk for session persistence across agent runs. |

---

## `system/dom/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the DOM subsystem. |
| `service.py` | `DomService` — orchestrates retrieval of the full DOM tree, accessibility tree, and CDP snapshot for a browser target, then merges them into a single `EnhancedDOMTreeNode` tree. |
| `views.py` | The core data classes for the DOM layer: `EnhancedDOMTreeNode`, `EnhancedSnapshotNode`, `EnhancedAXNode`, `SerializedDOMState`, `DOMSelectorMap`, `DOMInteractedElement`, and supporting types like `DOMRect` and `MatchLevel`. |
| `enhanced_snapshot.py` | Parses raw CDP `DOMSnapshot.captureSnapshot` results into a backend-node-id-keyed lookup of `EnhancedSnapshotNode` objects containing computed styles, bounds, and paint order. |
| `markdown_extractor.py` | Converts the current page's DOM tree into clean markdown text; also provides a `chunk_markdown_by_structure` function that splits long pages into overlap-aware chunks. |
| `utils.py` | Small DOM helpers: `cap_text_length` for truncating text and `generate_css_selector_for_element` for building stable CSS selectors from an `EnhancedDOMTreeNode`. |

---

## `system/dom/serializer/`

| File | Description |
|------|-------------|
| `serializer.py` | `DOMTreeSerializer` — converts the simplified DOM tree into a compact text representation used as the primary LLM input for page understanding. |
| `eval_serializer.py` | `DOMEvalSerializer` — alternative serializer producing a representation optimised for evaluation or benchmarking purposes. |
| `html_serializer.py` | `HTMLSerializer` — renders the enhanced DOM tree back to clean HTML (stripping scripts/styles) for use by the markdown extractor. |
| `views.py` | Serializer-level data models and filter constants. |

---

## `system/llm/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init that re-exports all LLM provider classes and helper functions. |
| `base.py` | `BaseChatModel` — a `Protocol` (structural interface) defining the `ainvoke` method contract that all LLM implementations must satisfy. |
| `messages.py` | Pydantic models for all LLM message types: `UserMessage`, `SystemMessage`, `AssistantMessage`, plus content-part types for text, images, and tool calls. |
| `views.py` | `ChatInvokeCompletion` and `ChatInvokeUsage` models — the standard return type from any `ainvoke` call, including token counts and stop reason. |
| `models.py` | Module-level lazy attributes and a `get_llm_by_name(model_name)` factory that instantiates `ChatOpenAI` or `ChatGoogle` from a string like `"google_gemini_2_5_flash"`. |
| `exceptions.py` | Custom exception hierarchy: `ModelError`, `ModelProviderError`, and `ModelRateLimitError` for structured LLM error reporting. |
| `schema.py` | `SchemaOptimizer` — cleans and flattens Pydantic JSON schemas (removing `$ref`, `additionalProperties`, etc.) to produce strict, provider-compatible schemas for structured output. |

---

## `system/llm/google/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the Google LLM provider. |
| `chat.py` | `ChatGoogle` — implements `BaseChatModel` using the Google GenAI SDK; handles text, structured JSON, and thinking-budget configurations for all Gemini models, with exponential-backoff retry logic. |
| `serializer.py` | `GoogleMessageSerializer` — converts the internal `BaseMessage` list into the `Content`/`Part` format expected by the Gemini API, handling system instructions and base64 image data. |

---

## `system/llm/openai/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the OpenAI LLM provider. |
| `chat.py` | `ChatOpenAI` — implements `BaseChatModel` using the `AsyncOpenAI` client; supports structured JSON output via `response_format`, reasoning models (o-series), and configurable schema strictness. |
| `like.py` | `ChatOpenAILike` — minimal subclass of `ChatOpenAI` that relaxes the `model` type annotation to `str`, allowing any OpenAI-compatible endpoint (e.g., local proxies). |
| `serializer.py` | `OpenAIMessageSerializer` — converts `BaseMessage` objects into the `ChatCompletionMessageParam` dicts required by the OpenAI Chat Completions API. |
| `responses_serializer.py` | `ResponsesAPIMessageSerializer` — alternative serializer targeting the OpenAI Responses API format (`EasyInputMessageParam`). |

---

## `system/telemetry/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the telemetry subsystem. |
| `service.py` | `ProductTelemetry` singleton — sends anonymized usage events to PostHog via the `posthog` client; can be disabled via `ANONYMIZED_TELEMETRY=false`. |
| `views.py` | Defines telemetry event dataclasses: `AgentTelemetryEvent`, `MCPClientTelemetryEvent`, `MCPServerTelemetryEvent`, and `CLITelemetryEvent`. |
| `telemetry/service.py` | *(duplicate path)* Same `ProductTelemetry` singleton — appears to be a nested copy of the telemetry service. |
| `telemetry/views.py` | *(duplicate path)* Variant of telemetry event views that calls `is_running_in_docker()` directly instead of using `CONFIG.IN_DOCKER`. |

---

## `system/tokens/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the token counting and cost calculation subsystem. |
| `service.py` | `TokenCountingService` — tracks per-model token usage across agent steps and computes API cost estimates using LiteLLM's pricing data. |
| `views.py` | Data models for token usage: `TokenUsageEntry`, `TokenCostCalculated`, `ModelPricing`, `ModelUsageStats`, and `UsageSummary`. |
| `mappings.py` | A small dictionary mapping internal model aliases (e.g. `"gemini-flash-latest"`) to their LiteLLM pricing keys. |
| `custom_pricing.py` | Hard-coded pricing entries for internal `bu-*` model tiers that are not yet in LiteLLM's pricing database. |

---

## `system/tools/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the browser action tools subsystem. |
| `utils.py` | `get_click_description()` — builds a human-readable description of a DOM element being interacted with for use in agent history logging. |

---

## `system/tools/extraction/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the structured data extraction sub-package. |
| `service.py` | Implements the `extract_structured_data` action: retrieves page markdown, passes it to the LLM with a user-supplied JSON schema, and returns a validated `ExtractionResult`. |
| `views.py` | `ExtractionResult` — Pydantic model returned by the extraction action, containing the validated `data` payload, the schema used, and content processing stats. |
| `schema_utils.py` | `schema_dict_to_pydantic_model()` — dynamically builds a Pydantic `BaseModel` from a plain JSON Schema dict, enabling runtime schema enforcement during extraction. |

---

## `system/tools/registry/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the tool registry sub-package. |
| `service.py` | `Registry` — a generic decorator-based action registry that registers, validates, and executes browser actions; handles sensitive data substitution (including TOTP 2FA) and domain-scoped action filtering. |
| `views.py` | `RegisteredAction`, `ActionModel`, `ActionRegistry`, and `SpecialActionParameters` — the data structures underpinning the tool registry. |

---

## `system/tools/default_actions/` *(if present)*

| File | Description |
|------|-------------|
| `browser_actions.py` | Default set of registered browser actions (click, type, scroll, navigate, upload, dropdown, etc.) wired to CDP event dispatch via the watchdog system. |

---

## `system/screenshots/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the screenshot persistence service. |
| `service.py` | `ScreenshotService` — asynchronously writes base64-encoded screenshots to disk as per-step PNG files and reads them back when needed for history replay. |

---

## `system/filesystem/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the filesystem helper. |
| `file_system.py` | `FileSystem` — provides the agent with a sandboxed view of the local file system: listing available files for upload and resolving paths within an allowed directory. |

---

## `system/wallet/`

| File | Description |
|------|-------------|
| `__init__.py` | Package init for the InfoWallet module. |
| `service.py` | `InfoWallet` — a per-user JSON-backed key-value store persisted to `MIRA/wallets/<user_id>.json`; provides `get_all`, `set`, and `format_for_prompt` helpers used to inject user data into agent prompts. |

---

## `tests/`

| File | Description |
|------|-------------|
| `__init__.py` | Test package root. |
| `conftest.py` | Shared pytest fixtures and configuration for the test suite. |

---

## `tests/robustness/`

| File | Description |
|------|-------------|
| `__init__.py` | Robustness test sub-package init. |
| `test_agent_robustness.py` | End-to-end robustness tests that run the agent against live or mock browser sessions to verify stability under edge cases (loops, crashes, empty pages). |

---

## `wallets/`

| File | Description |
|------|-------------|
| `<user_id>.json` | Auto-generated JSON files, one per user, persisting key-value pairs such as name, email, address, and phone number for wallet-based form auto-fill. |

---

## Root-level Configuration

| File | Description |
|------|-------------|
| `.env` | Environment variable file holding API keys (`OPENAI_API_KEY`, `GOOGLE_API_KEY`), feature flags, and directory overrides — never committed to source control. |
| `requirements.txt` / `pyproject.toml` | Python dependency declarations. |
| `README.md` | Project documentation: setup instructions, usage examples, and architecture overview for the MIRA accessibility agent. |
