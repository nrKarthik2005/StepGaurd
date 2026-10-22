"""Contract engine: argument validation, precondition verification, postcondition validation,
effect probes, and the four-valued verdict.

  BLOCK         a pre-execution condition failed; the action is never executed
  VIOLATE       the action ran, but its output or the observed state breaks its contract
  INCONCLUSIVE  the outcome cannot be established (timeout, unobservable state, undefined predicate)
  ACCEPT        preconditions, output schema, effect, expected stage and invariants all hold

A tool's own success message never establishes success: effects are checked against
read-only observations of the environment.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.core.models import Action, ProbeResult, ToolContract, Verdict
from backend.core.workflow_engine import Workflow

_TYPES = {"str": (str,), "int": (int,), "float": (int, float), "bool": (bool,), "dict": (dict,), "list": (list,)}


def _type_ok(value, tname: str) -> bool:
    if tname in ("int", "float") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[tname])


def action_key(contract: Optional[ToolContract], action: Action) -> Optional[str]:
    """Logical-operation (idempotency) key: tool name plus the declared key fields."""
    if contract is None or not contract.key_fields:
        return None
    return action.tool + ":" + "|".join(str(action.args.get(f)) for f in contract.key_fields)


@dataclass
class PreResult:
    verdict: Verdict
    failed: Optional[str] = None
    message: Optional[str] = None
    fix: Optional[Dict[str, Any]] = None
    enabled: List[str] = field(default_factory=list)
    duplicate: bool = False
    elapsed_ms: float = 0.0


@dataclass
class PostResult:
    verdict: Verdict
    failed: Optional[str] = None
    message: Optional[str] = None
    expected_stage: Optional[str] = None
    observed_stage: Optional[str] = None
    elapsed_ms: float = 0.0


class ContractEngine:
    def __init__(self, workflow: Workflow):
        self.wf = workflow

    # ------------------------------------------------------------------ arguments
    def validate_args(self, contract: ToolContract, args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        for name, spec in contract.args.items():
            if name not in args or args[name] is None:
                if spec.required:
                    return False, f"missing argument '{name}'"
                continue
            v = args[name]
            if not _type_ok(v, spec.type):
                return False, f"argument '{name}' must be {spec.type}, got {type(v).__name__}"
            if spec.min is not None and isinstance(v, (int, float)) and v < spec.min:
                return False, f"argument '{name}' below minimum {spec.min}"
            if spec.max is not None and isinstance(v, (int, float)) and v > spec.max:
                return False, f"argument '{name}' above maximum {spec.max}"
            if spec.pattern and isinstance(v, str) and not re.fullmatch(spec.pattern, v):
                return False, f"argument '{name}' does not match {spec.pattern}"
        extra = set(args) - set(contract.args)
        if extra:
            return False, f"unexpected argument(s) {sorted(extra)}"
        return True, None

    def check_output_schema(self, contract: ToolContract, output: Any) -> Tuple[bool, Optional[str]]:
        if not isinstance(output, dict):
            return False, "output is not an object"
        for k, t in contract.output_schema.items():
            if k not in output:
                return False, f"output field '{k}' missing"
            if not _type_ok(output[k], t):
                return False, f"output field '{k}' should be {t}, got {type(output[k]).__name__}"
        return True, None

    # ------------------------------------------------------------------ precondition engine
    def precheck(self, q: str, action: Action, obs: Optional[Dict[str, Any]], task: Dict[str, Any],
                 done: Set[str], committed_keys: Set[str], in_flight: Set[str]) -> PreResult:
        """Evaluate Pre(s, q, h, P, a). Conjuncts are checked in a fixed order and the first
        failure is reported with a repair suggestion and the currently enabled actions."""
        t0 = time.perf_counter()
        enabled = self.wf.enabled(q)

        def out(verdict, failed=None, msg=None, fix=None, dup=False):
            return PreResult(verdict, failed, msg, fix, enabled, dup, (time.perf_counter() - t0) * 1000)

        c = self.wf.contract(action.tool)
        if c is None:
            return out(Verdict.BLOCK, "known_tool", f"unknown tool '{action.tool}'")
        if action.role not in c.roles:
            return out(Verdict.BLOCK, "permission", f"role '{action.role}' may not call {action.tool}")
        key = action_key(c, action)
        if not c.idempotent and key is not None and (key in committed_keys or key in in_flight):
            where = "already committed" if key in committed_keys else "still in flight (outcome unresolved)"
            return out(Verdict.BLOCK, "no_duplicate", f"duplicate side effect: {key} is {where}", dup=True)
        if self.wf.next_state(q, action.tool) is None:
            return out(Verdict.BLOCK, "transition_defined",
                       f"'{action.tool}' is not allowed in state {q}; enabled: {enabled}")
        missing = [d for d in c.requires if d not in done]
        if missing:
            return out(Verdict.BLOCK, "dependencies", f"missing prerequisite step {missing[0]}", {"tool": missing[0]})
        ok, msg = self.validate_args(c, action.args)
        if not ok:
            return out(Verdict.BLOCK, "argument_schema", msg)
        if obs is None:
            return out(Verdict.BLOCK, "observable", "state could not be observed before execution")
        status, cond, msg, fix = self.wf.check_conditions(
            c.preconditions, {"obs": obs, "args": action.args, "task": task})
        if status is not True:
            return out(Verdict.BLOCK, cond.name, msg, fix)
        return out(Verdict.ACCEPT)

    # ------------------------------------------------------------------ postcondition engine
    def postcheck(self, q: str, action: Action, output: Optional[Dict[str, Any]],
                  before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]], task: Dict[str, Any],
                  check_schema: bool = True) -> PostResult:
        """Evaluate Post(s, q, a, o, s') against the observed post-state."""
        t0 = time.perf_counter()
        expected = self.wf.next_state(q, action.tool)

        def out(verdict, failed=None, msg=None, observed=None):
            return PostResult(verdict, failed, msg, expected, observed, (time.perf_counter() - t0) * 1000)

        c = self.wf.contract(action.tool)
        if after is None or before is None:
            return out(Verdict.INCONCLUSIVE, "observable", "post-state could not be observed")
        observed = self.wf.stage_of(after)
        if check_schema:
            ok, msg = self.check_output_schema(c, output)
            if not ok:
                return out(Verdict.VIOLATE, "output_schema", msg, observed)
        ctx = {"before": before, "after": after, "args": action.args, "output": output or {}, "task": task}
        status, cond, msg, _ = self.wf.check_conditions(c.effect, ctx)
        if status is None:
            return out(Verdict.INCONCLUSIVE, cond.name, msg, observed)
        if status is False:
            return out(Verdict.VIOLATE, cond.name, f"effect not observed: {cond.name}", observed)
        if observed is None:
            return out(Verdict.INCONCLUSIVE, "stage_observable", "stage could not be determined", observed)
        if expected is not None and observed != expected:
            return out(Verdict.VIOLATE, "expected_stage", f"expected stage {expected}, observed {observed}", observed)
        inv, name = self.wf.invariants_hold(after)
        if inv is None:
            return out(Verdict.INCONCLUSIVE, name, "invariant could not be evaluated", observed)
        if inv is False:
            return out(Verdict.VIOLATE, name, f"invariant violated: {name}", observed)
        return out(Verdict.ACCEPT, observed=observed)

    # ------------------------------------------------------------------ effect probe
    def probe(self, action: Action, output, before, now, task) -> ProbeResult:
        """APPLIED if the intended effect is observed, ABSENT if the relevant state is unchanged,
        UNKNOWN otherwise (partial or different effect, or an undefined predicate)."""
        c = self.wf.contract(action.tool)
        if before is None or now is None:
            return ProbeResult.UNKNOWN
        ctx = {"before": before, "after": now, "args": action.args, "output": output or {}, "task": task}
        status, _, _, _ = self.wf.check_conditions(c.effect, ctx)
        if status is True:
            return ProbeResult.APPLIED
        if c.unchanged:
            status, _, _, _ = self.wf.check_conditions(c.unchanged, ctx)
            if status is True:
                return ProbeResult.ABSENT
        return ProbeResult.UNKNOWN
