"""Vision critic via the claude CLI subprocess (subscription-compatible).

Source-aware: the agent runs INSIDE the project workspace with Read+Glob,
so visual feedback can be grounded in the actual src/ files that produced
the renders. Image staging happens in ``<workspace>/_critic_images/``.

Implementation note: this class is a thin façade over the unified
``CliVisionCritic`` (in ``cli_critic.py``) parameterised with
``ClaudeCLIBackend``. The two other CLI critics (codex_cli, gemini_cli)
use the same underlying class — the only thing that differs is which
agent CLI gets driven.

Why kept as a separate class with this name:
- The rubric YAML field ``judge_backend: claude_vision`` is referenced
  by every shipped rubric and many in-flight plan.json files. Keeping
  the class + factory entry stable avoids churn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...backends.claude_cli import ClaudeCLIBackend
from .base import CriticInputs, CriticResult, Rubric
from .cli_critic import CliVisionCritic


@dataclass
class ClaudeVisionCritic:
    """Source-aware vision critic via claude CLI."""
    backend: ClaudeCLIBackend = field(default_factory=ClaudeCLIBackend.from_config)
    timeout_s: int = 300

    @classmethod
    def from_config(cls, config: dict | None = None) -> "ClaudeVisionCritic":
        return cls(
            backend=ClaudeCLIBackend.from_config(),
            timeout_s=int((config or {}).get("timeout_s", 300)),
        )

    def evaluate(self, inputs: CriticInputs, rubric: Rubric) -> CriticResult:
        impl = CliVisionCritic(
            backend=self.backend, timeout_s=self.timeout_s, label="claude",
        )
        return impl.evaluate(inputs, rubric)
