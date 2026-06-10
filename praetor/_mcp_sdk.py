try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:

    class FastMCP:  # type: ignore[no-redef]
        def __init__(self, name: str) -> None:
            self.name = name

        def tool(self):
            def decorator(func):
                return func

            return decorator

        def run(self, transport: str = "stdio") -> None:
            import threading

            threading.Event().wait()
