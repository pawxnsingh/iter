# Agent Architecture — Diagrams

A visual companion to the agentic spine. Read top to bottom: each diagram zooms
in one level. File:line references point at the live code.

> The one idea: the agent is a **loop of LLM round-trips**. Each round-trip the
> model either emits **tool calls** ("not done — run these and ask me again") or
> emits **only text** ("done"). Everything else hangs off that fork.
> See `query/loop.py:222` → `needs_follow_up = len(tool_calls) > 0`.

---

## 1. Component wiring — the spine and its spokes

`Agent` and `Session` are thin assembly points; the logic lives in `query_loop`.

```mermaid
flowchart TD
    MAIN["main.py → TUI"] --> AGENT

    subgraph AGENT["Agent — agent/agent.py"]
        AENTER["__aenter__ : session setup"]
        RUN["run(message) : one user turn"]
        AEXIT["__aexit__ : teardown"]
    end

    AGENT --> SESSION

    subgraph SESSION["Session — agent/session.py (the wiring)"]
        CLIENT["LLMClient<br/>client/llm_client.py"]
        REG["ToolRegistry<br/>tools/registry.py"]
        CTX["ContextManager<br/>context/manager.py"]
        STORE["SessionStorage<br/>session/storage.py"]
    end

    RUN --> LOOP["query_loop<br/>query/loop.py"]

    LOOP --> CLIENT
    LOOP --> CTX
    LOOP --> REG
    LOOP --> PERM["PermissionChecker<br/>permissions/*"]
    LOOP --> HOOKS["HookExecutor<br/>hooks/executor.py"]
    LOOP --> ORCH["run_tools<br/>services/tools/orchestration.py"]
    LOOP --> STORE

    CTX --> COMPACT["compaction pipeline<br/>services/compact/*"]
    REG --> TOOLS["builtin tools<br/>tools/builtin/*"]
    REG --> MCP["external tools<br/>tools/mcp/*"]

    style LOOP fill:#e36414,color:#fff
    style AGENT fill:#0f3460,color:#fff
    style SESSION fill:#16213e,color:#fff
```

---

## 2. Session lifecycle — the three phases

`__aenter__` and `__aexit__` run **once**; `run()` runs **per message**. Note
where the hooks fire.

```mermaid
sequenceDiagram
    participant U as User / UI
    participant A as Agent
    participant L as query_loop
    participant H as HookExecutor

    rect rgb(15,52,96)
    Note over A: __aenter__ (once)
    A->>A: load settings.json, permission rules
    A->>H: SessionStart hook
    A->>A: build context reminder (AGENT.md + date)
    end

    rect rgb(45,106,79)
    U->>A: message
    Note over A: run(message)
    A->>H: UserPromptSubmit hook
    A->>A: add_user_message + save to transcript
    A->>L: query_loop(...)
    L-->>U: stream AgentEvents (text, tool calls)
    L-->>A: loop ends
    end

    rect rgb(22,33,62)
    Note over A: __aexit__ (once)
    A->>H: SessionEnd hook
    A->>A: save metadata, close client + MCP
    end
```

---

## 3. The query loop — state machine (the heart)

One pass through this = one LLM round-trip plus its tool execution. This matches
`query/loop.py:109` onward exactly.

```mermaid
flowchart TD
    START([loop iteration]) --> BUDGET{task budget<br/>exhausted?}
    BUDGET -->|yes| ERR1([agent_error → return])
    BUDGET -->|no| BUILD["get_messages_with_compaction()<br/>runs compaction pipeline"]
    BUILD --> STREAM["stream model<br/>client.chat_completion()"]
    STREAM --> PERSIST["add_assistant_message + save<br/>emit text_complete"]
    PERSIST --> PS["POST_SAMPLING hook"]
    PS --> FORK{tool calls<br/>emitted?}

    FORK -->|"no (only text)"| RECOVER{recoverable<br/>error?}
    RECOVER -->|prompt too long| RC["reactive compact once<br/>→ continue"]
    RECOVER -->|max output tokens| MR["nudge + retry up to 3<br/>→ continue"]
    RECOVER -->|other error| ERR2([agent_error → return])
    RECOVER -->|none| STOPHOOK{Stop hook<br/>blocks?}
    STOPHOOK -->|yes| CONT1["add feedback → continue"]
    STOPHOOK -->|no| DONE([return — done])
    RC --> START
    MR --> START
    CONT1 --> START

    FORK -->|yes| PERLOOP["for each tool call:<br/>PreToolUse hook → permission gate"]
    PERLOOP --> RUN["run_tools (parallel reads / serial writes)"]
    RUN --> EACH["per result: emit events,<br/>persist, PostToolUse hook"]
    EACH --> INC["turn += 1, reset recovery flags"]
    INC --> MAXT{turn > max_turns?}
    MAXT -->|yes| ERR3([agent_error → return])
    MAXT -->|no| START

    style FORK fill:#0f3460,color:#fff
    style RUN fill:#52b788,color:#fff
    style STREAM fill:#e36414,color:#fff
    style DONE fill:#2d6a4f,color:#fff
```

---

## 4. One round-trip — message flow

The concrete path of a single iteration across the collaborators. Two of these
back-to-back is "read a file, then edit it."

```mermaid
sequenceDiagram
    participant L as query_loop
    participant C as ContextManager
    participant M as LLMClient / Model
    participant P as PermissionChecker
    participant O as run_tools
    participant T as Tool

    L->>C: get_messages_with_compaction()
    C-->>L: API messages (system prompt + history)
    L->>M: chat_completion(messages, tool schemas)
    M-->>L: stream text deltas + tool calls
    L->>C: add_assistant_message + save

    alt model emitted tool calls
        loop each tool call
            L->>P: check(tool, args)
            P-->>L: ALLOW / DENY / ASK
        end
        L->>O: run_tools(approved)
        O->>T: execute()
        T-->>O: ToolResult
        O-->>L: results (streamed)
        L->>C: add_tool_result + save
        Note over L,C: loop again — next round-trip
    else only text
        Note over L: Stop hook → return (done)
    end
```

---

## 5. Compaction pipeline

Runs inside `get_messages_with_compaction()` before **every** API call
(`context/manager.py:220`). Order matters: cheap/lossless passes first, the
expensive LLM summary last. `reactive_compact` is a **separate emergency path**
the loop triggers only after the API rejects an over-long prompt.

```mermaid
flowchart LR
    IN["messages"] --> B["1. apply_tool_result_budget<br/>cap each result at 200K chars"]
    B --> S["2. snip_compact<br/>drop oldest groups (lossless)"]
    S --> MI["3. micro_compact<br/>NO-OP — see its docstring"]
    MI --> CO["4. context_collapse<br/>merge same-role + collapse old tool calls"]
    CO --> AC{over auto-compact<br/>threshold?}
    AC -->|yes| LLM["auto_compact<br/>LLM summary (expensive)"]
    AC -->|no| OUT["_build_api_messages()<br/>prepend system prompt + reminder"]
    LLM --> OUT

    API["API returns 413<br/>'prompt too long'"] -. triggers .-> RC["reactive_compact<br/>strip images + summarize, retry once"]
    RC -. retry .-> IN

    style MI fill:#6c757d,color:#fff
    style LLM fill:#f77f00,color:#fff
    style RC fill:#d62828,color:#fff
```

---

## 6. Permission decision

The gate every tool call passes before execution (`query/loop.py:341-424`,
`permissions/checker.py`). A PreToolUse hook can short-circuit; otherwise rules
(deny > allow > ask) then mode + filesystem safety decide.

```mermaid
flowchart TD
    START([tool call]) --> PRE["PreToolUse hook"]
    PRE --> HD{hook decision}
    HD -->|deny| DENY["DENY → error result"]
    HD -->|ask| ASKP["ASK → permission_prompt_fn (UI)"]
    HD -->|allow| RONLY{deny rule<br/>still matches?}
    RONLY -->|yes| DENY
    RONLY -->|no| ALLOW["ALLOW → execute"]
    HD -->|none| CHECK["permission_checker.check()"]

    CHECK --> RULES{rule match?}
    RULES -->|deny rule| DENY
    RULES -->|allow rule| ALLOW
    RULES -->|ask rule| ASKP
    RULES -->|no match| MODE{mode + filesystem<br/>safety}

    MODE -->|"bypass / acceptEdits / read"| ALLOW
    MODE -->|"default: needs confirm"| ASKP
    MODE -->|"protected path (.git, rc files)"| ASKP

    ASKP -->|approved| ALLOW
    ASKP -->|rejected| DENY

    style ALLOW fill:#2d6a4f,color:#fff
    style DENY fill:#d62828,color:#fff
    style ASKP fill:#0f3460,color:#fff
```

---

## 7. Tool execution — parallel reads, serial writes

`partition_tool_calls` (`services/tools/orchestration.py:56`) groups consecutive
concurrency-safe (read-only) tools into one parallel batch; any write tool gets
its own serial batch. This is why the loop can fan out reads but never races two
writes.

```mermaid
sequenceDiagram
    participant M as Model
    participant O as Orchestrator
    participant B1 as Batch 1 (parallel)
    participant B2 as Batch 2 (serial)
    participant B3 as Batch 3 (parallel)

    M->>O: [Grep, Glob, Read, Edit, Grep, Read]

    O->>B1: Grep + Glob + Read (concurrent, semaphore=10)
    Note over B1: all concurrency-safe
    B1-->>O: results

    O->>B2: Edit (alone)
    Note over B2: NOT concurrency-safe
    B2-->>O: result

    O->>B3: Grep + Read (concurrent)
    Note over B3: all concurrency-safe
    B3-->>O: results

    O-->>M: all 6 tool results (streamed back in order)
```

---

## Where to go in the code

| Diagram | Primary file(s) |
|---|---|
| 1. Wiring | `agent/agent.py`, `agent/session.py` |
| 2. Lifecycle | `agent/agent.py` (`__aenter__`, `run`, `__aexit__`) |
| 3. Loop state machine | `query/loop.py` |
| 4. Round-trip flow | `query/loop.py`, `context/manager.py`, `client/llm_client.py` |
| 5. Compaction | `context/manager.py:220`, `services/compact/*` |
| 6. Permissions | `permissions/checker.py`, `core_types/permissions.py` |
| 7. Tool execution | `services/tools/orchestration.py` |
