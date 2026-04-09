"""
NotebookEdit tool — edit Jupyter notebook cells.

Source: Claude Code src/tools/NotebookEditTool/

Replaces the contents of a specific cell in a .ipynb file.
Supports: replace, insert, delete operations.
"""

import json

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from utils.paths import resolve_path


class NotebookEditParams(BaseModel):
    notebook_path: str = Field(..., description="Absolute path to the .ipynb file")
    cell_number: int = Field(..., ge=0, description="Cell index (0-based)")
    new_source: str = Field(default="", description="New source for the cell")
    cell_type: str = Field(default="code", description="Cell type: 'code' or 'markdown'")
    edit_mode: str = Field(
        default="replace",
        description="Edit mode: 'replace', 'insert', or 'delete'",
    )


class NotebookEditTool(Tool):
    name = "NotebookEdit"
    description = (
        "Edit a Jupyter notebook (.ipynb) cell. Supports replace, insert, and delete. "
        "The cell_number is 0-indexed. Use edit_mode='insert' to add a new cell at the "
        "index, 'delete' to remove, or 'replace' (default) to update content."
    )
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

        if params.edit_mode == "delete":
            if params.cell_number >= len(cells):
                return ToolResult.error_result(
                    f"Cell {params.cell_number} does not exist (notebook has {len(cells)} cells)"
                )
            cells.pop(params.cell_number)
            notebook["cells"] = cells
            path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
            return ToolResult.success_result(
                f"Deleted cell {params.cell_number} from {path.name}"
            )

        elif params.edit_mode == "insert":
            new_cell = {
                "cell_type": params.cell_type,
                "source": params.new_source.split("\n"),
                "metadata": {},
            }
            if params.cell_type == "code":
                new_cell["outputs"] = []
                new_cell["execution_count"] = None

            cells.insert(params.cell_number, new_cell)
            notebook["cells"] = cells
            path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
            return ToolResult.success_result(
                f"Inserted {params.cell_type} cell at position {params.cell_number} in {path.name}"
            )

        else:  # replace
            if params.cell_number >= len(cells):
                return ToolResult.error_result(
                    f"Cell {params.cell_number} does not exist (notebook has {len(cells)} cells)"
                )
            old_source = "".join(cells[params.cell_number].get("source", []))
            cells[params.cell_number]["source"] = params.new_source.split("\n")
            if params.cell_type:
                cells[params.cell_number]["cell_type"] = params.cell_type

            notebook["cells"] = cells
            path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
            return ToolResult.success_result(
                f"Replaced cell {params.cell_number} in {path.name}",
                metadata={
                    "previous_source": old_source,
                    "new_source": params.new_source,
                },
            )
