"""
Iter — tui ai coding assistant.
"""

from __future__ import annotations

import sys
import click
from pathlib import Path
from dotenv import load_dotenv
from config.loader import load_config
from ui.app import IterApp
from utils.logger import configure_logging
from utils.system import get_version, set_process_title
from pprint import pprint

load_dotenv()


class DefaultCommandGroup(click.Group):
    def parse_args(self, ctx, args):
        if "--help" in args or "-h" in args:
            return super().parse_args(ctx, args)
        if "--version" in args or "-v" in args:
            return super().parse_args(ctx, args)
        # Check if first arg is a known subcommand
        if not args or args[0] not in self.commands:
            # If not a subcommand, prepend the default command name
            args.insert(0, "run")
            
        return super().parse_args(ctx, args)

    def format_usage(self, ctx, formatter):
        formatter.write_usage(ctx.command_path, "[OPTIONS] PATH OR COMMAND [ARGS]...")


@click.group(cls=DefaultCommandGroup, invoke_without_command=True)
@click.option("-v", "--version", is_flag=True, help="Show version and exit.")
@click.pass_context
def main(ctx:click.Context, version:bool):
    """Iter — AI for your terminal."""
    print(pprint(vars(ctx)), version)
    if version:
        click.echo(get_version())
        ctx.exit()
        

@main.command("run")
@click.option(
    "--cwd",
    "-c",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Project directory (defaults to current).",
)
@click.option( "-m", "--model", type=str, help="Override the model name.")
@click.option("--max-turns", type=int, help="Cap turns per session.")
@click.option(
    "--permission-mode",
    type=click.Choice(["default", "plan", "bypass", "accept_edits", "dont_ask"]),
    help="Initial permission mode.",
)
@click.option("--debug", is_flag=True, help="Enable verbose logging.")
@click.option(
    "-p",
    "--port",
    metavar="PORT",
    default=6969,
    type=int,
    help="Port to use in conjunction with --serve",
)
@click.option(
    "-H",
    "--host",
    metavar="HOST",
    default="localhost",
    type=str,
    help="Host to use in conjunction with --serve",
)
@click.option(
    "--public-url",
    metavar="URL",
    default=None,
    help="Public URL to use in conjunction with --serve",
)
@click.option("-s", "--serve", is_flag=True, help="Serve Toad as a web application")

def main(
    cwd: Path | None,
    model: str | None,
    max_turns: int | None,
    permission_mode: str | None,
    debug: bool,
    port: int,
    host: str,
    public_url: str,
    serve: bool
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
        raise click.ClickException("Config Validation Failure!!")
        
    app = IterApp(config)
    
    if serve:
        import shlex
        from textual_serve.server import Server
        
        command_args = sys.argv
        print(command_args)
        # Remove serve flag from args (could be either --serve or -s)
        for flag in ["--serve", "-s"]:
            try:
                command_args.remove(flag)
                break
            except ValueError:
                pass
            
        serve_command = shlex.join(command_args)
        server = Server(
            serve_command,
            host=host,
            port=port,
            title=serve_command,
            public_url=public_url,
        )
        set_process_title("iter --serve")
        server.serve()
    else:
        app.run()
        
    # app.run_on_exit()
    
if __name__ == "__main__":
    main()
