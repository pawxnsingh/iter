"""
NotebookEdit tool — edit Jupyter notebook cells.

Source: src/tools/NotebookEditTool/

Replaces the contents of a specific cell in a .ipynb file.
Supports: replace, insert, delete operations.
"""

import json
import re

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.NotebookEditTool.prompt import (
    DESCRIPTION,
    NOTEBOOK_EDIT_TOOL_PROMPT,
)
from utils.paths import resolve_path


class NotebookEditParams(BaseModel):
    """
    Cell identification: either `cell_number` (int, 0-based) per the verbatim
    prompt, OR `cell_id` (str, matches `cells[i].id`) per the actual
    schema. the prompt and schema disagree; we accept both. If both are
    supplied, `cell_number` wins (matches the prompt). If `cell_id` is given
    and the notebook has cell IDs, we resolve to the matching index.
    """
    notebook_path: str = Field(..., description="Absolute path to the .ipynb file")
    cell_number: int | None = Field(
        default=None, ge=0,
        description="Cell index (0-based). Either this or cell_id is required.",
    )
    cell_id: str | None = Field(
        default=None,
        description="Cell ID matching cells[i].id. Either this or cell_number is required.",
    )
    new_source: str = Field(default="", description="New source for the cell")
    cell_type: str = Field(default="code", description="Cell type: 'code' or 'markdown'")
    edit_mode: str = Field(
        default="replace",
        description="Edit mode: 'replace', 'insert', or 'delete'",
    )

    def resolve_index(self, cells: list[dict]) -> int | None:
        """Resolve cell_number / cell_id → index in cells. None if neither set."""
        if self.cell_number is not None:
            return self.cell_number
        if self.cell_id is not None:
            for i, cell in enumerate(cells):
                if cell.get("id") == self.cell_id:
                    return i
            m = re.fullmatch(r"cell-(\d+)", self.cell_id)
            if m:
                return int(m.group(1))
            return -1
        return None


class NotebookEditTool(Tool):
    name = "NotebookEdit"
    description = DESCRIPTION
    prompt = NOTEBOOK_EDIT_TOOL_PROMPT
    kind = ToolKind.WRITE
    schema = NotebookEditParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = NotebookEditParams(**invocation.params)
        path = resolve_path(invocation.cwd, params.notebook_path)

        if not path.exists():
            return ToolResult.error_result(f"Notebook not found: {path}")

        if not path.suffix == ".ipynb":
            return ToolResult.error_result("File must be a .ipynb notebook")

        from tools.builtin.shared.readFileState import check_freshness
        stale = check_freshness(path, None)
        if stale:
            return ToolResult.error_result(stale)

        try:
            raw = path.read_text(encoding="utf-8")
            notebook = json.loads(raw)
        except (json.JSONDecodeError, OSError) as e:
            return ToolResult.error_result(f"Failed to read notebook: {e}")

        cells = notebook.get("cells", [])

        idx = params.resolve_index(cells)
        if idx is None:
            if params.edit_mode == "insert":
                idx = 0
            else:
                return ToolResult.error_result(
                    "Either cell_number or cell_id must be provided"
                )
        elif idx == -1:
            return ToolResult.error_result(
                f"Cell ID {params.cell_id!r} not found in notebook"
            )
        elif params.edit_mode == "insert":
            idx += 1

        edit_mode = params.edit_mode
        cell_type = params.cell_type
        if edit_mode == "replace" and idx == len(cells):
            edit_mode = "insert"

        if edit_mode != "insert" and not (0 <= idx < len(cells)):
            return ToolResult.error_result(
                f"Cell {idx} does not exist (notebook has {len(cells)} cells)"
            )

        if edit_mode == "delete":
            cells.pop(idx)
            notebook["cells"] = cells
            self._write(path, notebook)
            return ToolResult.success_result(f"Deleted cell {idx} from {path.name}")

        elif edit_mode == "insert":
            new_cell: dict = {
                "cell_type": cell_type,
                "source": params.new_source,
                "metadata": {},
            }
            if cell_type == "code":
                new_cell["execution_count"] = None
                new_cell["outputs"] = []

            nbformat = notebook.get("nbformat", 4)
            nbformat_minor = notebook.get("nbformat_minor", 0)
            if nbformat > 4 or (nbformat == 4 and nbformat_minor >= 5):
                import random
                import string
                new_cell["id"] = "".join(
                    random.choices(string.ascii_lowercase + string.digits, k=13)
                )

            cells.insert(idx, new_cell)
            notebook["cells"] = cells
            self._write(path, notebook)
            return ToolResult.success_result(
                f"Inserted {cell_type} cell at position {idx} in {path.name}"
            )

        else:
            target = cells[idx]
            old_source = target.get("source", "")
            if isinstance(old_source, list):
                old_source = "".join(old_source)
            target["source"] = params.new_source
            if target.get("cell_type") == "code":
                target["execution_count"] = None
                target["outputs"] = []
            if cell_type and cell_type != target.get("cell_type"):
                target["cell_type"] = cell_type

            notebook["cells"] = cells
            self._write(path, notebook)
            return ToolResult.success_result(
                f"Replaced cell {idx} in {path.name}",
                metadata={
                    "previous_source": old_source,
                    "new_source": params.new_source,
                },
            )

    @staticmethod
    def _write(path, notebook: dict) -> None:
        """Serialize + write the notebook, then refresh readFileState so a
        follow-up edit in the same session isn't blocked by our own mtime bump
."""
        from tools.builtin.shared.readFileState import record_read
        serialized = json.dumps(notebook, indent=1)
        path.write_text(serialized, encoding="utf-8")
        record_read(path, serialized)
