from pathlib import Path


def resolve_path(base: str | Path, path: str | Path):
    path = Path(path)

    if path.is_absolute():
        return path.resolve()

    return Path(base).resolve() / path


def ensure_parent_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def find_git_root(start_path: str | Path) -> Path | None:
    """Walk UP from start_path looking for a `.git` (dir OR file — worktrees use a
    .git file) at each level until the filesystem root. Returns the repo root, or
    None if not inside a git repo.

    getIsGit → findGitRoot walks up; a single-level
    `(cwd/.git).exists()` check wrongly reports false when launched from a
    subdirectory of a repo.
    """
    try:
        current = Path(start_path).resolve()
    except OSError:
        return None
    while True:
        if (current / ".git").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent


def is_binary(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            return b"\x00" in chunk
    except (OSError, IOError):
        return False


def display_path_rel_to_cwd(path: str, cwd: Path | None) -> str:
    try:
        p = Path(path)
    except Exception:
        return path

    if cwd:
        try:
            return str(p.relative_to(cwd))
        except ValueError:
            pass

    return str(p)
