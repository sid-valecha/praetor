from praetor.adapters.claude import ClaudeCodeAdapter
from praetor.adapters.codex import CodexAdapter
from praetor.adapters.mock import MockAdapter
from praetor.models import AgentAdapter

_ADAPTERS: dict[str, type[AgentAdapter]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    MockAdapter.name: MockAdapter,
    CodexAdapter.name: CodexAdapter,
}


def get_adapter(name: str) -> AgentAdapter:
    try:
        adapter_cls = _ADAPTERS[name]
    except KeyError as exc:
        valid_names = ", ".join(sorted(_ADAPTERS))
        msg = f"Unknown adapter '{name}'. Valid adapters: {valid_names}"
        raise ValueError(msg) from exc

    return adapter_cls()


__all__ = ["ClaudeCodeAdapter", "MockAdapter", "CodexAdapter", "get_adapter"]
