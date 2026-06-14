"""
Bash command security analysis.

Analyzes shell commands for:
  - Destructive patterns (rm -rf, git push --force)
  - Command injection (eval, exec, curl|bash)
  - Path traversal (../../etc/passwd)
  - Environment injection (LD_PRELOAD, PATH override)
  - Dangerous redirections (> /etc/*)
  - Pipeline analysis (all parts must be safe)
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

_SAFE_WRAPPERS = {"timeout", "time", "nice", "nohup", "stdbuf"}
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_OPERATOR_CHARS = set("&|;()<>")


def split_and_normalize_command(command: str) -> list[str]:
    """Split a compound command on && || | ; (respecting quotes) and strip
    leading env-var assignments + safe wrappers from each segment.

    Prevents deny-rule bypass via `FOO=1 denied`, `timeout 5 denied`, or
    `echo ok && denied` — each maps to a normalized segment a deny rule can
    match — WITHOUT false-matching an operator that sits inside a quoted string
    (e.g. `git commit -m "fix; rm"` must NOT yield an `rm` segment).
    """
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = command.split()

    segments: list[list[str]] = []
    cur: list[str] = []
    for tok in tokens:
        if tok and set(tok) <= _OPERATOR_CHARS:
            if cur:
                segments.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        segments.append(cur)

    out: list[str] = []
    for seg in segments:
        toks = list(seg)
        changed = True
        while changed and toks:
            changed = False
            while toks and _ENV_ASSIGN.match(toks[0]):
                toks = toks[1:]
                changed = True
            if toks and toks[0] in _SAFE_WRAPPERS:
                toks = toks[1:]
                changed = True
                while toks and toks[0].startswith("-"):
                    toks = toks[1:]
                if toks and re.fullmatch(r"\d+(\.\d+)?[smhd]?", toks[0]):
                    toks = toks[1:]
        if toks:
            out.append(" ".join(toks))
    return out


@dataclass
class CommandAnalysis:
    """Result of analyzing a shell command."""
    is_safe: bool = True
    is_read_only: bool = False
    is_destructive: bool = False
    needs_confirmation: bool = False
    reason: str = ""
    risk_level: str = "low"


READ_ONLY_COMMANDS = {
    "ls", "dir", "cat", "head", "tail", "less", "more", "wc",
    "sort", "uniq", "tee", "cut", "paste", "tr", "column",
    "grep", "rg", "ag", "ack", "find", "fd", "fdfind",
    "tree", "file", "stat", "du", "df", "free",
    "which", "whereis", "whence", "type", "command",
    "echo", "printf", "date", "whoami", "id", "groups",
    "pwd", "env", "printenv", "hostname", "uname",
    "diff", "cmp", "md5sum", "sha256sum", "shasum",
    "jq", "yq", "xq", "xmllint",
    "basename", "dirname", "realpath", "readlink",
}

GIT_READ_ONLY = {
    "git status", "git log", "git diff", "git show", "git blame",
    "git branch", "git remote", "git tag", "git stash list",
    "git rev-parse", "git describe", "git shortlog", "git reflog",
    "git ls-files", "git ls-tree", "git ls-remote",
    "git config --get", "git config --list",
}

DESTRUCTIVE_PATTERNS = [
    (r"rm\s+-[a-zA-Z]*[rf][a-zA-Z]*", "rm with force or recursive flags"),
    (r"rm\s+--force|rm\s+--recursive", "rm with force/recursive"),
    (r"rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+[/~]", "rm on root or home"),
    (r"git\s+push\s+.*--force", "git force push"),
    (r"git\s+push\s+-f\b", "git force push"),
    (r"git\s+reset\s+--hard", "git hard reset"),
    (r"git\s+clean\s+-[a-zA-Z]*f", "git clean with force"),
    (r"git\s+checkout\s+\.", "git checkout . (discard all)"),
    (r"git\s+restore\s+\.", "git restore . (discard all)"),
    (r"git\s+stash\s+(drop|clear)", "git stash drop/clear"),
    (r"git\s+branch\s+-D\b", "git branch -D (force delete)"),
    (r"\bdrop\s+table\b", "DROP TABLE"),
    (r"\bdrop\s+database\b", "DROP DATABASE"),
    (r"\btruncate\s+", "TRUNCATE"),
    (r"kubectl\s+delete\b", "kubectl delete"),
    (r"terraform\s+destroy\b", "terraform destroy"),
]

INJECTION_PATTERNS = [
    (r"\$\(\s*(curl|wget|eval|exec|bash|sh|zsh|python|ruby|perl|nc|ncat)", "dangerous command substitution"),
    (r"`\s*(curl|wget|eval|exec|bash|sh|zsh|python|ruby|perl|nc|ncat)", "dangerous backtick substitution"),
    (r"\beval\b", "eval command"),
    (r"\bexec\b", "exec command"),
    (r"\bbash\s+-c\b", "nested shell execution"),
    (r"\bsh\s+-c\b", "nested shell execution"),
    (r"\bzsh\s+-c\b", "nested shell execution"),
    (r"\bpython3?\s+-c\b", "python inline execution"),
    (r"\bruby\s+-e\b", "ruby inline execution"),
    (r"\bperl\s+-e\b", "perl inline execution"),
    (r"\bnode\s+-e\b", "node inline execution"),
    (r"<\(", "process substitution"),
    (r">\(", "process substitution (output)"),
    (r"<<<", "here-string injection"),
    (r"\bsource\b\s+/dev/", "source from device"),
    (r"curl\s.*\|\s*(bash|sh|zsh)", "curl pipe to shell"),
    (r"wget\s.*\|\s*(bash|sh|zsh)", "wget pipe to shell"),
    (r"\bLD_PRELOAD\s*=", "LD_PRELOAD injection"),
    (r"\bLD_LIBRARY_PATH\s*=", "LD_LIBRARY_PATH injection"),
    (r"\bDYLD_", "DYLD environment injection"),
    (r"\bPATH\s*=\s*[^$]", "PATH override (not extension)"),
    (r"\bPYTHONPATH\s*=", "PYTHONPATH injection"),
    (r"\bNODE_PATH\s*=", "NODE_PATH injection"),
    (r">\s*/etc/", "redirect to /etc"),
    (r">\s*/usr/", "redirect to /usr"),
    (r">\s*/var/", "redirect to /var"),
    (r">\s*/proc/", "redirect to /proc"),
    (r"\.\./\.\./", "path traversal"),
]

WRITE_COMMANDS = {
    "cp", "mv", "mkdir", "touch", "chmod", "chown", "chgrp",
    "ln", "install", "npm install", "pip install",
    "apt install", "brew install", "cargo install",
    "git push", "git commit", "git merge", "git rebase",
    "git checkout", "git stash", "docker run", "docker exec",
}


def analyze_command(command: str) -> CommandAnalysis:
    """
    Analyze a shell command for safety.

    Returns analysis with safety classification:
    - is_safe: can execute (may need confirmation)
    - is_read_only: pure read operation (safe for parallel)
    - is_destructive: irreversible operation (extra warning)
    - needs_confirmation: should ask user before executing
    """
    command = command.strip()
    if not command:
        return CommandAnalysis(is_safe=True, is_read_only=True)

    for pattern, desc in INJECTION_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return CommandAnalysis(
                is_safe=False,
                is_destructive=False,
                needs_confirmation=True,
                reason=f"Potential injection: {desc}",
                risk_level="high",
            )

    for pattern, desc in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return CommandAnalysis(
                is_safe=True,
                is_destructive=True,
                needs_confirmation=True,
                reason=f"Destructive: {desc}",
                risk_level="critical",
            )

    if "|" in command and "`" not in command and "$(" not in command:
        parts = [p.strip() for p in command.split("|") if p.strip()]
        analyses = [analyze_command(p) for p in parts]
        return CommandAnalysis(
            is_safe=all(a.is_safe for a in analyses),
            is_read_only=all(a.is_read_only for a in analyses),
            is_destructive=any(a.is_destructive for a in analyses),
            needs_confirmation=any(a.needs_confirmation for a in analyses),
            reason="; ".join(a.reason for a in analyses if a.reason),
            risk_level=max((a.risk_level for a in analyses), key=_risk_order),
        )

    if "&&" in command or "||" in command:
        parts = re.split(r"&&|\|\|", command)
        parts = [p.strip() for p in parts if p.strip()]
        analyses = [analyze_command(p) for p in parts]
        return CommandAnalysis(
            is_safe=all(a.is_safe for a in analyses),
            is_read_only=all(a.is_read_only for a in analyses),
            is_destructive=any(a.is_destructive for a in analyses),
            needs_confirmation=any(a.needs_confirmation for a in analyses),
            reason="; ".join(a.reason for a in analyses if a.reason),
            risk_level=max((a.risk_level for a in analyses), key=_risk_order),
        )


    try:
        tokens = shlex.split(command)
    except ValueError:
        return CommandAnalysis(
            is_safe=False,
            reason="Could not parse command",
            risk_level="medium",
        )

    if not tokens:
        return CommandAnalysis(is_safe=True, is_read_only=True)

    base_cmd = tokens[0].split("/")[-1]

    if _writes_output(command, base_cmd, tokens):
        return CommandAnalysis(
            is_safe=True,
            is_read_only=False,
            needs_confirmation=True,
            reason="Command writes output (redirect/write flag)",
            risk_level="medium",
        )

    if base_cmd in READ_ONLY_COMMANDS:
        return CommandAnalysis(is_safe=True, is_read_only=True)

    if len(tokens) >= 2:
        two_word = f"{base_cmd} {tokens[1]}"
        if two_word in GIT_READ_ONLY or two_word in READ_ONLY_COMMANDS:
            return CommandAnalysis(is_safe=True, is_read_only=True)

    if base_cmd in WRITE_COMMANDS:
        return CommandAnalysis(
            is_safe=True,
            is_read_only=False,
            needs_confirmation=True,
            reason=f"Write command: {base_cmd}",
            risk_level="medium",
        )

    if len(tokens) >= 2:
        two_word = f"{base_cmd} {tokens[1]}"
        if two_word in WRITE_COMMANDS:
            return CommandAnalysis(
                is_safe=True,
                is_read_only=False,
                needs_confirmation=True,
                reason=f"Write command: {two_word}",
                risk_level="medium",
            )

    return CommandAnalysis(
        is_safe=True,
        is_read_only=False,
        needs_confirmation=True,
        reason=f"Unknown command: {base_cmd}",
        risk_level="low",
    )


def _writes_output(command: str, base_cmd: str, tokens: list[str]) -> bool:
    """True if a single command segment writes/deletes (so it is NOT read-only).

    Catches what a base-command allowlist misses: output redirects to a file,
    and read-named commands with write/exec flags (tee, find -delete/-exec,
    sort/split -o, dd).
    """
    if re.search(r"(?:^|[^0-9&])>>?\s*[^&\s]", command):
        return True
    if base_cmd == "tee":
        return True
    if base_cmd == "find" and any(
        t in ("-delete", "-exec", "-execdir", "-fprint", "-fprintf", "-fls") for t in tokens
    ):
        return True
    if base_cmd in ("sort", "split", "csplit") and "-o" in tokens:
        return True
    if base_cmd == "dd":
        return True
    return False


def _risk_order(level: str) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(level, 0)
