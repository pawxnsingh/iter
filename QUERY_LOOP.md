# `query/loop.py` — Study Guide

**The heart of the agent.** ~465 lines. An async generator state machine that
processes one user message into a stream of `AgentEvent` objects. Every
reference below is `query/loop.py:N`.

Multi-provider LLM support (Anthropic / OpenAI / Azure) is preserved through
the `client/llm_client.py` seam. The loop itself never branches on provider.

---

## Table of contents

1. [Mental model](#1-mental-model)
2. [State (lines 50-64)](#2-state-lines-50-64)
3. [Function signature & setup (lines 67-98)](#3-function-signature--setup-lines-67-98)
4. [The big `while True` (line 100)](#4-the-big-while-true-line-100)
5. [STEP 0 — Task budget check (lines 101-109)](#5-step-0--task-budget-check-lines-101-109)
6. [STEP 1 — Compact phase (lines 111-122)](#6-step-1--compact-phase-lines-111-122)
7. [STEP 2 — Streaming phase (lines 124-198)](#7-step-2--streaming-phase-lines-124-198)
8. [STEP 3 — Persist + POST_SAMPLING (lines 200-244)](#8-step-3--persist--post_sampling-lines-200-244)
9. [STEP 4 — Continue decision & recovery (lines 246-308)](#9-step-4--continue-decision--recovery-lines-246-308)
10. [STEP 5 — Tool execution (lines 310-450)](#10-step-5--tool-execution-lines-310-450)
11. [STEP 6 — Loop increment (lines 452-465)](#11-step-6--loop-increment-lines-452-465)
12. [Big-picture takeaways](#12-big-picture-takeaways)
13. [Interview questions](#13-interview-questions)
14. [Hands-on exercise](#14-hands-on-exercise)

---

## 1. Mental model

`query_loop` is an async generator. Calling it returns an iterator, not a
value. Each `yield` produces an `AgentEvent` for the caller to consume; the
caller decides when to advance.

```
caller: async for event in query_loop(...):    ← consumer drives the loop
            self._dispatch_agent_event(event)  ← caller does whatever it wants

inside query_loop:
    while True:
        ... do work ...
        yield AgentEvent.x(...)                 ← suspends; caller resumes us
        ... more work ...
```

When you read this file, the `while True` loop is **one user message being
processed**. Each iteration is **one LLM round-trip + tool execution**. The
loop terminates when the model stops asking for tools (return at line 346) or
hits a guard rail (return after error / max-turns / task budget exhaustion).

---

## 2. State (lines 50-64)

```python
@dataclass
class QueryState:
    turn_count: int = 1
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    compact_tracking: AutoCompactTracking | None = None
    task_tokens_used: int = 0
    pending_error: str | None = None
```

Six fields. **Mutated across iterations.** Each one exists for a specific
recovery scenario:

- `turn_count` — caps the loop via `max_turns` (line 501)
- `max_output_tokens_recovery_count` — bounds the "recover from truncation" retry (max 3, line 47)
- `has_attempted_reactive_compact` — bounds the "context too large, force compact" retry to 1 attempt
- `compact_tracking` — used by autocompact to know if it already ran this turn
- `task_tokens_used` — cumulative tokens spent across iterations of this user message
- `pending_error` — stashed stream/API error; the recovery branch decides whether to surface or swallow

This is **per-`agent.run(message)` state**. Created at line 82 — every new
user message gets a fresh state.

The loop's only safeguards against runaway behavior are `max_turns`, the
recovery counters above, and the optional `task_budget_tokens`. There is no
tool-call repetition detector.

---

## 3. Function signature & setup (lines 67-95)

```python
async def query_loop(
    session,
    config,
    permission_prompt_fn=None,
    abort_controller=None,
    hook_executor: HookExecutor | None = None,
) -> AsyncGenerator[AgentEvent, None]:
```


| Argument               | Purpose                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------- |
| `session`              | Owns the LLM client, tool registry, context manager, MCP, JSONL storage                     |
| `config`               | Model name, max_turns, permission_mode, cwd, task_budget_tokens, etc.                       |
| `permission_prompt_fn` | Callback for ASK decisions. The TUI provides this. Without it, ASK becomes DENY (line 402). |
| `abort_controller`     | Checked during streaming (line 180). User Ctrl+C sets it.                                   |
| `hook_executor`        | Snapshotted hook registry from `Agent.__aenter`__. None = empty fallback.                   |


Then setup lines:

```python
state = QueryState()
max_turns = config.max_turns
permission_checker = PermissionChecker(...)
if hook_executor is None:
    hook_executor = HookExecutor()
task_budget = getattr(config, "task_budget_tokens", None)
```

### Hook executor

`Agent.run()` passes its snapshotted `HookExecutor` in via the `hook_executor`
parameter — that's the instance that carries the hooks captured at session
start. The `if hook_executor is None` fallback exists only so the loop is
testable in isolation; production calls always supply a real one. Without it,
`PRE_TOOL_USE`, `POST_TOOL_USE`, and `POST_SAMPLING` would all be no-ops.

---

## 4. The big `while True` (line 100)

Everything below is **one iteration**. Termination paths:

- `return` at the top if task budget is exhausted (line 109)
- `return` after error (lines 159, 182, 317, 340, 346)
- `return` when model stops asking for tools and no recovery fires (line 346)
- `return` when `max_turns` exceeded (line 503)
- `return` when a post-tool hook prevents continuation (line 488)
- `continue` to skip the rest of the iteration and start fresh (lines 154, 203, 314, 335)

---

## 5. STEP 0 — Task budget check (lines 101-109)

```python
if task_budget is not None and state.task_tokens_used >= task_budget:
    yield AgentEvent.agent_error(
        f"Task budget exhausted: spent {state.task_tokens_used:,} of "
        f"{task_budget:,} tokens. Abandoning further iterations."
    )
    return
```

Every iteration starts by checking whether we've blown through the per-message
token budget. `config.task_budget_tokens=None` (the default) means unlimited
and skips the check.

The accumulator (`state.task_tokens_used`) is updated at the end of STEP 2 —
preferring the provider's reported `TokenUsage` when available, falling back
to a `len(text) / 4` estimate when the provider didn't report.

### Gotcha #1 — Budget is per-message, not per-session

State is fresh on every `agent.run(message)` call, so the budget resets per
user prompt. Useful: keeps one runaway answer from killing the whole session.
Pitfall: a session with 100 messages can spend 100× the budget cumulatively;
this is intentional but worth knowing.

---

## 6. STEP 1 — Compact phase (lines 111-122)

**One call site.** The full pipeline lives inside
`ContextManager.get_messages_with_compaction()`, in this order:

```
snip → microcompact → context_collapse → autocompact → tool_result_budget
```

```python
messages = await session.context_manager.get_messages_with_compaction()
```

Inside, in order:

- **snip** — lossless drop of oldest message groups if way over threshold. Cheapest.
- **microcompact** — clears stale tool results (zero-cost filter, runs every iteration).
- **context_collapse** — merges adjacent same-role messages, collapses old tool calls to one-liners. Runs **before** autocompact: if collapse frees enough, autocompact becomes a no-op and we keep granular history instead of a lossy summary.
- **autocompact** — if `current_tokens > threshold`, calls the LLM to summarise history. Lossy.
- **tool_result_budget** — caps aggregate tool result bytes at 200K (`MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`).

The returned `messages` is the API message list ready to send to the LLM.

### Gotcha #2 — Compaction order matters

Two ordering invariants:

1. **Cheapest-first.** Snip is free; microcompact is free; collapse is cheap; autocompact requires an LLM call. Once autocompact summarises, the originals are gone — better to try cheap stuff first.
2. **Collapse before autocompact.** If collapse frees enough to drop us under the autocompact threshold, autocompact skips entirely and we keep granular tool calls instead of a single summary message. Reversing this order causes unnecessary lossy compaction.

### 6c. No synthetic-413 preempt

After the four compaction layers, the loop goes straight to STEP 2 — no
local "are we at the blocking limit?" check before the API call. This is
deliberate with reactive compact enabled: a synthetic-413 preempt would
`return` before the API call, and reactive compact would never see a real
prompt-too-long to react to.

So the flow on overlong context is: compact runs → API call → real 413
comes back → STEP 4 recovery branch detects PROMPT_TOO_LONG → fires
reactive compact → retries. One wasted API call per overflow event in
exchange for a single recovery path that always fires on real server
errors instead of guesses.

---

## 7. STEP 2 — Streaming phase (lines 161-236)

```python
response_text = ""
tool_calls: list[ToolCall] = []
tool_schemas = session.tool_registry.get_schemas()
api_error: str | None = None
turn_usage: TokenUsage | None = None

try:
    async for event in session.client.chat_completion(...):
        if abort_controller and abort_controller.aborted:
            yield AgentEvent.agent_error("Interrupted by user")
            return

        if event.type == StreamEventType.TEXT_DELTA:
            response_text += event.text_delta.content
            yield AgentEvent.text_delta(event.text_delta.content)
        elif event.type == StreamEventType.TOOL_CALL_COMPLETED:
            tool_calls.append(event.tool_call)
        elif event.type == StreamEventType.ERROR:
            ...
            api_error = error_msg     # ← stashed, not yielded

        if event.usage is not None:
            turn_usage = event.usage  # captured for budget tracking

except Exception as e:
    api_error = str(e)                # ← also stashed, not yielded
    ...

# After streaming finishes, update the budget and stash the error
if turn_usage is not None:
    state.task_tokens_used += turn_usage.total_tokens or (...)
elif response_text:
    state.task_tokens_used += estimate_token(response_text)

state.pending_error = api_error
```

**Five things happen during streaming:**

1. **Text accumulates** in `response_text` AND yields to caller as deltas.
2. **Tool calls accumulate** in `tool_calls`.
3. **Abort check** every event.
4. **Token usage captured** for the task budget.
5. **Errors are stashed**, not yielded immediately.

### Gotcha #3 — the `__RETRY_RESET__` marker (lines 200-203)

```python
if error_msg.startswith("__RETRY_RESET__"):
    response_text = ""
    tool_calls = []
    continue
```

`continue` here is `continue` of the `async for event` loop, NOT the outer
`while True`. It means "throw away whatever I've collected and continue
listening to the stream." Handles a race where the LLM streams partial
content, hits an internal retry, and starts over — the marker tells us to
discard the partial.

### Gotcha #4 — Error withholding

The streaming and exception branches don't yield `AgentEvent.agent_error(...)`
directly. They stash the error string into `state.pending_error`. The
continue-decision at STEP 4 inspects the error type:

- **Recoverable** (`PROMPT_TOO_LONG`, `MAX_OUTPUT_TOKENS`) → recovery runs,
pending_error cleared, user never sees the error.
- **Unrecoverable** → surface the stashed error to the user, then return.

This keeps transient errors that recovery handles from leaking out as
user-visible "agent_error" events.

### Gotcha #5 — Provider-agnostic error classification

Errors are classified via `client.llm_client.classify_error()` which maps
provider-specific exception strings to an `ErrorKind` enum. The recovery
branches match on the enum, not the raw string. This is what lets the same
recovery code work for Anthropic, OpenAI, and Azure.

### Gotcha #6 — Overloaded fallback lives in the client, not the loop

Sustained 529/overloaded handling is implemented inside
`client/llm_client.py`: retries with backoff, then a sticky switch to the
configured fallback model (`LLM_FALLBACK_MODEL`). The loop itself never
branches on it — it just sees a fresh stream after the client's
`__RETRY_RESET__` marker.

---

## 8. STEP 3 — Persist + POST_SAMPLING (lines 238-282)

```python
session.context_manager.add_assistant_message(
    content=response_text, tool_calls=tool_calls_data,
)
session.save_message(role="assistant", ...)

if response_text:
    yield AgentEvent.text_complete(content=response_text)

post_sampling = await hook_executor.run_event_hooks(
    event=HookEvent.POST_SAMPLING,
    data={
        "session_id": session.session_id,
        "assistant_text": response_text,
        "tool_calls": [...],
        "turn": state.turn_count,
    },
)
if post_sampling.additional_context:
    session.context_manager.add_user_message(
        f"[post-sampling hook context]\n{post_sampling.additional_context}"
    )
```

Five things:

1. **Convert tool calls to OpenAI-format dicts** — the canonical wire shape (both Anthropic and OpenAI accept this).
2. **Add to context manager** — in-memory message list, used in next iteration's compaction.
3. **Save to JSONL** — durable session log, used by `/resume`.
4. **Yield `text_complete`** — signals the UI: this turn's text is done.
5. **Fire POST_SAMPLING hook** — observers see the full assistant message + intended tool calls *before* tools execute. Hook can return `additional_context` to inject as a synthetic user message.

### Gotcha #7 — POST_SAMPLING fires every iteration

If the model emits 10 turns of tool-driven work, POST_SAMPLING fires 10 times.
Hook authors should make it idempotent / cheap. The hook runs *before* the
continue decision — meaning it sees responses that produced no tool calls
(genuine final answers) as well as intermediate turns.

---

## 9. STEP 4 — Continue decision & recovery (lines 284-346)

```python
needs_follow_up = len(tool_calls) > 0

if not needs_follow_up:
    error_kind = classify_error(state.pending_error) if state.pending_error else ErrorKind.OTHER

    if error_kind == ErrorKind.PROMPT_TOO_LONG:
        # try reactive compact, retry once, clear pending_error
        ...
    if error_kind == ErrorKind.MAX_OUTPUT_TOKENS:
        # inject "resume" message, retry up to 3 times, clear pending_error
        ...

    # No recovery applicable — surface stashed error if any
    if state.pending_error:
        yield AgentEvent.agent_error(state.pending_error)
        state.pending_error = None
    return
```

> The model votes to continue by emitting tool calls.
> The model votes to stop by responding with just text.

That's the entire termination logic. Then two recovery paths kick in:

### Recovery 1: prompt too long (PROMPT_TOO_LONG)

The blocking-limit guard at the top tries to prevent this, but if the *server*
still rejects (API count differs from our estimate), we land here. Force a
reactive compact, clear `pending_error`, `continue` the outer loop. If reactive
compact already ran for this message, give up cleanly.

### Recovery 2: max output tokens (MAX_OUTPUT_TOKENS)

The model hit its output cap mid-thought. Inject a literal "Resume from where
you stopped, no apology" user message and retry. Bound to 3 retries via
`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT`. Recovery clears `pending_error` so the
user never sees the transient error.

### Gotcha #8 — Recovery only fires when no tool calls

Both recovery paths only fire when `not needs_follow_up`. If the model *did*
emit tool calls before erroring, we proceed normally — `pending_error` will be
cleared organically when the next iteration overwrites it. Recovery is for the
case where the model didn't get to do anything meaningful before failing.

### Gotcha #9 — Recovery resets per turn, not per session

STEP 6 (lines 497-498) resets both `max_output_tokens_recovery_count` and
`has_attempted_reactive_compact` on every successful turn advance. So a
long-running session can hit max-output-tokens up to 3 times *per turn* and
still recover.

---

## 10. STEP 5 — Tool execution (lines 348-488)

The biggest section. Four sub-steps:

### 10a. Build tool requests (lines 353-360)

Convert `ToolCall` → `ToolCallRequest` (just a dataclass swap;
`ToolCallRequest` is what `run_tools` accepts).

### 10b. Permission check BEFORE execution (lines 365-409)

```python
for req in tool_requests:
    tool = tools_dict.get(req.name)
    if not tool:
        # unknown tool → immediate error result, never execute
        ...continue

    perm_result = await permission_checker.check(tool, req.arguments)

    if perm_result.behavior == PermissionBehavior.DENY:
        ...continue

    if perm_result.behavior == PermissionBehavior.ASK:
        if permission_prompt_fn:
            approved = await permission_prompt_fn(...)
            if not approved: continue
        else:
            # no prompt fn → DEFAULT DENY, not silent allow
            continue

    approved_requests.append(req)
```

**If there's no `permission_prompt_fn` (CLI mode without UI?) and a tool needs
ASK, we DENY by default.** This is intentional — silent-allow would be a
security hole.

**Each denied tool gets:**

- `AgentEvent.tool_call_start` + `tool_call_complete` (the UI needs both for the bubble lifecycle)
- A `ToolResult.error_result` added to context (so the LLM sees "permission denied" and can react)

Same pattern for unknown tools and hook-blocked tools. **Every tool call
request produces a tool_result in the context** — never just silently
dropped. That's required by the LLM API contract: every `tool_use_id` must
have a matching `tool_result`.

### 10c. Pre-tool hooks (lines 414-435)

```python
for req in approved_requests:
    hook_result = await hook_executor.run_pre_tool_hooks(...)
    if hook_result.blocking_error:
        # hook said no → emit error result, skip
        continue
    if hook_result.updated_input:
        req.arguments = hook_result.updated_input
    hook_approved.append(req)
```

Hooks can either **block** the tool call (`blocking_error`) or **rewrite its
input** (`updated_input`). Then we proceed with the surviving list.

### 10d. Execute (lines 437-486)

```python
async for result in run_tools(hook_approved, tools_dict, config.cwd):
    yield AgentEvent.tool_call_start(...)
    yield AgentEvent.tool_call_complete(...)

    tool_output = result.result.to_model_output()
    session.context_manager.add_tool_result(...)
    session.save_message(...)

    if result.result.metadata.get("set_permission_mode"):
        config.permission_mode = result.result.metadata["set_permission_mode"]

    post_hook = await hook_executor.run_post_tool_hooks(...)
    if post_hook.blocking_error:
        # inject feedback as a user message; loop continues
        session.context_manager.add_user_message(
            f"[hook feedback] {post_hook.blocking_error}"
        )
    elif post_hook.prevent_continuation:
        should_stop = True
```

`run_tools` is an async iterator that **yields results in REQUEST order**. It
internally batches reads into parallel groups and writes serially (see
`services/tools/orchestration.py`). For each result: emit UI events, add to
context, save to JSONL, run post-tool hook.

### Gotcha #10 — Permission mode mutation mid-turn

Specific tools (`PlanModeTool`, `ExitPlanModeTool`) signal mode changes by
setting `set_permission_mode` in their result metadata. This config mutation
takes effect on the **next** turn — the current turn's permission decisions
are already made. If two tools change the mode in the same turn, last write
wins.

### Gotcha #11 — Two distinct post-hook signals

Post-tool hooks can return two different signals:


| Signal                 | What happens                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------ |
| `blocking_error`       | Inject hook's message as a synthetic user message. Loop **continues** with feedback. |
| `prevent_continuation` | Set `should_stop = True`. Loop **stops** after current tool batch finishes.          |


The distinction matters: `blocking_error` says "tell the agent to fix this and
try again." `prevent_continuation` says "you're done, stop now."

---

## 11. STEP 6 — Loop increment (lines 490-503)

```python
state.turn_count += 1
session.increment_turn()

# Reset recovery state on new turn
state.max_output_tokens_recovery_count = 0
state.has_attempted_reactive_compact = False

if state.turn_count > max_turns:
    yield AgentEvent.agent_error(f"Max turns ({max_turns}) reached")
    return
```

Three things:

1. Bump the counter (both local state and session telemetry).
2. **Reset the recovery counters** — each new turn starts fresh. So you get
  up to 3 max-output-tokens retries *per turn*, not per session.
3. Hard cap on turn count.

Note: `task_tokens_used` is **not reset here** — it accumulates across the
whole user message, which is the whole point of the budget.

---

## 12. Big-picture takeaways

### What `query_loop` IS responsible for

- The state machine (budget → compact → stream → permissions → hooks → execute → continue)
- Recovery from API errors (PROMPT_TOO_LONG via reactive compact, MAX_OUTPUT_TOKENS via resume injection)
- Per-tool permission gating
- Per-message task budget enforcement
- Bookkeeping (turn count, token usage, context updates, JSONL persistence)
- Lifecycle hook firing (POST_SAMPLING, PRE_TOOL_USE, POST_TOOL_USE)

### What `query_loop` is NOT responsible for


| Concern                                      | Lives in                                     |
| -------------------------------------------- | -------------------------------------------- |
| Compaction algorithms                        | `services/compact/`                          |
| LLM streaming details + error classification | `client/llm_client.py`                       |
| Permission decisions                         | `permissions/checker.py`                     |
| Tool execution mechanics                     | `services/tools/orchestration.py::run_tools` |
| Session lifecycle / hooks setup              | `agent/agent.py::__aenter__/__aexit`__       |
| Hook command execution                       | `hooks/executor.py`                          |


---

## 13. Interview questions

1. **"How does a turn end?"**
  The model votes to stop by responding with just text (no tool_calls).
   Line 288: `needs_follow_up = len(tool_calls) > 0`.
2. **"What happens if the LLM returns a 413 / context-length error?"**
  Caught at line 294 via `classify_error` → `ErrorKind.PROMPT_TOO_LONG`.
   Force one reactive compact, retry. If reactive already ran for this
   message, surface `agent_error("Context too large...")` and stop.
3. **"How do you prevent infinite loops?"**
  Two ways. (a) `max_turns` hard cap. (b) Per-message
   `task_budget_tokens` if configured. There is no tool-call repetition
   heuristic.
4. **"How are permissions enforced?"**
  Always before execution, line 380. Three behaviors: DENY → error result;
   ASK → prompt user (or default-deny if no prompt fn); ALLOW → proceed.
   Per-tool, sequentially.
5. **"What if the user Ctrl+Cs?"**
  `AbortController` is checked at line 180 inside the streaming loop. We
   yield an error event and return. Note: tools already executing don't get
   cancelled — they finish or hang.
6. **"How does the model get tool results?"**
  Each tool result is added to `context_manager` (line 448) and the
   OpenAI/Anthropic chat history. Next iteration's `chat_completion` call
   sends them as `role: tool` messages.
7. **"Why is `run_tools` a generator?"**
  It yields results in request order as they finish. Reads run in parallel,
   writes serial. The yielding lets the UI show tool completion live without
   waiting for the slowest tool.
8. **"Where does compaction happen?"**
  Lines 111-159. Four layers, ordered cheapest-first. The expensive one
   (autocompact) only runs if cheaper ones didn't free enough.
9. **"How does error withholding work?"**
  Stream errors and exceptions are stashed in `state.pending_error` instead
   of yielded immediately. Recovery branches in STEP 4 inspect the
   `ErrorKind` and either swallow the error (after handling) or surface it.
   This avoids showing transient errors that we recover from.
10. **"Why did you add a task budget when there's already `max_turns`?"**
  `max_turns` bounds *iterations*; `task_budget_tokens` bounds *spend*. A
    single iteration can burn 30k tokens (long context + long response), so
    `max_turns=100` doesn't actually bound dollar cost. Token budget does.
11. **"How does the loop work with three different LLM providers?"**
  The loop only sees `StreamEvent`s and a unified exception surface.
    Provider-specific error formats are normalised into `ErrorKind` via
    `classify_error()` in `client/llm_client.py`. The loop never branches on
    provider — that's the whole point of the seam.
12. **"What's the most fragile part of this code?"**
  The error classification table in `classify_error()`. Recovery depends on
    matching specific error message substrings; if a provider changes their
    error wording, recovery silently breaks. The enum gives us a place to
    add tests but doesn't eliminate the fragility.

---

## 14. Hands-on exercise

Spend 10 minutes:

1. Open `query/loop.py` in your IDE.
2. Find each of the 11 gotchas above by file:line.
3. For each, read the surrounding 5-10 lines and ask yourself:
  *"What would happen if this line were removed?"*

That exercise will make you fluent in this file faster than any walkthrough.

---

## Quick reference — line ranges


| Lines   | What                                                          |
| ------- | ------------------------------------------------------------- |
| 1-19    | Module docstring (architecture summary)                       |
| 21-43   | Imports                                                       |
| 47      | `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT` constant                   |
| 50-65   | `QueryState` dataclass (6 fields)                             |
| 67-83   | `query_loop` signature + docstring                            |
| 84-98   | Setup (state, permission checker, hook executor, task budget) |
| 100     | `while True`                                                  |
| 101-109 | STEP 0: Task budget check                                     |
| 111-122 | STEP 1: Compact pipeline (one call into ContextManager)       |
| 124-198 | STEP 2: Model streaming + budget update + error stash         |
| 200-223 | STEP 3a: Persist assistant message                            |
| 225-244 | STEP 3b: POST_SAMPLING hook                                   |
| 246-308 | STEP 4: Continue decision + recovery                          |
| 316-323 | Build tool requests                                           |
| 328-371 | Permission check (before execution)                           |
| 376-397 | Pre-tool hooks                                                |
| 399-449 | Execute + post-tool hooks                                     |
| 453-465 | STEP 6: Loop increment + max_turns                            |


