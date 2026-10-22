"""Common machinery for the simulated tool environments.

An environment owns the *real* state that tools change (an in-memory service or files in a
per-episode sandbox directory). It exposes:

  call(tool, args)   execute a registered tool; raises ToolError (loud failure) or
                     ToolTimeout (the call's outcome is unknown to the caller)
  observe()          a read-only observation containing only the fields used for verification
  snapshot()/restore(handle)  restoration handle for reversible (R) state
  ledger             append-only record of every side effect that actually landed; the oracle
                     uses it to count duplicates independently of StepGuard
  truth()            unrestricted internal state, used only by the oracle

Nothing here is real: there are no payments, no network calls, and file operations happen
only inside the episode's sandbox directory.
"""
from __future__ import annotations

import copy
import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class ToolError(Exception):
    """A tool failed loudly (e.g. HTTP 4xx/5xx, missing file)."""

    def __init__(self, message: str, code: int = 400, hint: Optional[str] = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.hint = hint            # what a careful reader of the error message would do next
        self.retryable = retryable


class ToolTimeout(Exception):
    """The call timed out; whether its effect landed is unknown to the caller."""


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Environment:
    name = "base"
    family = "base"

    def __init__(self, task: Dict[str, Any], sandbox_root: Optional[Path] = None):
        self.task = task
        self.ledger: List[Dict[str, Any]] = []
        self.tools: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
        self.sandbox: Optional[Path] = None
        if sandbox_root is not None:
            sandbox_root.mkdir(parents=True, exist_ok=True)
            self.sandbox = Path(tempfile.mkdtemp(prefix=f"{self.family}_", dir=str(sandbox_root)))
        self.setup()

    # ---- to implement
    def setup(self) -> None:
        raise NotImplementedError

    def observe(self) -> Dict[str, Any]:
        raise NotImplementedError

    def snapshot(self) -> Any:
        raise NotImplementedError

    def restore(self, handle: Any) -> None:
        raise NotImplementedError

    def truth(self) -> Dict[str, Any]:
        raise NotImplementedError

    # ---- fault-injection hooks (used only by the fault injector)
    def remove_prerequisite(self) -> str:
        raise NotImplementedError

    def corrupt_effect(self, tool: str, args: Dict[str, Any], variant: str, before: Any) -> str:
        raise NotImplementedError

    # ---- common
    def register(self, name: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        self.tools[name] = fn

    def call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool not in self.tools:
            raise ToolError(f"404 unknown tool {tool}", code=404)
        return self.tools[tool](dict(args))

    def record_effect(self, op: str, key: Any, **detail) -> None:
        self.ledger.append({"op": op, "key": key, **detail})

    def cleanup(self) -> None:
        if self.sandbox is not None and self.sandbox.exists():
            shutil.rmtree(self.sandbox, ignore_errors=True)

    # ---- helpers for file-based environments
    def _read_tree(self, *dirs: Path) -> Dict[str, bytes]:
        out = {}
        for d in dirs:
            if d.exists():
                for p in sorted(d.rglob("*")):
                    if p.is_file():
                        out[p.relative_to(self.sandbox).as_posix()] = p.read_bytes()
        return out

    def _write_tree(self, files: Dict[str, bytes], *dirs: Path) -> None:
        for d in dirs:
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)
        for rel, data in files.items():
            p = self.sandbox / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    @staticmethod
    def deep(x):
        return copy.deepcopy(x)
