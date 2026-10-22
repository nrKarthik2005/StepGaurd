"""State-aware recovery: admissibility rules and strategy selection.

Admissibility (mandatory safety rules, kept in every StepGuard configuration):
  RETRY     only if the tool is idempotent or a reliable probe reports ABSENT
  RESUME    only if the probe reports APPLIED and the observed post-state satisfies the
            effect, stage and invariant conditions (Post without the output-schema check)
  ROLLBACK  only if checkpoints are enabled, the target is not behind the barrier, and every
            action since the checkpoint is reversible or compensable
  REPAIR    for pre-execution blocks, and for loud tool errors whose effect is confirmed ABSENT
  ESCALATE  always admissible

Selection ("selective" policy, Table II of the paper) depends on the failure class; the
"unselective" ablation always tries RETRY first and otherwise escalates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from backend.core.models import ProbeResult, Strategy, ToolContract, Verdict

PREFERENCE = {
    # failure class           ordered preference among admissible strategies
    "inconclusive":    [Strategy.RESUME, Strategy.RETRY, Strategy.ROLLBACK],
    "output_schema":   [Strategy.RESUME, Strategy.RETRY, Strategy.ROLLBACK],
    # RESUME comes first wherever it is admissible: it requires a strongly consistent read
    # confirming the full effect, stage and invariants, so the earlier failure was a stale read
    "effect":          [Strategy.RESUME, Strategy.ROLLBACK, Strategy.RETRY],
    "effect_absent":   [Strategy.RETRY, Strategy.ROLLBACK],
    "invariant":       [Strategy.ROLLBACK],
    "tool_error":      [Strategy.REPAIR, Strategy.RETRY],
}


@dataclass
class Decision:
    strategy: Strategy
    reason: str
    admissible: List[str] = field(default_factory=list)


def classify(verdict: Verdict, failed: Optional[str], probe: ProbeResult, tool_error: bool,
             invariant_names=()) -> str:
    """Failure class used to pick a recovery strategy."""
    if tool_error:
        return "tool_error"
    if verdict == Verdict.INCONCLUSIVE:
        return "inconclusive"
    if failed == "output_schema":
        return "output_schema"
    if failed in invariant_names:
        return "invariant"
    return "effect_absent" if probe == ProbeResult.ABSENT else "effect"


class RecoveryManager:
    def __init__(self, policy: str = "selective", checkpoints_enabled: bool = True, max_retries: int = 2):
        if policy not in ("selective", "unselective"):
            raise ValueError("policy must be 'selective' or 'unselective'")
        self.policy = policy
        self.checkpoints_enabled = checkpoints_enabled
        self.max_retries = max_retries

    def admissible(self, contract: ToolContract, probe: ProbeResult, resume_ok: bool, rollback_feasible: bool,
                   retries: int, tool_error: bool) -> List[Strategy]:
        allowed = set(contract.recovery) | {Strategy.ESCALATE, Strategy.REPAIR}
        out = []
        if resume_ok and probe == ProbeResult.APPLIED and Strategy.RESUME in allowed:
            out.append(Strategy.RESUME)
        if (contract.idempotent or probe == ProbeResult.ABSENT) and retries < self.max_retries \
                and Strategy.RETRY in allowed:
            out.append(Strategy.RETRY)
        if self.checkpoints_enabled and rollback_feasible and retries < self.max_retries \
                and Strategy.ROLLBACK in allowed:
            out.append(Strategy.ROLLBACK)
        if tool_error and probe == ProbeResult.ABSENT:
            out.append(Strategy.REPAIR)
        out.append(Strategy.ESCALATE)
        return out

    def decide(self, contract: ToolContract, cls: str, probe: ProbeResult, resume_ok: bool,
               rollback_feasible: bool, retries: int, tool_error: bool) -> Decision:
        adm = self.admissible(contract, probe, resume_ok, rollback_feasible, retries, tool_error)
        names = [s.value for s in adm]
        if self.policy == "unselective":
            order = [Strategy.RETRY, Strategy.REPAIR] if tool_error else [Strategy.RETRY]
        else:
            order = PREFERENCE[cls]
        for s in order:
            if s in adm:
                return Decision(s, f"{cls}: {s.value} is the first admissible strategy", names)
        why = []
        if probe != ProbeResult.ABSENT and not contract.idempotent:
            why.append("retry unsafe: non-idempotent and effect not confirmed absent")
        if not rollback_feasible:
            why.append("rollback infeasible")
        if retries >= self.max_retries:
            why.append("retry budget exhausted")
        return Decision(Strategy.ESCALATE, f"{cls}: " + ("; ".join(why) or "no safe strategy"), names)
