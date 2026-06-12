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


def resolve_reviewer_adapter(
    *,
    executor_adapter: str,
    executor_model: str | None,
    executor_effort: str | None,
    reviewer_adapter: str | None,
    reviewer_model: str | None,
    reviewer_effort: str | None,
) -> AgentAdapter | None:
    if reviewer_adapter is None and reviewer_model is None and reviewer_effort is None:
        return None

    adapter_name = reviewer_adapter or executor_adapter
    inherit_executor_options = adapter_name == executor_adapter
    model = reviewer_model if reviewer_model is not None else None
    effort = reviewer_effort if reviewer_effort is not None else None
    if inherit_executor_options:
        model = executor_model if model is None else model
        effort = executor_effort if effort is None else effort
    return get_adapter(adapter_name, model=model, effort=effort)


__all__ = [
    "ClaudeCodeAdapter",
    "MockAdapter",
    "CodexAdapter",
    "get_adapter",
    "resolve_reviewer_adapter",
]
