"""
Newt CLI — -style coding assistant in the terminal.

Entry point. Launches the Textual TUI (ui.app). The legacy Rich/readline
REPL (ui.tui / PieAgent) was removed during the Textual migration; this
launcher routes straight to run_tui().
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from config.loader import load_config
from ui.app import run_tui
from utils.logger import configure_logging

load_dotenv()


@click.command()
@click.option(
    "--cwd",
    "-c",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Project directory (defaults to current).",
)
@click.option("--model", "-m", type=str, help="Override the model name.")
@click.option("--max-turns", type=int, help="Cap turns per session.")
@click.option(
    "--permission-mode",
    type=click.Choice(["default", "plan", "bypass", "accept_edits", "dont_ask"]),
    help="Initial permission mode.",
)
@click.option("--debug", is_flag=True, help="Enable verbose logging.")
def main(
    cwd: Path | None,
    model: str | None,
    max_turns: int | None,
    permission_mode: str | None,
    debug: bool,
) -> None:
    config = load_config(cwd=cwd)
    if model:
        config.model_name = model
    if max_turns:
        config.max_turns = max_turns
    if permission_mode:
        config.permission_mode = permission_mode
    if debug:
        config.debug = True
    configure_logging(debug=config.debug)

    errors = config.validate()
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(run_tui(config))


if __name__ == "__main__":
    main()
