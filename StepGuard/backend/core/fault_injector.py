"""Deterministic fault injection (E1-E6).

The injector sits upstream of every configuration, so StepGuard, the baselines and the
ablations all face exactly the same fault at the same point of the same task. All random
choices (variant, occurrence) come from a seeded RNG and are recorded.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from backend.core.models import Action, FaultType
from backend.core.workflow_engine import Workflow
from backend.environments.base import Environment, ToolTimeout

E4_VARIANTS = [("suppress", 0.4), ("corrupt", 0.3), ("subtle", 0.3)]
E6_VARIANTS = [("before", 0.5), ("after", 0.5)]
DROP_FIELD = {"create_cart": "cart_id", "load_document": "doc_id", "create_file": "path",
              "transform_data": "output_path"}


def _choose(rng: random.Random, options):
    r, acc = rng.random(), 0.0
    for name, p in options:
        acc += p
        if r < acc:
            return name
    return options[-1][0]


class FaultInjector:
    def __init__(self, fault: Optional[str], workflow: Workflow, rng: random.Random, plan_counts: Dict[str, int],
                 agent=None):
        self.fault = fault
        self.agent = agent
        self.fired = False
        self.fire_step: Optional[int] = None
        self.note: Optional[str] = None
        self.variant: Optional[str] = None
        self.events: List[Dict[str, Any]] = []
        self._counts: Dict[str, int] = {}
        self._replay: Optional[Action] = None
        self.target = workflow.spec.fault_targets.get(fault) if fault else None
        self.occurrence = None
        if self.target is not None:
            occ = self.target.occurrence
            if occ == 0:
                occ = rng.randint(1, max(1, plan_counts.get(self.target.tool, 1)))
            self.occurrence = occ
            if fault == FaultType.E4.value:
                self.variant = _choose(rng, E4_VARIANTS)
            elif fault == FaultType.E6.value:
                self.variant = _choose(rng, E6_VARIANTS)

    # ------------------------------------------------------------------ helpers
    def _hit(self, hook: str, tool: str) -> bool:
        if self.fired or self.target is None or self.target.hook != hook or self.target.tool != tool:
            return False
        self._counts[tool] = self._counts.get(tool, 0) + 1
        return self._counts[tool] == self.occurrence

    def _fire(self, step: int, note: str) -> None:
        self.fired, self.fire_step, self.note = True, step, note
        self.events.append({"fault": self.fault, "variant": self.variant, "step": step, "note": note})

    # ------------------------------------------------------------------ proposal hook (E1, E2)
    def on_propose(self, action: Action, env: Environment, step: int) -> Action:
        if not self._hit("propose", action.tool):
            return action
        if self.fault == "E1":
            injected = self.agent.render(self.target.inject) if self.agent else Action(tool=self.target.inject)
            injected.origin = "fault_injector"
            self._fire(step, f"E1: replaced {action.tool} with out-of-order {injected.tool}")
            return injected
        if self.fault == "E2":
            note = env.remove_prerequisite()
            self._fire(step, f"E2: {note} before {action.tool}")
        return action

    # ------------------------------------------------------------------ replay hook (E5)
    def pop_replay(self, step: int) -> Optional[Action]:
        if self._replay is None:
            return None
        a, self._replay = self._replay, None
        self._fire(step, f"E5: replayed {a.tool} after a lost acknowledgement")
        return a

    # ------------------------------------------------------------------ execution hook (E3, E4, E6)
    def execute(self, env: Environment, action: Action, step: int):
        tool, args = action.tool, action.args
        if self.fault == "E5" and not self.fired and self._replay is None and self.target.tool == tool \
                and action.origin != "fault_injector":
            out = env.call(tool, args)
            self._counts[tool] = self._counts.get(tool, 0) + 1
            if self._counts[tool] == self.occurrence:
                self._replay = Action(tool=tool, args=dict(args), role=action.role, origin="fault_injector")
            return out
        if not self._hit("execute", tool):
            return env.call(tool, args)
        # Occurrences count only calls the service accepts: a call that fails on its own (bad cart id,
        # expired session, ...) must not use up the fault, otherwise whether the fault ever fires would
        # depend on the configuration that happened to let that failing call through.
        if self.fault == "E3":
            try:
                out = env.call(tool, args)
            except Exception:
                self._counts[tool] -= 1
                raise
            field = DROP_FIELD.get(tool)
            if field and field in out:
                out = {k: v for k, v in out.items() if k != field}
            self._fire(step, f"E3: {tool} output lost field '{field}' (effect applied)")
            return out
        if self.fault == "E4":
            before = env.truth()
            try:
                out = env.call(tool, args)
            except Exception:
                self._counts[tool] -= 1
                raise
            note = env.corrupt_effect(tool, args, self.variant, before)
            self._fire(step, f"E4 ({self.variant}): {note}")
            return out
        if self.fault == "E6":
            if self.variant == "after":
                try:
                    env.call(tool, args)
                except Exception:
                    self._counts[tool] -= 1
                    raise
                self._fire(step, f"E6 (after): {tool} applied, then the response timed out")
            else:
                self._fire(step, f"E6 (before): {tool} timed out before reaching the service")
            raise ToolTimeout("request timed out")
        return env.call(tool, args)
