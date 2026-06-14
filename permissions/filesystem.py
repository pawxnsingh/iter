"""
Filesystem permission checks — path validation and symlink resolution.

Security features:
  - Symlink chain traversal (follows chain, max 40 hops)
  - Working directory containment check
  - Path traversal detection (../../etc/passwd)
  - Case-insensitive comparison (macOS/Windows)
"""

from __future__ import annotations

import os
from pathlib import Path

MAX_SYMLINK_HOPS = 40


def get_paths_for_permission_check(input_path: str) -> list[str]:
    """
    Get ALL paths to check for permissions, including symlink targets.

    Returns array of paths: original + all symlink chain targets + resolved.
    Permission rules are checked against EVERY path in the set.
    """
    paths = [input_path]

    try:
        current = Path(input_path)
        hops = 0
        while current.is_symlink() and hops < MAX_SYMLINK_HOPS:
            target = os.readlink(current)
            if not os.path.isabs(target):
                target = str(current.parent / target)
            paths.append(target)
            current = Path(target)
            hops += 1

        resolved = str(Path(input_path).resolve())
        if resolved not in paths:
            paths.append(resolved)

    except (OSError, ValueError):
        pass

    return paths


def _normalize_for_containment(p: str) -> str:
    """Match pathInWorkingPath's normalization: collapse the macOS /private
    symlinks (/private/var → /var, /private/tmp → /tmp) and lowercase (guards
    case-insensitive-filesystem bypasses)."""
    import re
    p = re.sub(r"^/private/var/", "/var/", p)
    p = re.sub(r"^/private/tmp(/|$)", r"/tmp\1", p)
    return p.lower()


def _path_in_working_path(path: str, working_path: str) -> bool:
    """Single-pair containment: lexical absolute compare with /private
    normalization, case-insensitivity, and ../ traversal rejection."""
    p = _normalize_for_containment(os.path.abspath(path))
    w = _normalize_for_containment(os.path.abspath(working_path))
    if p == w:
        return True
    try:
        rel = os.path.relpath(p, w)
    except ValueError:
        return False
    return not rel.startswith("..")


def path_in_working_directory(path: str, working_dirs: tuple[str, ...]) -> bool:
    """True if `path` is within one of the allowed working directories.

    Security shape: EVERY path in the input's symlink set (original + each chain
    hop + fully resolved) must be inside SOME form of an allowed working dir —
    so a symlink that lives inside the working dir but points OUTSIDE it is NOT
    contained (its resolved form fails). Working dirs are expanded into their own
    symlink sets so the comparison is symmetric (a resolved input still matches
    an unresolved working dir on mount-symlink systems).
    """
    try:
        paths_to_check = get_paths_for_permission_check(path)
    except (OSError, ValueError):
        return False
    if not paths_to_check:
        return False

    working_paths: list[str] = []
    for wd in working_dirs:
        try:
            working_paths.extend(get_paths_for_permission_check(wd))
        except (OSError, ValueError):
            continue
    if not working_paths:
        return False

    return all(
        any(_path_in_working_path(p, w) for w in working_paths)
        for p in paths_to_check
    )


_DANGEROUS_DIRECTORIES = (".git", ".vscode", ".idea", ".claude", ".ai-agent")
_DANGEROUS_FILES = (
    ".gitconfig", ".gitmodules", ".bashrc", ".bash_profile", ".zshrc",
    ".zprofile", ".profile", ".ripgreprc", ".mcp.json", ".claude.json",
)


def is_protected_auto_edit_path(path: str) -> bool:
    """True if `path` is a VCS/config dir or shell-rc/config file that always
    prompts, even in acceptEdits/bypass.

    Case-insensitive (prevents `.vScOdE/`-style bypass). Checks EVERY path in
    the symlink set (original + each hop + resolved), so neither a symlink named
    `.bashrc` nor a `.git/...` path that realpath rewrites away can slip through.
    """
    for raw in get_paths_for_permission_check(path):
        norm = os.path.abspath(raw).lower().replace("\\", "/")
        parts = norm.split("/")
        if any(d in parts for d in _DANGEROUS_DIRECTORIES):
            return True
        if parts and parts[-1] in _DANGEROUS_FILES:
            return True
    return False
