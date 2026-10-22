"""Deterministic, seeded stand-in for an LLM agent.

It follows the workflow's plan and reproduces the behaviours that matter for procedural
errors, with configurable rates:

  p_skip     propose the step after the current one (a skipped step)
  p_dup      re-issue the last side-effecting call (a natural duplicate)
  p_fix      after a loud error, act on the error's hint (otherwise retry the same call)
  patience   consecutive errors before giving up

It believes any well-formed output (so silent failures propagate) and follows explicit
verifier feedback when it is given. These rates are modelling assumptions, not measurements
of any particular LLM.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from backend.agents.base_agent import BaseAgent
from backend.core.models import Action
from backend.core.workflow_engine import Workflow


class RuleBasedAgent(BaseAgent):
    name = "rule_based"

    def __init__(self, workflow: Workflow, task: Dict[str, Any], rng: random.Random,
                 p_skip: float = 0.04, p_dup: float = 0.02, p_fix: float = 0.5, patience: int = 4):
        self.wf, self.task, self.rng = workflow, task, rng
        self.p_skip, self.p_dup, self.p_fix, self.patience = p_skip, p_dup, p_fix, patience
        self.plan = self._expand_plan()
        self.i = 0
        self.memory: Dict[str, Dict[str, Any]] = {}
        self.pending: Optional[Action] = None
        self.last_exec: Optional[Action] = None
        self.streak = 0
        self.gave_up = False
        self.log: List[str] = []

    # ------------------------------------------------------------------ plan handling
    def _expand_plan(self) -> List[Dict[str, Any]]:
        steps = []
        for p in self.wf.spec.plan:
            if p.foreach:
                for item in self.task.get(p.foreach, []):
                    steps.append({"tool": p.tool, "args": p.args, "item": item})
            else:
                steps.append({"tool": p.tool, "args": p.args, "item": None})
        return steps

    def plan_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for s in self.plan:
            out[s["tool"]] = out.get(s["tool"], 0) + 1
        return out

    def _resolve(self, value: Any, item: Optional[Dict[str, Any]]):
        if not isinstance(value, str) or not value.startswith("$"):
            return value
        parts = value[1:].split(".")
        if parts[0] == "task":
            return self.task.get(parts[1])
        if parts[0] == "item":
            return (item or {}).get(parts[1])
        if parts[0] == "out":
            return self.memory.get(parts[1], {}).get(parts[2])
        return None

    def _materialise(self, step: Dict[str, Any]) -> Action:
        args = {k: self._resolve(v, step["item"]) for k, v in step["args"].items()}
        return Action(tool=step["tool"], args=args)

    def render(self, tool: str) -> Action:
        for j in list(range(self.i, len(self.plan))) + list(range(0, self.i)):
            if self.plan[j]["tool"] == tool:
                return self._materialise(self.plan[j])
        return Action(tool=tool, args={})

    # ------------------------------------------------------------------ proposing
    def propose(self) -> Optional[Action]:
        if self.gave_up:
            return None
        if self.pending is not None:
            a, self.pending = self.pending, None
            return a
        if self.i >= len(self.plan):
            return None
        r = self.rng.random()
        if r < self.p_skip and self.i + 1 < len(self.plan):
            self.log.append("natural skip")
            return self._materialise(self.plan[self.i + 1])
        if r < self.p_skip + self.p_dup and self.last_exec is not None:
            c = self.wf.contract(self.last_exec.tool)
            if c is not None and not c.idempotent:
                self.log.append("natural duplicate")
                return Action(tool=self.last_exec.tool, args=dict(self.last_exec.args))
        return self._materialise(self.plan[self.i])

    # ------------------------------------------------------------------ updates
    def on_output(self, action: Action, output: Dict[str, Any]) -> None:
        self.streak = 0
        self.memory[action.tool] = dict(output or {})
        self.last_exec = action
        for j in range(self.i, len(self.plan)):
            if self.plan[j]["tool"] == action.tool:
                expected = self._materialise(self.plan[j]).args
                if all(action.args.get(k) == v for k, v in expected.items() if v is not None):
                    self.i = j + 1
                    break

    def on_error(self, action: Action, message: str, hint: Optional[str]) -> None:
        self.streak += 1
        if self.streak >= self.patience:
            self.gave_up = True
            self.log.append("gave up")
            return
        if "cart is empty" in message and self.rng.random() < self.p_fix:
            self.i = next(j for j, s in enumerate(self.plan) if s["tool"] == "add_item")
            return
        if hint is None:
            hint = self._producer_of_bad_argument(action, message)
        if hint and self.rng.random() < self.p_fix:
            self.pending = self.render(hint)
            return
        self.pending = Action(tool=action.tool, args=dict(action.args))     # plain retry

    def _producer_of_bad_argument(self, action: Action, message: str) -> Optional[str]:
        """If an argument was rejected and it came from an earlier tool's output, the obvious
        fix is to call that tool again (a careful reader of the error would do the same)."""
        import re
        m = re.search(r"argument '(\w+)'", message or "")
        if not m:
            return None
        for step in self.plan:
            if step["tool"] == action.tool:
                src = step["args"].get(m.group(1))
                if isinstance(src, str) and src.startswith("$out."):
                    return src.split(".")[1]
        return None

    def on_feedback(self, action: Action, feedback: Dict[str, Any]) -> None:
        if feedback.get("committed_result") is not None:
            self.memory.setdefault(action.tool, dict(feedback["committed_result"]))
            return
        self.streak += 1
        if self.streak >= self.patience * 3:
            self.gave_up = True
            self.log.append("gave up after repeated blocks")
            return
        fix = feedback.get("fix")
        if fix:
            if fix.get("args"):
                self.pending = Action(tool=fix["tool"], args=dict(fix["args"]))
            else:
                self.pending = self.render(fix["tool"])
        elif self._producer_of_bad_argument(action, feedback.get("reason") or ""):
            self.pending = self.render(self._producer_of_bad_argument(action, feedback.get("reason") or ""))
        elif self.i < len(self.plan):
            self.pending = self._materialise(self.plan[self.i])
