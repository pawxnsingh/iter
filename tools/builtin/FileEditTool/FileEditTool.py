from pydantic import BaseModel, Field

from tools.base import (
    FileDiff,
    Tool,
    ToolInvocation,
    ToolKind,
    ToolResult,
)
from tools.builtin.FileEditTool.prompt import EDIT_PROMPT
from utils.paths import ensure_parent_directory, resolve_path

_LEFT_SINGLE, _RIGHT_SINGLE = "‘", "’"
_LEFT_DOUBLE, _RIGHT_DOUBLE = "“", "”"

_QUOTE_FOLD = str.maketrans({
    _LEFT_SINGLE: "'", _RIGHT_SINGLE: "'",
    _LEFT_DOUBLE: '"', _RIGHT_DOUBLE: '"',
})


def _is_opening_context(chars: list[str], index: int) -> bool:
    """isOpeningContext."""
    if index == 0:
        return True
    prev = chars[index - 1]
    return prev in (" ", "\t", "\n", "\r", "(", "[", "{", "—", "–")


def _apply_curly_double(s: str) -> str:
    chars = list(s)
    out: list[str] = []
    for i, ch in enumerate(chars):
        if ch == '"':
            out.append(_LEFT_DOUBLE if _is_opening_context(chars, i) else _RIGHT_DOUBLE)
        else:
            out.append(ch)
    return "".join(out)


def _apply_curly_single(s: str) -> str:
    chars = list(s)
    out: list[str] = []
    for i, ch in enumerate(chars):
        if ch == "'":
            prev = chars[i - 1] if i > 0 else None
            nxt = chars[i + 1] if i < len(chars) - 1 else None
            if prev and nxt and prev.isalpha() and nxt.isalpha():
                out.append(_RIGHT_SINGLE)
            else:
                out.append(_LEFT_SINGLE if _is_opening_context(chars, i) else _RIGHT_SINGLE)
        else:
            out.append(ch)
    return "".join(out)


def _preserve_quote_style(old_string: str, actual_old: str, new_string: str) -> str:
    """When the old_string match came via curly-quote folding, convert
    straight quotes in new_string to the curly style the file actually uses, so
    the edit doesn't downgrade the file's typography."""
    if old_string == actual_old:
        return new_string
    has_double = _LEFT_DOUBLE in actual_old or _RIGHT_DOUBLE in actual_old
    has_single = _LEFT_SINGLE in actual_old or _RIGHT_SINGLE in actual_old
    if not has_double and not has_single:
        return new_string
    result = new_string
    if has_double:
        result = _apply_curly_double(result)
    if has_single:
        result = _apply_curly_single(result)
    return result


def _find_actual_old_string(content: str, old_string: str) -> str | None:
    """Return the substring of `content` that matches `old_string`, allowing
    straight↔curly quote differences. None if no match.

    A literal match wins. Otherwise both sides are quote-folded; the match is
    located in folded space and the ACTUAL (file-style) bytes at that position
    are returned so the replacement preserves the file's quote characters.
    """
    if old_string in content:
        return old_string
    folded_content = content.translate(_QUOTE_FOLD)
    folded_old = old_string.translate(_QUOTE_FOLD)
    idx = folded_content.find(folded_old)
    if idx == -1:
        return None
    return content[idx:idx + len(old_string)]


class EditParams(BaseModel):
    file_path: str = Field(
        ...,
        description="The absolute path to the file to modify",
    )
    old_string: str = Field(
        "",
        description="The exact text to find and replace. Must match exactly including all whitespace and indentation. For new files, leave this empty.",
    )
    new_string: str = Field(
        ...,
        description="The text to replace old_string with. Can be empty to delete text",
    )
    replace_all: bool = Field(
        False, description="Replace all occurrences of old_string (default: false)"
    )


class FileEditTool(Tool):
    name = "Edit"
    description = "Performs exact string replacements in files."
    prompt = EDIT_PROMPT

    schema = EditParams
    kind = ToolKind.WRITE

    def get_path(self, params: dict) -> str | None:
        return params.get("file_path")

    def get_permission_value(self, params: dict) -> str | None:
        """Path matched against `Edit(<pattern>)` rules."""
        return params.get("file_path")

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = EditParams(**invocation.params)
        path = resolve_path(invocation.cwd, params.file_path)

        if params.old_string == params.new_string:
            return ToolResult.error_result(
                "No changes to make: old_string and new_string are exactly the same."
            )

        if not path.exists():
            if params.old_string:
                return ToolResult.error_result(
                    f"File does not exist: {path}. To create a new file, use an empty old_string."
                )

            ensure_parent_directory(path)
            path.write_text(params.new_string, encoding="utf-8")

            line_count = len(params.new_string.splitlines())

            return ToolResult.success_result(
                f"Created {path} {line_count} lines",
                diff=FileDiff(
                    path=path,
                    old_content="",
                    new_content=params.new_string,
                    is_new_file=True,
                ),
                metadata={
                    "path": str(path),
                    "is_new_file": True,
                    "lines": line_count,
                },
            )

        if path.suffix == ".ipynb":
            return ToolResult.error_result(
                "File is a Jupyter Notebook. Use the NotebookEdit to edit this file."
            )

        uses_crlf = b"\r\n" in path.read_bytes()
        old_content = path.read_text(encoding="utf-8")

        from tools.builtin.shared.readFileState import check_freshness
        freshness_error = check_freshness(path, old_content)
        if freshness_error:
            return ToolResult.error_result(freshness_error)

        if not params.old_string:
            return ToolResult.error_result(
                "old_string is empty but file exists. Provide old_string to edit, or use write_file to overwrite."
            )

        actual_old = _find_actual_old_string(old_content, params.old_string)

        occurrence_count = old_content.count(actual_old) if actual_old else 0

        if occurrence_count == 0:
            return ToolResult.error_result(
                f"String to replace not found in file.\nString: {params.old_string}"
            )

        if occurrence_count > 1 and not params.replace_all:
            return ToolResult.error_result(
                f"Found {occurrence_count} matches of the string to replace, but "
                "replace_all is false. To replace all occurrences, set replace_all "
                "to true. To replace only one occurrence, please provide more "
                "context to uniquely identify the instance.\n"
                f"String: {params.old_string}",
                metadata={"occurence_count": occurrence_count},
            )

        new_string = _preserve_quote_style(params.old_string, actual_old, params.new_string)

        if params.replace_all:
            new_content = old_content.replace(actual_old, new_string)
            replace_count = occurrence_count
        else:
            new_content = old_content.replace(actual_old, new_string, 1)
            replace_count = 1

        try:
            to_write = new_content.replace("\n", "\r\n") if uses_crlf else new_content
            path.write_text(to_write, encoding="utf-8", newline="")
        except IOError as e:
            return ToolResult.error_result(f"failed to write file: {e}")

        from tools.builtin.shared.readFileState import record_read
        record_read(path, new_content)

        old_lines = len(old_content.splitlines())
        new_lines = len(new_content.splitlines())
        line_diff = new_lines - old_lines

        diff_msg = ""

        if line_diff > 0:
            diff_msg = f" (+{line_diff} lines)"
        elif line_diff < 0:
            diff_msg = f" ({line_diff} lines)"

        return ToolResult.success_result(
            f"Edited {path}: replaced {replace_count} occurrence(s){diff_msg}",
            diff=FileDiff(path=path, old_content=old_content, new_content=new_content),
            metadata={
                "path": str(path),
                "replaced_count": replace_count,
                "line_diff": line_diff,
            },
        )
