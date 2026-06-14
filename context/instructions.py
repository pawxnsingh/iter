"""
Instruction loading: AGENT.md hierarchy.

Loading hierarchy (lowest to highest priority):
  1. /etc/ai-agent/AGENT.md        (managed, all users)
  2. ~/.config/ai-agent/AGENT.md   (user, all projects)
  3. Walk up from CWD to root:
     - AGENT.md
     - .ai-agent/AGENT.md
     - .ai-agent/rules/*.md
  4. AGENT.local.md                (local, private)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir


@dataclass
class InstructionFile:
    """A loaded instruction file."""
    path: str
    type: str
    content: str


def _load_instruction_file(path: Path, type_name: str) -> InstructionFile | None:
    """Read an instruction file; return None on any read error.

    Centralises the 'try-read-text-or-skip' pattern so the loader
    below stays declarative.
    """
    try:
        return InstructionFile(str(path), type_name, path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_all_instructions(cwd: str) -> list[InstructionFile]:
    """
    Load instruction files following the exact hierarchy.
    Files closer to CWD have HIGHER priority (loaded last).
    """
    instructions: list[InstructionFile] = []

    managed = Path("/etc/ai-agent/AGENT.md")
    if managed.exists():
        if (instr := _load_instruction_file(managed, "managed")) is not None:
            instructions.append(instr)

    user_path = Path(user_config_dir("ai-agent")) / "AGENT.md"
    if user_path.exists():
        if (instr := _load_instruction_file(user_path, "user")) is not None:
            instructions.append(instr)

    project_files: list[InstructionFile] = []
    current = Path(cwd).resolve()

    while current != current.parent:
        for name in ["AGENT.md", ".ai-agent/AGENT.md"]:
            f = current / name
            if f.is_file():
                if (instr := _load_instruction_file(f, "project")) is not None:
                    project_files.append(instr)

        rules_dir = current / ".ai-agent" / "rules"
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                if (instr := _load_instruction_file(rule_file, "project")) is not None:
                    project_files.append(instr)

        current = current.parent

    instructions.extend(reversed(project_files))

    local = Path(cwd) / "AGENT.local.md"
    if local.is_file():
        if (instr := _load_instruction_file(local, "local")) is not None:
            instructions.append(instr)

    return instructions


def format_instructions_for_prompt(instructions: list[InstructionFile]) -> str:
    """Format loaded instructions for inclusion in system prompt."""
    if not instructions:
        return ""

    parts = []
    for instr in instructions:
        parts.append(f"# Instructions ({instr.type}: {instr.path})\n\n{instr.content}")

    return "\n\n".join(parts)


def _git_status_block(cwd: str) -> str | None:
    """getGitStatus: a snapshot of branch / main branch /
    git user / `git status --short` / recent commits. None if not a git repo."""
    import subprocess

    def run(*args: str) -> str:
        try:
            r = subprocess.run(["git", *args], capture_output=True, text=True,
                               cwd=cwd, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    if run("rev-parse", "--is-inside-work-tree") != "true":
        return None
    branch = run("branch", "--show-current")
    main = (run("symbolic-ref", "refs/remotes/origin/HEAD").rsplit("/", 1)[-1]) or "main"
    user = run("config", "user.name")
    raw_status = run("status", "--short")
    if len(raw_status) > 2000:
        status = raw_status[:2000] + (
            '\n... (truncated because it exceeds 2k characters. If you need more '
            'information, run "git status" using BashTool)'
        )
    else:
        status = raw_status
    commits = run("log", "--oneline", "-n", "5")
    parts = [
        "This is the git status at the start of the conversation. Note that "
        "this status is a snapshot in time, and will not update during the "
        "conversation.",
        f"Current branch: {branch}",
        f"Main branch (you will usually use this for PRs): {main}",
        f"Git user: {user}" if user else None,
        f"Status:\n{status}" if status else "Status:\n(clean)",
        f"Recent commits:\n{commits}" if commits else None,
    ]
    return "\n\n".join(p for p in parts if p)


_MEMORY_INSTRUCTION_PROMPT = (
    "Codebase and user instructions are shown below. Be sure to adhere to these "
    "instructions. IMPORTANT: These instructions OVERRIDE any default behavior "
    "and you MUST follow them exactly as written."
)


def _memory_type_description(type_name: str) -> str:
    """Per-file type description appended after the path.
    Our InstructionFile.type ∈ {managed, user, project, local}."""
    if type_name == "project":
        return " (project instructions, checked into the codebase)"
    if type_name == "local":
        return " (user's private project instructions, not checked in)"
    return " (user's private global instructions for all projects)"


def build_user_context_reminder(cwd: str, current_date: str) -> str:
    """Build the turn-1 <system-reminder> user-context message: agentMd +
    currentDate ONLY — NOT gitStatus. gitStatus is system-context, appended to
    the system prompt by build_system_context_suffix.

    Delivered via a meta USER message so the system prompt stays cache-stable
    while the dynamic memory/date varies.
    """
    context: dict[str, str] = {}

    instrs = load_all_instructions(cwd)
    memories = [
        f"Contents of {i.path}{_memory_type_description(i.type)}:\n\n{i.content.strip()}"
        for i in instrs if i.content and i.content.strip()
    ]
    if memories:
        context["agentMd"] = _MEMORY_INSTRUCTION_PROMPT + "\n\n" + "\n\n".join(memories)

    context["currentDate"] = f"Today's date is {current_date}."

    body = "\n".join(f"# {k}\n{v}" for k, v in context.items())
    return (
        "<system-reminder>\n"
        "As you answer the user's questions, you can use the following context:\n"
        f"{body}\n\n"
        "      IMPORTANT: this context may or may not be relevant to your tasks. "
        "You should not respond to this context unless it is highly relevant to "
        "your task.\n"
        "</system-reminder>\n"
    )


def build_system_context_suffix(cwd: str) -> str:
    """appendSystemContext — gitStatus joined as 'gitStatus: <value>'
    and appended to the system prompt (NOT the user reminder). Empty string if
    not a git repo. builds this per call from the memoized getSystemContext."""
    git = _git_status_block(cwd)
    return f"gitStatus: {git}" if git else ""
