# Praetor Claude Code Plugin

This plugin bundles Praetor's Claude Code skills and starts the Praetor MCP server with `praetor mcp`.

For local development, run Claude Code with:

```bash
claude --plugin-dir ./plugin
```

For installed use, publish or install the plugin through Claude Code's plugin marketplace flow. The MCP config assumes the `praetor` executable is already on `PATH` in the Claude Code environment, for example via `pipx install praetor-cli` or an activated development environment.
