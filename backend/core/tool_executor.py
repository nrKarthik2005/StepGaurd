"""Executes approved tool calls against the environment (through the fault injector)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from backend.core.models import Action
from backend.environments.base import Environment, ToolError, ToolTimeout


@dataclass
class ExecResult:
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    hint: Optional[str] = None
    timeout: bool = False
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timeout


class ToolExecutor:
    def __init__(self, env: Environment, injector=None):
        self.env = env
        self.injector = injector
        self.calls = 0

    def execute(self, action: Action, step: int) -> ExecResult:
        self.calls += 1
        try:
            if self.injector is not None:
                out = self.injector.execute(self.env, action, step)
            else:
                out = self.env.call(action.tool, action.args)
            return ExecResult(output=out)
        except ToolTimeout as e:
            return ExecResult(error=f"timeout: {e}", timeout=True, retryable=True)
        except ToolError as e:
            return ExecResult(error=str(e), hint=e.hint, retryable=e.retryable)
