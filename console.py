from rich.console import Console
from rich.style import Style
from rich.text import Text


console = Console()
console.print("Hello", style="bold cyan")
console.print({"a": 1, "b": [2, 3]})     # pretty-prints data structures
console.log("with timestamp + caller")    # like print but for debugging
console.rule("[bold]Section[/]")          # a horizontal divider

danger = Style(bgcolor="blue", bold=True)
console.print("YO NIGGA", style=danger)

t = Text("def ")
t.append("greet", style=danger)


console.print(t)

Text.from_markup("[red]hi[/]")         # parse markup into a Text
Text.assemble(("OK", "green"), " done") # build from (text, style) pairs