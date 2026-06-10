"""
Filesystem permission checks — path validation and symlink resolution.

Source: Claude Code src/utils/permissions/filesystem.ts, src/utils/fsOperations.ts

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
    Claude Code: getPathsForPermissionCheck() in fsOperations.ts

    Returns array of paths: original + all symlink chain targets + resolved.
    Permission rules are checked against EVERY path in the set.
    """
    paths = [input_path]

    try:
        # Follow symlink chain (max 40 hops)
        current = Path(input_path)
        hops = 0
        while current.is_symlink() and hops < MAX_SYMLINK_HOPS:
            target = os.readlink(current)
            # Resolve relative targets relative to symlink's directory
            if not os.path.isabs(target):
                target = str(current.parent / target)
            paths.append(target)
            current = Path(target)
            hops += 1

        # Add fully resolved path
        resolved = str(Path(input_path).resolve())
        if resolved not in paths:
            paths.append(resolved)

    except (OSError, ValueError):
        pass

    return paths


def path_in_working_directory(path: str, working_dirs: tuple[str, ...]) -> bool:
    """
    Check if a path is within allowed working directories.
    Claude Code: pathInWorkingPath() in filesystem.ts

    Uses case-insensitive comparison on macOS/Windows.
    Checks for path traversal (../ sequences).
    """
    try:
        resolved = os.path.realpath(path)
    except (OSError, ValueError):
        return False

    for wd in working_dirs:
        try:
            wd_resolved = os.path.realpath(wd)
            # Case-insensitive on macOS/Windows
            if os.name == 'nt' or os.uname().sysname == 'Darwin':
                if resolved.lower().startswith(wd_resolved.lower()):
                    # Check no path traversal
                    rel = os.path.relpath(resolved, wd_resolved)
                    if not rel.startswith('..'):
                        return True
            else:
                if resolved.startswith(wd_resolved):
                    rel = os.path.relpath(resolved, wd_resolved)
                    if not rel.startswith('..'):
                        return True
        except (OSError, ValueError):
            continue

    return False


def is_sensitive_path(path: str) -> bool:
    """
    Check if path targets sensitive system locations.
    Claude Code checks: /etc, /usr, /var, /proc, .ssh, .git
    """
    resolved = os.path.realpath(path).lower()
    sensitive = ['/etc/', '/usr/', '/var/', '/proc/', '/sys/',
                 '/.ssh/', '/.gnupg/', '/.aws/']
    return any(s in resolved for s in sensitive)
