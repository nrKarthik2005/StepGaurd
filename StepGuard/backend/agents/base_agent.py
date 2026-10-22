"""Agent interface. An agent only *proposes* actions; it never calls tools itself."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from backend.core.models import Action


class BaseAgent(ABC):
    name = "base"

    @abstractmethod
    def propose(self) -> Optional[Action]:
        """Return the next proposed action, or None when the agent believes the task is done."""

    @abstractmethod
    def render(self, tool: str) -> Action:
        """Build the action the agent would issue for `tool` from its plan and memory."""

    @abstractmethod
    def on_output(self, action: Action, output: Dict[str, Any]) -> None:
        """A call returned (what looks like) a result."""

    @abstractmethod
    def on_error(self, action: Action, message: str, hint: Optional[str]) -> None:
        """A call failed loudly or timed out."""

    @abstractmethod
    def on_feedback(self, action: Action, feedback: Dict[str, Any]) -> None:
        """Structured feedback from a verifier (blocked action, repair suggestion, committed result)."""
