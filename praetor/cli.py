import typer

from praetor.commands import register_commands

app = typer.Typer(help="Praetor — local-first task queue for coding agents.")
register_commands(app)


@app.callback()
def main() -> None:
    pass


if __name__ == "__main__":
    app()
