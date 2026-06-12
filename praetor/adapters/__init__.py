from praetor.adapters.claude import ClaudeCodeAdapter
from praetor.adapters.codex import CodexAdapter
from praetor.adapters.mock import MockAdapter
from praetor.models import AgentAdapter

_ADAPTERS: dict[str, type[AgentAdapter]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    MockAdapter.name: MockAdapter,
    CodexAdapter.name: CodexAdapter,
}


def get_adapter(
    name: str,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> AgentAdapter:
    try:
        adapter_cls = _ADAPTERS[name]
    except KeyError as exc:
        valid_names = ", ".join(sorted(_ADAPTERS))
        msg = f"Unknown adapter '{name}'. Valid adapters: {valid_names}"
        raise ValueError(msg) from exc

    if adapter_cls is not ClaudeCodeAdapter and (model is not None or effort is not None):
        msg = "--model and --effort are only supported by the claude adapter"
        raise ValueError(msg)
    if adapter_cls is ClaudeCodeAdapter:
        return ClaudeCodeAdapter(model=model, effort=effort)

    return adapter_cls()


__all__ = ["ClaudeCodeAdapter", "MockAdapter", "CodexAdapter", "get_adapter"]
