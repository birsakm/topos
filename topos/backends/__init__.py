"""Agent backends (L1) — drive a coding-agent CLI on a task.

Public API. A new-provider author implements ``AgentBackend`` (returning an
``AgentRunResult`` whose ``exit_reason`` is one of the normalized ``ExitReason``
values) and declares a ``StreamDialect`` describing its stream-json event
vocabulary. Everything else under this package (``_utils``, ``_rate_limit``,
``_pricing`` …) is internal: import via this surface, not those modules.
"""

from .base import (
    AgentBackend,
    AgentRunResult,
    AuthMode,
    ExitReason,
    McpServerConfig,
)
from .claude_cli import ClaudeCLIBackend
from .codex_cli import CodexCLIBackend
from .dialect import StreamDialect
from .gemini_cli import GeminiCLIBackend

__all__ = [
    # contract / extension points
    "AgentBackend",
    "AgentRunResult",
    "ExitReason",
    "AuthMode",
    "McpServerConfig",
    "StreamDialect",
    # shipped backends
    "ClaudeCLIBackend",
    "CodexCLIBackend",
    "GeminiCLIBackend",
]
