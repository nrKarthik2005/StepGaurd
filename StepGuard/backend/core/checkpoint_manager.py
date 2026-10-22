"""Verified checkpoints, the irreversible-action barrier, and verified restoration.

A checkpoint k_i = <observed state, stage, committed history, restoration handle> is pushed
only after a step is ACCEPTed (or RESUMEd after a confirming probe). When an irreversible
action is committed, the barrier b moves to that checkpoint; no rollback may target a
checkpoint below b. After any rollback the environment is re-observed and the restoration
is accepted only if the stage, the invariants and the restorable state all match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.core.expressions import ExpressionError, evaluate
from backend.core.models import Action, CheckpointEvent, Reversibility
from backend.core.state_observer import StateObserver
from backend.core.workflow_engine import Workflow
from backend.environments.base import Environment, ToolError


@dataclass
class Checkpoint:
    index: int
    stage: str
    history: List[str]
    keys: List[str]
    obs: Dict[str, Any]
    handle: Any
    step: int


@dataclass
class RestoreOutcome:
    ok: bool
    reason: str
    compensations: int = 0
    events: List[CheckpointEvent] = field(default_factory=list)


class CheckpointManager:
    def __init__(self, env: Environment, observer: StateObserver, workflow: Workflow):
        self.env, self.observer, self.wf = env, observer, workflow
        self.stack: List[Checkpoint] = []
        self.barrier = 0
        self.events: List[CheckpointEvent] = []
        self.snapshots = 0

    # ------------------------------------------------------------------ commit
    def commit(self, stage: str, history: List[str], keys: List[str], obs: Dict[str, Any], step: int,
               irreversible: bool = False) -> Checkpoint:
        k = Checkpoint(len(self.stack), stage, list(history), list(keys), obs, self.env.snapshot(), step)
        self.snapshots += 1
        self.stack.append(k)
        self.events.append(CheckpointEvent(step=step, event="COMMIT", index=k.index, stage=stage,
                                           history_len=len(history), barrier=self.barrier))
        if irreversible:
            self.barrier = k.index
            self.events.append(CheckpointEvent(step=step, event="BARRIER", index=k.index, stage=stage,
                                               history_len=len(history), barrier=self.barrier))
        return k

    @property
    def latest(self) -> Optional[Checkpoint]:
        return self.stack[-1] if self.stack else None

    # ------------------------------------------------------------------ feasibility
    def feasible(self, target: int, actions_since: List[Action]) -> Tuple[bool, str]:
        """Feas(j): j >= b and every action executed since k_j is reversible or compensable."""
        if not self.stack or target < 0 or target >= len(self.stack):
            return False, "no checkpoint to restore"
        if target < self.barrier:
            return False, f"checkpoint k{target} is behind the irreversible barrier b=k{self.barrier}"
        for a in actions_since:
            c = self.wf.contract(a.tool)
            if c is None or c.reversibility == Reversibility.I:
                return False, f"{a.tool} is irreversible"
            if c.reversibility == Reversibility.C and not c.compensation:
                return False, f"{a.tool} has no compensation"
        return True, "feasible"

    # ------------------------------------------------------------------ rollback
    def rollback(self, target: int, undo: List[Tuple[Action, Dict[str, Any], Dict[str, Any]]], step: int) -> RestoreOutcome:
        """Undo `undo` = [(action, before_obs, after_obs)] in reverse order, then restore and verify.

        Compensable actions are undone by their compensating action, sized from the effect that
        was actually observed (not the effect that was requested). Reversible state is restored
        from the checkpoint's handle."""
        k = self.stack[target]
        n_comp = 0
        for action, before, after in reversed(undo):
            c = self.wf.contract(action.tool)
            if c.reversibility == Reversibility.C:
                comp = self.wf.contract(c.compensation)
                ctx = {"args": action.args, "before": before, "after": after}
                try:
                    cargs = {name: evaluate(expr, ctx) for name, expr in c.compensation_args.items()}
                except ExpressionError as e:
                    return self._fail(k, step, f"compensation arguments undefined: {e}")
                numeric = [v for v in cargs.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
                if numeric and all(v <= 0 for v in numeric):
                    continue                      # nothing landed, nothing to compensate
                try:
                    self.env.call(comp.name, cargs)
                    n_comp += 1
                except ToolError as e:
                    return self._fail(k, step, f"compensation {comp.name} failed: {e}")
        # reversible state comes back from the snapshot handle (compensable-only state is untouched)
        if any(self.wf.contract(a.tool).reversibility == Reversibility.R for a, _, _ in undo) or not undo:
            self.env.restore(k.handle)
        ok, reason = self.verify_restoration(k)
        ev = CheckpointEvent(step=step, event="RESTORE" if ok else "RESTORE_FAILED", index=k.index, stage=k.stage,
                             history_len=len(k.history), barrier=self.barrier, verified=ok)
        self.events.append(ev)
        if ok:
            del self.stack[target + 1:]
        return RestoreOutcome(ok, reason, n_comp, [ev])

    def verify_restoration(self, k: Checkpoint) -> Tuple[bool, str]:
        obs = self.observer.observe_consistent()
        if obs is None:
            return False, "state unobservable after restoration"
        stage = self.wf.stage_of(obs)
        if stage != k.stage:
            return False, f"restored stage {stage} != checkpoint stage {k.stage}"
        inv, name = self.wf.invariants_hold(obs)
        if inv is not True:
            return False, f"invariant {name} does not hold after restoration"
        if self.env.snapshot() != k.handle:
            return False, "restorable state differs from the checkpoint"
        return True, "restoration verified"

    def _fail(self, k: Checkpoint, step: int, reason: str) -> RestoreOutcome:
        ev = CheckpointEvent(step=step, event="RESTORE_FAILED", index=k.index, stage=k.stage,
                             history_len=len(k.history), barrier=self.barrier, verified=False)
        self.events.append(ev)
        return RestoreOutcome(False, reason, 0, [ev])
