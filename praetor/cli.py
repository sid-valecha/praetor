import typer

app = typer.Typer(help="Praetor — local-first task queue for coding agents.")


@app.callback()
def main() -> None:
    """Praetor root command. Subcommands are added in later phases."""


if __name__ == "__main__":
    app()
