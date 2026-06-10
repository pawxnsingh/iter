"""
NotebookEdit tool — edit Jupyter notebook cells.

Source: Claude Code src/tools/NotebookEditTool/

Replaces the contents of a specific cell in a .ipynb file.
Supports: replace, insert, delete operations.
"""

import json

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
    CC prompt, OR `cell_id` (str, matches `cells[i].id`) per CC's actual
    schema. CC's prompt and schema disagree; we accept both. If both are
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
            return -1  # sentinel: cell_id given but not found
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

        try:
            notebook = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return ToolResult.error_result(f"Failed to read notebook: {e}")

        cells = notebook.get("cells", [])

        # Resolve cell identifier (accepts CC's cell_id or our cell_number)
        idx = params.resolve_index(cells)
        if idx is None:
            return ToolResult.error_result(
                "Either cell_number or cell_id must be provided"
            )
        if idx == -1:
            return ToolResult.error_result(
                f"Cell ID {params.cell_id!r} not found in notebook"
            )
        if idx >= len(cells) and params.edit_mode != "insert":
            return ToolResult.error_result(
                f"Cell {idx} does not exist (notebook has {len(cells)} cells)"
            )

        if params.edit_mode == "delete":
            cells.pop(idx)
            notebook["cells"] = cells
            path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
            return ToolResult.success_result(f"Deleted cell {idx} from {path.name}")

        elif params.edit_mode == "insert":
            new_cell = {
                "cell_type": params.cell_type,
                "source": params.new_source.split("\n"),
                "metadata": {},
            }
            if params.cell_type == "code":
                new_cell["outputs"] = []
                new_cell["execution_count"] = None

            cells.insert(idx, new_cell)
            notebook["cells"] = cells
            path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
            return ToolResult.success_result(
                f"Inserted {params.cell_type} cell at position {idx} in {path.name}"
            )

        else:  # replace
            old_source = "".join(cells[idx].get("source", []))
            cells[idx]["source"] = params.new_source.split("\n")
            if params.cell_type:
                cells[idx]["cell_type"] = params.cell_type

            notebook["cells"] = cells
            path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
            return ToolResult.success_result(
                f"Replaced cell {idx} in {path.name}",
                metadata={
                    "previous_source": old_source,
                    "new_source": params.new_source,
                },
            )
