"""
Flexible end-to-end test harness for the agentic core — no static asserts.

This is a thin runner. Claude Code drives the testing in the conversation
by invoking specific scenarios and reading the JSON-structured results.

Usage:
    uv run main_test.py "your prompt"                    # run a single prompt
    uv run main_test.py "prompt" --json                  # JSON output (for me to parse)
    uv run main_test.py "prompt" --cwd /tmp/x            # custom cwd
    uv run main_test.py "prompt" --max-turns 3
    uv run main_test.py "prompt" --permission bypass     # mode: default/plan/bypass/accept_edits/dont_ask
    uv run main_test.py "prompt" --deny-all              # auto-deny all permission prompts
    uv run main_test.py "prompt" --setup-files           # populate cwd with README.md, hello.py, data.txt, src/
    uv run main_test.py "prompt" --tempdir               # run in a fresh tempdir, optionally with --setup-files
    uv run main_test.py "prompt" --quiet                 # suppress agent's debug prints
    uv run main_test.py --inspect-schemas                # dump tool inventory, no LLM
    uv run main_test.py --direct-bash-security           # unit-style: BashTool blocks rm -rf /, mkfs, fork bomb
    uv run main_test.py --direct-max-turns               # unit-style: max_turns=3 fires "Max turns reached"
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import logging
import shutil
import signal
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agent.agent import Agent  # noqa: E402
from agent.event import AgentEventType  # noqa: E402
from config.config import Config  # noqa: E402


# ─── Colors ──────────────────────────────────────────────────────────────

C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_RED = "\033[31m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_CYAN = "\033[36m"
C_MAGENTA = "\033[35m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{C_RESET}"


def setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(name)-30s %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "anthropic", "openai", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ─── Result schema ───────────────────────────────────────────────────────

@dataclass
class ToolCallRecord:
    name: str
    arguments: dict
    success: bool | None = None
    output: str = ""


@dataclass
class RunResult:
    prompt: str
    cwd: str
    text_output: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0
    ended_cleanly: bool = False
    files_after: list[str] = field(default_factory=list)  # files in cwd after run

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


# ─── Permission prompts ──────────────────────────────────────────────────

async def always_approve(tool_name: str, message: str, is_destructive: bool) -> bool:
    return True


async def always_deny(tool_name: str, message: str, is_destructive: bool) -> bool:
    return False


# ─── Setup helpers ───────────────────────────────────────────────────────

def setup_test_files(cwd: Path) -> None:
    """Populate cwd with a small predictable fixture set."""
    (cwd / "README.md").write_text("# Test Project\n\nThis is a fixture for end-to-end tests.\n")
    (cwd / "hello.py").write_text("def greet(name):\n    return f'hello {name}'\n")
    (cwd / "data.txt").write_text("line one\nline two\nline three\nline four\nline five\n")
    sub = cwd / "src"
    sub.mkdir(exist_ok=True)
    (sub / "a.py").write_text("import os\nprint('a')\n")
    (sub / "b.py").write_text("import sys\nprint('b')\n")
    (sub / "c.py").write_text("def func_c():\n    pass\n")


def list_files(cwd: Path) -> list[str]:
    """Recursive file listing relative to cwd (max depth 3)."""
    if not cwd.exists():
        return []
    out = []
    for p in cwd.rglob("*"):
        if p.is_file():
            try:
                rel = p.relative_to(cwd)
                if len(rel.parts) <= 3:
                    out.append(str(rel))
            except ValueError:
                pass
    return sorted(out)


# ─── Core runner ─────────────────────────────────────────────────────────

@asynccontextmanager
async def agent_session(
    cwd: Path | None = None,
    max_turns: int | None = None,
    permission_mode: str | None = None,
    permission_prompt=always_approve,
):
    config = Config()
    if cwd is not None:
        config.cwd = cwd
    if max_turns is not None:
        config.max_turns = max_turns
    if permission_mode is not None:
        config.permission_mode = permission_mode

    errors = config.validate()
    if errors:
        raise RuntimeError(f"Config errors: {errors}")

    async with Agent(config, permission_prompt) as agent:
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, agent.abort)
        except (NotImplementedError, ValueError):
            pass
        try:
            yield agent
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, ValueError):
                pass


async def run_once(
    prompt: str,
    cwd: Path | None = None,
    max_turns: int | None = None,
    permission_mode: str | None = None,
    permission_prompt=always_approve,
    quiet: bool = False,
) -> RunResult:
    actual_cwd = cwd or Path.cwd()
    result = RunResult(prompt=prompt, cwd=str(actual_cwd))
    t0 = time.monotonic()

    stdout_buf = io.StringIO() if quiet else None
    stdout_ctx = contextlib.redirect_stdout(stdout_buf) if quiet else contextlib.nullcontext()

    try:
        with stdout_ctx:
            async with agent_session(
                cwd=cwd,
                max_turns=max_turns,
                permission_mode=permission_mode,
                permission_prompt=permission_prompt,
            ) as agent:
                # Map tool_call_start by call_id so we can attach result later
                pending: dict[str, ToolCallRecord] = {}
                async for event in agent.run(prompt):
                    result.event_types.append(event.type.value if hasattr(event.type, "value") else str(event.type))
                    if event.type == AgentEventType.TEXT_DELTA:
                        result.text_output += event.data.get("content", "")
                    elif event.type == AgentEventType.TOOL_CALL_START:
                        rec = ToolCallRecord(
                            name=event.data.get("name", ""),
                            arguments=event.data.get("arguments", {}),
                        )
                        pending[event.data.get("call_id", "")] = rec
                        result.tool_calls.append(rec)
                    elif event.type == AgentEventType.TOOL_CALL_COMPLETE:
                        cid = event.data.get("call_id", "")
                        rec = pending.get(cid)
                        if rec:
                            rec.success = bool(event.data.get("success"))
                            rec.output = (event.data.get("output") or event.data.get("error") or "")[:1000]
                    elif event.type == AgentEventType.AGENT_ERROR:
                        result.errors.append(event.data.get("error", ""))
                    elif event.type == AgentEventType.AGENT_END:
                        result.ended_cleanly = True
    finally:
        result.wall_time_s = time.monotonic() - t0
        result.files_after = list_files(actual_cwd)
    return result


# ─── Direct unit-style tests (bypass the LLM) ────────────────────────────

async def direct_bash_security_test(cwd: Path | None = None) -> RunResult:
    """Verify bash_security blocks destructive commands regardless of the model."""
    from tools.base import ToolInvocation
    from tools.builtin.BashTool import BashTool

    config = Config()
    if cwd:
        config.cwd = cwd
    tool = BashTool(config)

    actual_cwd = cwd or Path.cwd()
    result = RunResult(prompt="[DIRECT] bash_security against destructive commands", cwd=str(actual_cwd))
    t0 = time.monotonic()

    blocked = ["rm -rf /", "rm -rf /*", "mkfs.ext4 /dev/sda", ":(){ :|:& };:"]
    for cmd in blocked:
        r = await tool.execute(ToolInvocation(params={"command": cmd}, cwd=actual_cwd))
        rec = ToolCallRecord(name="Bash", arguments={"command": cmd}, success=r.success, output=(r.output or r.error or "")[:300])
        result.tool_calls.append(rec)
        if r.success:
            result.errors.append(f"FAILED to block: {cmd!r}")

    result.ended_cleanly = True
    result.wall_time_s = time.monotonic() - t0
    result.files_after = list_files(actual_cwd)
    return result


async def direct_max_turns_test(cwd: Path | None = None) -> RunResult:
    """Verify max_turns guard fires regardless of model behavior."""
    from agent.session import Session
    from client.response import StreamEvent, StreamEventType, ToolCall
    from query.loop import query_loop

    config = Config()
    if cwd:
        config.cwd = cwd
    config.max_turns = 3

    session = Session(config=config)
    await session.initialize()

    call_count = {"n": 0}

    async def fake_chat(messages, tools, stream):
        call_count["n"] += 1
        yield StreamEvent(
            type=StreamEventType.TOOL_CALL_COMPLETED,
            tool_call=ToolCall(
                call_id=f"call_{call_count['n']}",
                name="Bash",
                arguments={"command": f"echo iter_{call_count['n']}"},
            ),
        )

    session.client.chat_completion = fake_chat

    actual_cwd = cwd or Path.cwd()
    result = RunResult(prompt="[DIRECT] max_turns=3 forces stop on 4th iteration", cwd=str(actual_cwd))
    t0 = time.monotonic()
    pending: dict[str, ToolCallRecord] = {}
    async for event in query_loop(session, config, permission_prompt_fn=None):
        result.event_types.append(event.type.value)
        if event.type.value == "tool_call_start":
            rec = ToolCallRecord(name=event.data.get("name", ""), arguments=event.data.get("arguments", {}))
            pending[event.data.get("call_id", "")] = rec
            result.tool_calls.append(rec)
        elif event.type.value == "tool_call_complete":
            rec = pending.get(event.data.get("call_id", ""))
            if rec:
                rec.success = bool(event.data.get("success"))
                rec.output = (event.data.get("output") or "")[:300]
        elif event.type.value == "agent_error":
            result.errors.append(event.data.get("error", ""))

    result.ended_cleanly = True
    result.wall_time_s = time.monotonic() - t0
    if hasattr(session.client, "close"):
        try:
            await session.client.close()
        except Exception:
            pass
    return result


# ─── Pretty printer (interactive mode) ──────────────────────────────────

def print_result(r: RunResult) -> None:
    print()
    print(color("─" * 72, C_DIM))
    print(color(f"cwd:        {r.cwd}", C_DIM))
    print(color(f"wall:       {r.wall_time_s:.1f}s", C_DIM))
    print(color(f"clean end:  {r.ended_cleanly}", C_DIM))
    print(color(f"tools:      {len(r.tool_calls)} calls", C_DIM))
    for tc in r.tool_calls:
        marker = color("✓", C_GREEN) if tc.success else color("✗", C_RED) if tc.success is False else color("?", C_YELLOW)
        args_short = json.dumps(tc.arguments)[:80]
        out_short = tc.output.replace("\n", " ")[:100]
        print(f"  {marker} {tc.name}({args_short})")
        if out_short:
            print(color(f"      → {out_short}", C_DIM))
    if r.errors:
        print(color(f"errors:     {len(r.errors)}", C_RED))
        for e in r.errors:
            print(color(f"  ✗ {e[:200]}", C_RED))
    if r.text_output:
        print(color(f"text:       {len(r.text_output)} chars", C_DIM))
        preview = r.text_output[:500].replace("\n", "\n  ")
        print(f"  {preview}")
    print()


# ─── Schema inspection ──────────────────────────────────────────────────

def inspect_schemas() -> None:
    from tools.registry import create_default_registry
    config = Config()
    registry = create_default_registry(config)
    print(color("\n  TOOL INVENTORY\n", C_BOLD))
    print(f"  {'Tool':<22} {'desc':>5} {'prompt':>7} {'total':>7}")
    print(f"  {'-'*22} {'-'*5} {'-'*7} {'-'*7}")
    total_prompt = 0
    for t in registry.get_tools():
        d = len(t.description or "")
        p = len(t.prompt or "")
        total = len(t.to_openai_schema()["description"])
        total_prompt += p
        marker = color("verbatim", C_GREEN) if p else color("        ", C_DIM)
        print(f"  {t.name:<22} {d:>5} {p:>7} {total:>7}  {marker}")
    print(f"\n  Total: {len(registry.get_tools())} tools, {total_prompt:,} chars of prompt content")

    print(color("\n  BUILT-IN AGENTS\n", C_BOLD))
    from tools.builtin.AgentTool.AgentTool import BUILT_IN_AGENTS
    for name, defn in BUILT_IN_AGENTS.items():
        sp = defn.resolve_system_prompt() or ""
        print(f"  {name:<22} prompt={len(sp):>5}  model={defn.model:<8}  disallowed={len(defn.disallowed_tools)}")
    print()


# ─── Entrypoint ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Flexible agent test runner")
    parser.add_argument("prompt", nargs="?", default=None, help="Prompt to send the agent")
    parser.add_argument("--cwd", default=None, help="Working directory override")
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--permission", default=None, choices=["default", "plan", "bypass", "accept_edits", "dont_ask"])
    parser.add_argument("--deny-all", action="store_true", help="auto-deny all permission prompts")
    parser.add_argument("--setup-files", action="store_true", help="populate cwd with fixture files")
    parser.add_argument("--tempdir", action="store_true", help="run in a fresh tempdir")
    parser.add_argument("--json", action="store_true", help="emit JSON result (parseable)")
    parser.add_argument("--quiet", action="store_true", help="suppress agent's debug prints")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--inspect-schemas", action="store_true")
    parser.add_argument("--direct-bash-security", action="store_true")
    parser.add_argument("--direct-max-turns", action="store_true")
    args = parser.parse_args()

    setup_logging(args.debug)

    if args.inspect_schemas:
        inspect_schemas()
        return 0

    # Resolve cwd
    tempdir = None
    if args.tempdir:
        tempdir = tempfile.mkdtemp(prefix="agent_e2e_")
        cwd = Path(tempdir)
    elif args.cwd:
        cwd = Path(args.cwd)
    else:
        cwd = None

    if args.setup_files and cwd:
        setup_test_files(cwd)

    permission_prompt = always_deny if args.deny_all else always_approve

    try:
        if args.direct_bash_security:
            result = asyncio.run(direct_bash_security_test(cwd=cwd))
        elif args.direct_max_turns:
            result = asyncio.run(direct_max_turns_test(cwd=cwd))
        else:
            prompt = args.prompt or "Reply with exactly: ok"
            result = asyncio.run(run_once(
                prompt=prompt,
                cwd=cwd,
                max_turns=args.max_turns,
                permission_mode=args.permission,
                permission_prompt=permission_prompt,
                quiet=args.quiet,
            ))

        if args.json:
            print(result.to_json())
        else:
            print_result(result)
    except KeyboardInterrupt:
        print(color("\n[interrupted]", C_YELLOW))
        return 130
    finally:
        if tempdir:
            shutil.rmtree(tempdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
