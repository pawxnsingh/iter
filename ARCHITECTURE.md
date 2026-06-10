# Newt — Non-UI Architecture Reference

This is the reference for everything that isn't the Textual TUI. Read top-to-bottom in
the **Study Order** section if you want to learn it; jump to a subsystem if you already
know what you're looking for.

---

## TL;DR conceptual model

```
                  ┌─────────────────────────────────────────────────┐
                  │  TUI (ui/) — irrelevant for this doc            │
                  └────────────────────┬────────────────────────────┘
                                       │ user message
                  ┌────────────────────▼────────────────────────────┐
                  │  Agent.run(msg)  ── async generator of events   │
                  │   agent/agent.py:44                             │
                  └────────────────────┬────────────────────────────┘
                                       │ delegates to
                  ┌────────────────────▼────────────────────────────┐
                  │  query_loop()  ── the state machine             │
                  │   query/loop.py:66                              │
                  │                                                 │
                  │  for each turn:                                 │
                  │    1. compact context (5 strategies)            │
                  │    2. stream LLM response                       │
                  │    3. for each tool call:                       │
                  │         check permissions                       │
                  │         run pre-tool hooks                      │
                  │         execute (parallel reads, serial writes) │
                  │         run post-tool hooks                     │
                  │    4. decide whether to continue                │
                  │    5. recover from 413 / max-tokens / errors    │
                  └─────────────────────────────────────────────────┘
                              │              │           │            │
                  ┌───────────▼──┐  ┌────────▼──┐  ┌─────▼────┐  ┌───▼─────┐
                  │ ContextMgr   │  │ Permission│  │ Hook     │  │ Tool    │
                  │ +5 compact   │  │ Checker   │  │ Executor │  │ Registry│
                  │ strategies   │  │ +Bash AST │  │ (snap)   │  │ +MCP    │
                  └──────────────┘  └───────────┘  └──────────┘  └─────────┘
                                                                       │
                                                  ┌────────────────────▼─────────┐
                                                  │ services/tools/orchestration │
                                                  │  partition into batches      │
                                                  │  (parallel reads, serial     │
                                                  │   writes), enforce caps      │
                                                  └──────────────────────────────┘
```

Three orthogonal axes:
- **Time axis** — query_loop is an async generator. Each yield = one event for the TUI.
- **Memory axis** — ContextManager grows; compaction shrinks it before token limits.
- **Trust axis** — Permission checker gates every tool call. Bash gets AST analysis on top.

---

## The request lifecycle (the only diagram that matters)

User submits "edit foo.py to fix the bug" in the TUI. Here's every layer touched, in order:

| # | Where | What |
|---|---|---|
| 1 | `main.py:64` | CLI parsed → `run_tui(config)` |
| 2 | `ui/widgets/conversation.py` (`_run_agent_task`) | Agent created as `async with Agent(config, permission_prompt_fn=…)` |
| 3 | `agent/agent.py:73-117` | `__aenter__` initialises Session, fires `SESSION_START` hooks, loads instructions |
| 4 | TUI submits via input queue → `agent.run(msg)` invoked |
| 5 | `agent/agent.py:44-71` | yields `agent_start` event; persists user message to JSONL; fires `USER_PROMPT_SUBMIT` |
| 6 | `agent/agent.py:62-68` | enters `query_loop(...)` async generator |
| 7 | `query/loop.py:89-136` | **COMPACT PHASE** — snip / micro / collapse / autocompact in that order |
| 8 | `query/loop.py:121-136` | hard-limit guard: synthetic 413 if context still over `blocking_limit` |
| 9 | `query/loop.py:139-191` | **STREAM PHASE** — `client.chat_completion()` returns `AsyncIterator[StreamEvent]` |
| 10 | `client/llm_client.py` | provider chosen via env (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / Azure trio) |
| 11 | yields `text_delta` / collects `tool_call_completed` events as they stream |
| 12 | `query/loop.py:193-212` | assistant message appended to ContextManager + JSONL |
| 13 | `query/loop.py:218-270` | **CONTINUE DECISION** — recover from 413 (line 225-246) / max-output-tokens (250-269) / done |
| 14 | `query/loop.py:273-419` | **TOOL EXECUTION PHASE** (only if tool calls were emitted) |
| 14a | `query/loop.py:290-303` | loop detection — same tool+args 3× in last 20 → abort |
| 14b | `query/loop.py:308-352` | per-tool permission check (3-stage: rule → mode → ask user via callback) |
| 14c | `query/loop.py:357-378` | `PRE_TOOL_USE` hooks — can rewrite input or block |
| 14d | `services/tools/orchestration.py:58-86` | partition into batches (parallel reads, serial writes) |
| 14e | `services/tools/orchestration.py:140-204` | execute via semaphore (≤10 concurrent), gather in REQUEST order |
| 14f | `tools/registry.py:63-109` | each tool: validate params (Pydantic) → `tool.execute(invocation)` → cap output |
| 14g | `query/loop.py:407-416` | `POST_TOOL_USE` hooks |
| 15 | `query/loop.py:421-437` | turn++; if `max_turns` exceeded, terminate |
| 16 | loop back to step 7 if model wants another turn (had tool calls) |
| 17 | `agent/agent.py:71` | yields `agent_end` |
| 18 | `agent/agent.py:119-142` | `__aexit__` fires `SESSION_END` hooks, saves metadata, closes client + MCP |

Every event yielded between 5 and 17 lands in the TUI's `Conversation` widget which translates them to Textual messages.

---

## The conceptual primitives

Five things you must internalise. Everything else falls out.

### 1. `AgentEvent` — the protocol between agent and UI
`agent/event.py:11-23` defines 7 event types. The agent yields these; the UI translates each to a Textual message in `ui/widgets/conversation.py::_dispatch_agent_event`. **The UI does not know about LLM streaming, tools, or permissions.** It only knows about events.

### 2. `Tool` — the abstract interface every capability implements
`tools/base.py:119-222`. To add a capability:
- subclass `Tool`
- declare `name`, `description`, `kind` (READ/WRITE/SHELL/MEMORY/NETWORK/MCP)
- declare `schema` (Pydantic model OR plain dict — both supported, see `to_openai_schema` line 193)
- implement `async def execute(self, invocation: ToolInvocation) -> ToolResult`
- optionally override `is_concurrency_safe`, `is_read_only`, `is_destructive`

`ToolKind` drives the **default** `is_mutating` answer. Read tools run in parallel; mutating tools force a new serial batch.

### 3. `PermissionResult` — the gate
`core_types/permissions.py:45-63`. The checker (`permissions/checker.py:57-148`) returns one of three behaviors: ALLOW / DENY / ASK. ASK calls the `permission_prompt_fn` callback supplied by the TUI. The Agent never displays UI itself.

### 4. `MessageItem` — the unit of conversation memory
`context/manager.py:30-51`. ContextManager owns a `list[MessageItem]`. Compaction strategies mutate this list. Token counting is `len(content) // 4` — rough on purpose; precise counts come back from the LLM in `TokenUsage`.

### 5. `HookCommand` — the customization extension point
`core_types/hooks.py:45-53`. User-defined commands (or HTTP endpoints) fire at any of the 27 events. The hook subprocess receives JSON on stdin, writes JSON on stdout to influence the decision (`block`, `updatedInput`, `additionalContext`, etc.).

---

## Subsystem map (one paragraph each, dense)

### A. Query loop — `query/loop.py`, `query/transitions.py`, `query/config.py`
The agent's heart. 437 lines that orchestrate everything. Async generator — each turn yields events. Three phases per turn: **compact → stream → tools**. Recovery built in for two errors that reliably happen at long contexts: (1) prompt-too-long (413) → reactive_compact + retry; (2) max-output-tokens-hit → inject a recovery message + retry (cap 3). State lives in `QueryState` (line 48-56), reasons for transitions in `Continue` dataclass (`transitions.py:15-22`). Loop detection at line 290-303: MD5(name+args), 20-item sliding window, abort on 3× repeat.

### B. Agent + Session + Client — `agent/{agent,session,event}.py`, `client/{llm_client,response}.py`
`Agent` is thin (143 lines). It owns: a Session (created in `__init__`), an AbortController (per-run), a HookExecutor (snapshotted at startup). `Session` (`agent/session.py`) wires LLMClient + ToolRegistry + ContextManager + MCPManager + SessionStorage; this is where the actual state lives. `LLMClient` auto-detects provider from env (Anthropic / OpenAI / Azure), translates schemas, and yields `StreamEvent`s (`response.py:74-84`). Three event types matter: TEXT_DELTA, TOOL_CALL_COMPLETED, MESSAGE_COMPLETE.

### C. Tool system — `tools/base.py`, `tools/registry.py`, `tools/discovery.py`, `tools/subagents.py`, `services/tools/{executor,orchestration,result_storage}.py`, `tools/builtin/*`, `tools/mcp/*`
**Registration**: `ToolRegistry.register()` for builtins; `ToolDiscoveryManager` auto-scans `.ai-agent/tools/*.py` for user tools; `mcp_manager.register_tools()` for MCP. **Execution**: `services/tools/orchestration.py:58-86` partitions tool calls into batches by `is_concurrency_safe`. Reads run concurrent (semaphore = 10); first non-safe call ends the batch and starts a serial section. **Critical**: results come out in REQUEST order, not completion order — the LLM needs them ordered to match its tool_call_ids. Output truncation at `tool.max_result_size_chars` (default 100K, can be overridden per tool).

### D. Permissions — `core_types/permissions.py`, `permissions/{checker,bash_security,filesystem,rules}.py`
Five modes (`PermissionMode` enum): `default` (ask for writes), `plan` (block all execution), `accept_edits` (auto-approve in cwd), `bypass` (allow everything), `dont_ask` (auto-deny unmatched). The checker (`checker.py:57-148`) runs three stages: **(1)** explicit rules (deny > allow > ask globs) + safety checks (path is cwd, sensitive file detection); **(2)** mode-based decision; **(3)** fallback to ASK. Bash gets extra: `bash_security.py` does pattern-based detection of injection (`$(...)`, backticks), destructive ops (`rm -rf /`), pipe-to-shell, env-leak — plus a list of known read-only commands that bypass prompts even in default mode.

### E. Hooks — `core_types/hooks.py`, `hooks/{executor,config}.py`
27 events (lifecycle, tool, compact, config). User defines hooks in `settings.json` or `.ai-agent/hooks/`. **Snapshot pattern** (`hooks/config.py:22-46`): hook config frozen at session start to prevent TOCTOU. Hook subprocess gets JSON on stdin (`{event, tool_name, tool_input, session_id, cwd, ...}`), writes JSON to stdout (`{decision, reason, updatedInput, blockingError, additionalContext}`). Secret env vars stripped before exec (`hooks/executor.py:37-53`). Two key wire-ups in query_loop: PRE_TOOL_USE at line 357, POST_TOOL_USE at 407.

### F. Context + compaction — `context/manager.py`, `services/compact/*`
`ContextManager` owns the message list, computes token estimates, runs compaction. Five strategies, applied at different triggers:
- **snip** (`snip_compact.py`) — lossless, removes oldest message groups. Runs first.
- **micro** (`micro_compact.py`) — clears tool results older than N turns. Always runs each iteration (zero cost).
- **collapse** (`context_collapse.py`) — merges adjacent same-role messages.
- **auto** (`auto_compact.py`) — LLM summarises history, replaces all old messages with the summary. Triggered when `current_tokens > threshold` (default `context_window - 33K`).
- **reactive** (`reactive_compact.py`) — emergency LLM compact when API returns 413. Different from auto in that it must succeed or the call fails.

`auto_compact.py:107-145` defines a 5-level warning system (warning → error → blocking_limit). 5-level so the UI can show a status bar that changes color as you approach the wall.

### G. Settings + Config — `config/{config,loader,settings,validation}.py`
Two layers: **`config.py`** is a Pydantic model used at runtime; **`settings.py`** is a 5-layer JSON merger (managed → user → project → local → cli) that produces a dict the Config can be built from. **`loader.py`** is the bridge: reads global `~/.config/ai-agent/config.toml`, project-level `.ai-agent/config.toml`, merges them, then constructs the Config. Project config wins over global. `_merge_dicts` (loader.py:78-86) does deep merge — nested dicts merge recursively, scalars/arrays overwrite.

### H. Session persistence — `session/{storage,export}.py`
JSONL append-only (`storage.py:78-95`). One message per line. Crash-safe — partial writes are skipped on resume. `parent_uuid` chain links each message to the previous turn (`load_transcript:104-116`). Path is `~/.local/share/ai-agent/projects/<slug>/transcripts/<session_id>.jsonl` where slug is `md5(project_dir)[:16]`. `list_sessions(cwd)` scans the project's transcript dir; `load_transcript(path)` rebuilds the message list.

### I. Prompts — `prompts/{system,sections,output_styles}.py`
`get_system_prompt()` (system.py:26-55) assembles seven static sections + a dynamic boundary marker `DYNAMIC_BOUNDARY` (signals where prompt caching can split). Sections in `sections.py`: intro, system, actions, tools (auto-injected from registry), tone, efficiency, plus user-supplied `developer_instructions` from AGENT.md. Output styles loaded from `.ai-agent/output-styles/` or settings.

### J. Slash commands — `commands/__init__.py`
50+ commands registered via `@register_command(name, description)` decorator. Receive `(args: str, context: dict)` where context has `{"session", "config", "context_manager"}`. Special return strings: `"__EXIT__"` quits the app; `"__RETRY__:<msg>"` re-feeds `<msg>` to the agent (used by `/retry` and `/resume`). Commands are NOT tools — no permission gating, no hook firing, they run inline.

### K. Utils — `utils/{abort,errors,paths,text}.py`, `memory/memdir.py`
- `abort.py` — `AbortController` based on `asyncio.Event`. `create_child()` cascades. **Not synchronous** — `abort()` sets the event but running tasks continue until they next check `.aborted`.
- `errors.py` — `AgentError` (with details + cause), `ConfigError` (subclass adding key + file).
- `paths.py` — `resolve_path`, `is_binary` (null-byte sniff in first 8KB), `display_path_rel_to_cwd`.
- `text.py` — `count_tokens` (tiktoken), `truncate_text` (preserve_lines + suffix).
- `memory/memdir.py` — auto-memory backing store (used by `MemoryTool`).

### L. main.py — 72 lines
Click-based CLI. Loads config, applies CLI overrides, validates, calls `run_tui`. That's it.

---

## 15 trip wires — non-obvious behavior

Read these BEFORE reading the code. They prevent "this looks broken" reactions.

1. **Tool results come out in REQUEST order, not COMPLETION order.** `services/tools/orchestration.py:177-189` uses `gather()` which preserves order. If tool A is slow and B is fast, B waits in a buffer until A finishes before either is yielded. Required by the LLM contract: tool_call_ids must match tool_results in order.

2. **Permission check happens BEFORE tool execution.** `query/loop.py:308-352`. If you write a custom tool that does its own validation, do it in `validate_param` — not in `execute`. Failed validation in `execute` produces a ToolResult.error that the model sees, but the side effects already happened.

3. **Compaction is lossy.** `auto_compact.py` summarises history, deletes the originals. Resume from JSONL recovers structure but mid-session detail is gone.

4. **Tool output is silently truncated at `max_result_size_chars`** (default 100K). `tools/registry.py:99-107` AND `services/tools/orchestration.py:110-123` both enforce — double cap possible. Overflow saved to disk by `result_storage.py`.

5. **Abort is not synchronous.** `AbortController.abort()` sets the event; running tools continue until they check `.aborted` (most don't). No graceful task cancellation.

6. **Permission mode can change mid-session via tool result metadata.** `query/loop.py:402-405` — specific tools (`plan_mode.py`) set `metadata["set_permission_mode"]`. Last write wins if multiple tools change it in same turn.

7. **MCP is lazy-loaded.** Only initialised if `config.mcp_servers` is non-empty (`session.py:52-56`). Heavy import (fastmcp) is deferred. If startup is slow, session waits up to `startup_timeout_sec`.

8. **Token estimates are crude.** `len(content) // 4` everywhere except real LLM responses. Compaction may trigger early or late. The reactive 413 path exists exactly because of this.

9. **Tool retry resets partial response.** On `__RETRY_RESET__` (line 172-175), `response_text` and `tool_calls` are cleared. Model resends from scratch. User may briefly see duplicate text if first attempt got 413 mid-stream.

10. **Hooks are frozen at session start.** `hooks/config.py:22-46`. Modifying `settings.json` mid-session has no effect. Restart required.

11. **Concurrency is per-invocation, not per-tool.** `is_concurrency_safe(params)` is called for each tool call. Shell tool is safe for `ls`, unsafe for `rm`. A single unsafe call ends the batch and forces serial mode for everything after it.

12. **`is_destructive` affects PERMISSIONS, not just UX.** `permissions/checker.py:89-93` — destructive tools get ASK, not ALLOW. The TUI's "are you sure" dialog is downstream of this.

13. **Microcompact runs every iteration unconditionally.** `context/manager.py:141`. Cost is near-zero (just a filter), but it's not skipped even when there's nothing to compact.

14. **Consecutive autocompact failures are session-counted.** `auto_compact.py:35` — `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`. After 3 failures, next API call gets a synthetic 413 (`query/loop.py:133`) instead of real attempt.

15. **Tool param validation errors don't raise — they become ToolResult.error.** `services/tools/orchestration.py:99-104`, `tools/registry.py:77-85`. The model sees a normal error result and may retry with corrected params. User never sees a crash.

---

## Study order — read in this sequence

Each step builds on the previous. Times are reading times for someone who knows Python.

| # | Files | Time | What you learn |
|---|---|---|---|
| 1 | `core_types/permissions.py`, `core_types/hooks.py` | 5 min | Vocabulary: PermissionMode, HookEvent enums |
| 2 | `agent/event.py` | 5 min | The 7 events the agent emits |
| 3 | `agent/agent.py` | 10 min | Lifecycle (`__aenter__`/`__aexit__`), `run()` shape |
| 4 | `agent/session.py` | 10 min | Where actual state lives |
| 5 | `client/response.py`, `client/llm_client.py` | 15 min | Provider abstraction, StreamEvent, retry loop |
| 6 | `tools/base.py` | 15 min | The Tool ABC, ToolResult, FileDiff, ToolKind |
| 7 | `tools/registry.py`, `tools/discovery.py` | 10 min | Registration, discovery, allow/disallow filtering |
| 8 | `services/tools/orchestration.py` | 20 min | Batching, partitioning, semaphore, REQUEST-order yield |
| 9 | `tools/builtin/read_file.py`, `edit_file.py`, `shell.py` | 15 min | Three reference tool implementations |
| 10 | `permissions/checker.py` | 20 min | The 3-stage decision flow |
| 11 | `permissions/bash_security.py` | 15 min | Why bash needs special handling (injection patterns, destructive ops) |
| 12 | `context/manager.py` | 20 min | MessageItem, append, compaction orchestration |
| 13 | `services/compact/auto_compact.py` | 15 min | 5-level warning system, threshold computation |
| 14 | `services/compact/{snip,micro,collapse,reactive}_compact.py` | 15 min | Each strategy's contract |
| 15 | `query/loop.py` | 30 min | The full loop with everything you've now seen |
| 16 | `hooks/{executor,config}.py` | 15 min | Snapshot pattern, JSON protocol |
| 17 | `session/storage.py` | 10 min | JSONL format, parent_uuid chain, resume |
| 18 | `config/{config,loader,settings}.py` | 15 min | 5-layer merge, AGENT.md hierarchy |
| 19 | `prompts/{system,sections}.py` | 10 min | System prompt assembly, dynamic boundary |
| 20 | `commands/__init__.py` | 15 min | Slash command registry, `__EXIT__` / `__RETRY__:` |

**Total: ~4.5 hours of focused reading.**

---

## Five architectural truths to remember

1. **The query loop runs single-threaded.** Tools execute concurrently inside `run_tools()`, but the loop itself never parallelises. All state mutations happen in one goroutine equivalent.

2. **Permission checking MUST precede tool execution.** Any custom tool that does its own gating is wrong; the loop is the only correct place.

3. **Tool results are buffered, not streamed.** Truncation happens before the model sees them; overflow goes to disk.

4. **Compaction is lossy and one-way.** Once auto-compact runs, the original messages are gone. Resume reads JSONL (which still has them); the in-memory list does not.

5. **Hooks are frozen at startup.** The snapshot pattern (`hooks/config.py:22-46`) intentionally blocks mid-session injection. This is a security property, not a bug.
