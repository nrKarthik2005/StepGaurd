"""Deterministic finite-state workflow engine  W = (Q, Sigma, delta, q0, F).

Specifications are loaded from YAML, validated with Pydantic, and every expression is
compiled against the allowlist before the application starts. A frozen specification is
identified by the SHA-256 of its file, which is stored with every experiment.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from backend.config import WORKFLOW_DIR
from backend.core.action_registry import ENVIRONMENTS, PREDICATES
from backend.core.expressions import ExpressionError, compile_expr, evaluate
from backend.core.models import Condition, ToolContract, WorkflowSpec


class SpecError(Exception):
    """Raised when a workflow specification is invalid."""


class Workflow:
    def __init__(self, spec: WorkflowSpec, source: str = "", sha256: str = ""):
        self.spec = spec
        self.source = source
        self.sha256 = sha256
        self.delta: Dict[Tuple[str, str], str] = {(t.source, t.action): t.target for t in spec.transitions}
        self._validate_expressions()

    # ------------------------------------------------------------------ properties
    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def initial(self) -> str:
        return self.spec.initial

    @property
    def final(self) -> set:
        return set(self.spec.final)

    def contract(self, tool: str) -> Optional[ToolContract]:
        return self.spec.tools.get(tool)

    # ------------------------------------------------------------------ automaton
    def next_state(self, q: str, action: str) -> Optional[str]:
        """Partial transition function delta(q, action); None when undefined."""
        return self.delta.get((q, action))

    def enabled(self, q: str) -> List[str]:
        return sorted(a for (s, a) in self.delta if s == q)

    def accepts(self, actions: Iterable[str]) -> bool:
        q = self.initial
        for a in actions:
            q = self.next_state(q, a)
            if q is None:
                return False
        return q in self.final

    # ------------------------------------------------------------------ abstraction
    def stage_of(self, obs: Optional[Dict[str, Any]]) -> Optional[str]:
        """alpha(s): the first matching stage rule, or None if the stage cannot be determined."""
        if obs is None:
            return None
        for rule in self.spec.stage_rules:
            try:
                if evaluate(rule.when, {"obs": obs}):
                    return rule.stage
            except ExpressionError:
                return None
        return None

    def check_conditions(self, conds: List[Condition], ctx: Dict[str, Any]):
        """Evaluate conditions in order. Returns (status, failed_condition, message, fix).
        status is True (all passed), False (one failed) or None (one could not be evaluated)."""
        for c in conds:
            try:
                if c.expr:
                    ok, msg, fix = bool(evaluate(c.expr, ctx)), c.message, None
                else:
                    ok, msg, fix = PREDICATES[c.predicate](ctx, c.params)
                    msg = msg or c.message
            except ExpressionError as e:
                return None, c, f"undefined: {e}", None
            if not ok:
                if fix is None and c.fix:
                    fix = {"tool": c.fix}
                return False, c, msg or c.name, fix
        return True, None, None, None

    def invariants_hold(self, obs) -> Tuple[Optional[bool], Optional[str]]:
        status, cond, msg, _ = self.check_conditions(self.spec.invariants, {"obs": obs})
        return status, (cond.name if cond else None)

    def completion_holds(self, obs, task) -> bool:
        status, _, _, _ = self.check_conditions(self.spec.completion, {"obs": obs, "task": task})
        return bool(status)

    # ------------------------------------------------------------------ validation
    def _validate_expressions(self) -> None:
        s = self.spec
        if s.environment not in ENVIRONMENTS:
            raise SpecError(f"{s.name}: unknown environment '{s.environment}'")
        exprs: List[str] = [r.when for r in s.stage_rules]
        conds: List[Condition] = list(s.invariants) + list(s.completion)
        for t in s.tools.values():
            conds += t.preconditions + t.effect + t.unchanged
            exprs += list(t.compensation_args.values()) + list(t.resume_output.values())
            env_cls = ENVIRONMENTS[s.environment]
            if t.name not in self._env_tool_names(env_cls):
                raise SpecError(f"{s.name}: tool '{t.name}' is not implemented by environment {s.environment}")
        for c in conds:
            if c.expr:
                exprs.append(c.expr)
            elif c.predicate not in PREDICATES:
                raise SpecError(f"{s.name}: predicate '{c.predicate}' is not registered")
        for e in exprs:
            try:
                compile_expr(e)
            except ExpressionError as err:
                raise SpecError(f"{s.name}: {err}") from None
        # every non-final state must be able to make progress
        for q in s.states:
            if q not in s.final and not self.enabled(q):
                raise SpecError(f"{s.name}: state {q} has no outgoing transition")

    @staticmethod
    def _env_tool_names(env_cls) -> set:
        return {n[1:] for n in dir(env_cls) if n.startswith("_") and not n.startswith("__")}


# ---------------------------------------------------------------------- loading
_CACHE: Dict[str, Workflow] = {}


def load_workflow_file(path: Path) -> Workflow:
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except yaml.YAMLError as e:
        raise SpecError(f"{path.name}: invalid YAML: {e}") from None
    try:
        spec = WorkflowSpec.model_validate(data)
    except Exception as e:
        raise SpecError(f"{path.name}: {e}") from None
    return Workflow(spec, source=raw.decode("utf-8"), sha256=hashlib.sha256(raw).hexdigest())


def load_all(directory: Path = WORKFLOW_DIR, refresh: bool = False) -> Dict[str, Workflow]:
    if _CACHE and not refresh:
        return dict(_CACHE)
    _CACHE.clear()
    for p in sorted(directory.glob("*.yaml")):
        wf = load_workflow_file(p)
        _CACHE[wf.name] = wf
    if not _CACHE:
        raise SpecError(f"no workflow specifications found in {directory}")
    return dict(_CACHE)


def get_workflow(name: str) -> Workflow:
    wfs = load_all()
    if name not in wfs:
        raise KeyError(f"unknown workflow '{name}'. Available: {sorted(wfs)}")
    return wfs[name]
