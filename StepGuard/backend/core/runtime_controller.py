"""Runtime Execution Controller: Algorithm 1 of the paper.

One controller instance runs one episode for StepGuard or one of its ablations:

  SG_FULL                  pre + post + probe + checkpoints + selective recovery
  SG_NO_PRE                no precondition engine (no blocking, no duplicate check)
  SG_NO_POST               no postcondition engine or probes; successful calls are committed unchecked
  SG_NO_CHECKPOINT         no checkpoints, so no rollback
  SG_UNSELECTIVE_RECOVERY  uniform bounded retry; safety admissibility rules still apply

Every decision becomes a StepRecord; the controller never executes a tool except through the
executor, and never re-executes a non-idempotent action unless its effect is confirmed ABSENT.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from backend import config
from backend.core.checkpoint_manager import CheckpointManager
from backend.core.contract_engine import ContractEngine, action_key
from backend.core.models import (Action, Configuration, ProbeResult, Reversibility, RunResult, StepRecord,
                                 Strategy, Verdict)
from backend.core.recovery_manager import RecoveryManager, classify
from backend.core.state_observer import StateObserver
from backend.core.tool_executor import ToolExecutor
from backend.core.workflow_engine import Workflow

FLAGS = {
    Configuration.SG: dict(pre=True, post=True, ckpt=True, policy="selective"),
    Configuration.SG_NO_PRE: dict(pre=False, post=True, ckpt=True, policy="selective"),
    Configuration.SG_NO_POST: dict(pre=True, post=False, ckpt=True, policy="selective"),
    Configuration.SG_NO_CHECKPOINT: dict(pre=True, post=True, ckpt=False, policy="selective"),
    Configuration.SG_UNSELECTIVE: dict(pre=True, post=True, ckpt=True, policy="unselective"),
}


@dataclass
class Budgets:
    max_executions: int = 25     # H: tool executions per episode
    max_blocks: int = 10         # B_b: blocked proposals per episode
    max_retries: int = 2         # B_r: recovery attempts per step
    max_proposals: int = 60      # hard stop on agent proposals


class RuntimeController:
    def __init__(self, workflow: Workflow, env, agent, injector, oracle, configuration: Configuration,
                 task: Dict[str, Any], task_id: str, seed: int, read_lag: float = 0.0,
                 budgets: Optional[Budgets] = None, rng: Optional[random.Random] = None):
        self.wf, self.env, self.agent, self.injector, self.oracle = workflow, env, agent, injector, oracle
        self.cfg = Configuration(configuration)
        self.flags = FLAGS[self.cfg]
        self.task, self.task_id, self.seed = task, task_id, seed
        self.budgets = budgets or Budgets()
        self.observer = StateObserver(env, rng or random.Random(seed), read_lag)
        self.executor = ToolExecutor(env, injector)
        self.ce = ContractEngine(workflow)
        self.ck = CheckpointManager(env, self.observer, workflow)
        self.rm = RecoveryManager(self.flags["policy"], self.flags["ckpt"], self.budgets.max_retries)
        self.invariant_names = {c.name for c in workflow.spec.invariants}

    # ------------------------------------------------------------------ main loop
    def run(self) -> RunResult:
        t_wall = time.perf_counter()
        wf, ce, f, b = self.wf, self.ce, self.flags, self.budgets
        res = RunResult(workflow=wf.name, family=wf.spec.family, configuration=self.cfg.value,
                        task_id=self.task_id, seed=self.seed,
                        fault=self.injector.fault if self.injector else None)
        self.res = res
        self.vt = 0.0
        self.q = wf.initial
        self.history: List[str] = []
        self.committed_keys: Set[str] = set()
        self.in_flight: Set[str] = set()
        self.last_output: Dict[str, Dict[str, Any]] = {}
        detect_vt: Optional[float] = None

        obs0 = self.observer.observe_consistent()
        self.vt += config.COST_STATE_READ
        if f["ckpt"]:
            self.ck.commit(self.q, [], [], obs0, step=0)
            self.vt += config.COST_SNAPSHOT

        step, forced, retries = 0, None, 0
        while True:
            if res.executed >= b.max_executions or res.blocks > b.max_blocks or res.proposals >= b.max_proposals:
                res.escalated, res.escalation_reason = True, "execution, block or proposal budget exhausted"
                break
            # ---------------------------------------------------- next action
            if forced is not None:
                a, forced = forced, None
                step += 1
            else:
                a = self.injector.pop_replay(step + 1) if self.injector else None
                if a is None:
                    a = self.agent.propose()
                    res.proposals += 1
                    self.vt += config.COST_AGENT_STEP
                    if a is None:
                        break
                    step += 1
                    if self.injector:
                        a = self.injector.on_propose(a, self.env, step)
                else:
                    step += 1
                retries = 0
            contract = wf.contract(a.tool)
            unsafe, why = self.oracle.unsafe(a)
            fault_here = bool(self.injector and self.injector.fired and self.injector.fire_step == step)
            res.unsafe_proposed += unsafe
            res.correct_steps += (not unsafe)
            rec = StepRecord(step=step, action=a.tool, args=dict(a.args), origin=a.origin, stage_before=self.q,
                             expected_stage=wf.next_state(self.q, a.tool), verdict=Verdict.ACCEPT,
                             oracle_unsafe=unsafe, timestamp=time.time(), barrier=self.ck.barrier,
                             fault=self.injector.fault if fault_here else None)

            # ---------------------------------------------------- precondition engine
            before = None
            if f["pre"]:
                before = self.observer.observe()
                self.vt += config.COST_STATE_READ
                pr = ce.precheck(self.q, a, before, self.task, set(self.history), self.committed_keys, self.in_flight)
                rec.verification_ms += pr.elapsed_ms
                if pr.verdict == Verdict.BLOCK:
                    res.blocks += 1
                    res.unsafe_blocked += unsafe
                    if not unsafe and not fault_here:
                        res.false_flags += 1
                    rec.verdict, rec.failed_predicate, rec.message = Verdict.BLOCK, pr.failed, pr.message
                    rec.recovery, rec.recovery_reason = Strategy.REPAIR, "pre-execution block: feedback to the agent"
                    rec.admissible = [Strategy.REPAIR.value, Strategy.ESCALATE.value]
                    feedback = {"reason": pr.message, "fix": pr.fix, "enabled": pr.enabled}
                    if pr.duplicate and a.tool in self.last_output:
                        feedback["committed_result"] = self.last_output[a.tool]
                        rec.recovery_reason = "duplicate blocked: committed result returned"
                    self._finish(rec)
                    self.agent.on_feedback(a, feedback)
                    if detect_vt is None and self._after_fault(step):
                        detect_vt = self.vt
                    continue
            if before is None:
                before = self.observer.observe_consistent()
                self.vt += config.COST_STATE_READ

            # ---------------------------------------------------- execute
            key = action_key(contract, a) if contract else None
            non_idem = contract is not None and not contract.idempotent
            if key and non_idem:
                self.in_flight.add(key)
            self.observer.before_write(before)
            res.executed += 1
            res.unsafe_executed += unsafe
            self.vt += config.COST_TOOL_CALL
            ex = self.executor.execute(a, step)
            rec.executed = True
            rec.output, rec.error = ex.output, ex.error
            if self.injector and self.injector.fired and self.injector.fire_step == step:
                rec.fault = self.injector.fault

            # ---------------------------------------------------- no postcondition engine
            if not f["post"]:
                rec.verdict = Verdict.UNCHECKED
                if ex.ok:
                    self._commit(a, ex.output, None, step, rec)
                else:
                    self.agent.on_error(a, ex.error, ex.hint)
                    rec.message = "error passed to the agent (no postcondition engine)"
                self._finish(rec)
                continue

            # ---------------------------------------------------- postcondition engine
            after = self.observer.observe()
            self.vt += config.COST_STATE_READ
            tool_error = ex.error is not None and not ex.timeout
            if ex.timeout:
                verdict, failed, msg, observed = Verdict.INCONCLUSIVE, "timeout", "call timed out; outcome unknown", None
            elif tool_error:
                verdict, failed, msg, observed = Verdict.VIOLATE, "tool_error", ex.error, None
            else:
                po = ce.postcheck(self.q, a, ex.output, before, after, self.task)
                rec.verification_ms += po.elapsed_ms
                verdict, failed, msg, observed = po.verdict, po.failed, po.message, po.observed_stage
            rec.observed_stage = observed
            if verdict == Verdict.ACCEPT:
                rec.verdict = Verdict.ACCEPT
                self._commit(a, ex.output, after, step, rec)
                if detect_vt is not None and res.recovery_latency is None:
                    res.recovery_latency = round(self.vt - detect_vt, 3)
                self._finish(rec)
                continue

            # ---------------------------------------------------- failure: probe and recover
            rec.verdict, rec.failed_predicate, rec.message = verdict, failed, msg
            if ex.ok and not unsafe and not fault_here:
                res.false_flags += 1
            if detect_vt is None and self._after_fault(step):
                detect_vt = self.vt
            t0 = time.perf_counter()
            now = self.observer.observe_consistent()
            self.vt += config.COST_STATE_READ
            probe = ce.probe(a, ex.output, before, now, self.task)
            resume_ok = probe == ProbeResult.APPLIED and \
                ce.postcheck(self.q, a, ex.output, before, now, self.task, check_schema=False).verdict == Verdict.ACCEPT
            rec.verification_ms += (time.perf_counter() - t0) * 1000
            rec.probe = probe
            feasible, why_not = (False, "checkpoints disabled")
            if f["ckpt"] and self.ck.latest is not None:
                feasible, why_not = self.ck.feasible(self.ck.latest.index, [a])
            cls = classify(verdict, failed, probe, tool_error, self.invariant_names)
            dec = self.rm.decide(contract, cls, probe, resume_ok, feasible, retries, tool_error)
            rec.recovery, rec.recovery_reason, rec.admissible = dec.strategy, dec.reason, dec.admissible
            if dec.strategy == Strategy.RESUME:
                out = self._reconstruct_output(a, ex.output, now)
                rec.observed_stage = wf.stage_of(now)
                self._commit(a, out, now, step, rec)
                if res.recovery_latency is None and detect_vt is not None:
                    res.recovery_latency = round(self.vt - detect_vt, 3)
            elif dec.strategy == Strategy.RETRY:
                if key:
                    self.in_flight.discard(key)
                retries += 1
                res.retries += 1
                forced = Action(tool=a.tool, args=dict(a.args), role=a.role, origin="recovery")
            elif dec.strategy == Strategy.ROLLBACK:
                outcome = self.ck.rollback(self.ck.latest.index, [(a, before, now)], step)
                self.vt += config.COST_TOOL_CALL * outcome.compensations + config.COST_STATE_READ
                self.observer.reads += 0
                res.tool_calls += outcome.compensations
                rec.restoration_verified = outcome.ok
                rec.checkpoint = self.ck.latest.index if self.ck.latest else None
                if not outcome.ok:
                    rec.recovery = Strategy.ESCALATE
                    rec.recovery_reason = f"rollback failed: {outcome.reason}"
                    res.escalated, res.escalation_reason = True, rec.recovery_reason
                    self._finish(rec)
                    break
                if key:
                    self.in_flight.discard(key)
                retries += 1
                res.retries += 1
                forced = Action(tool=a.tool, args=dict(a.args), role=a.role, origin="recovery")
            elif dec.strategy == Strategy.REPAIR:
                if key:
                    self.in_flight.discard(key)
                self.agent.on_error(a, ex.error, ex.hint)
            else:
                res.escalated, res.escalation_reason = True, dec.reason
                self._finish(rec)
                break
            self._finish(rec)

        # ---------------------------------------------------- wrap up
        res.final_stage = self.q
        res.completed = self.oracle.goal_met()
        res.duplicate_side_effects = self.oracle.duplicate_side_effects()
        res.checkpoints = list(self.ck.events)
        res.tool_calls += self.executor.calls
        res.state_reads = self.observer.reads
        res.virtual_time = round(self.vt, 3)
        res.verification_ms = round(sum(s.verification_ms for s in res.steps), 3)
        self._fault_summary(res)
        res.wall_ms = round((time.perf_counter() - t_wall) * 1000, 3)
        return res

    # ------------------------------------------------------------------ helpers
    def _after_fault(self, step: int) -> bool:
        inj = self.injector
        return bool(inj and inj.fired and inj.fire_step is not None and inj.fire_step <= step <= inj.fire_step + 2)

    def _commit(self, a: Action, output, obs_after, step: int, rec: StepRecord) -> None:
        contract = self.wf.contract(a.tool)
        nxt = self.wf.next_state(self.q, a.tool)
        if nxt is None:              # only reachable without the precondition engine
            nxt = self.wf.stage_of(obs_after) or self.q
        self.q = nxt
        self.history.append(a.tool)
        key = action_key(contract, a) if contract else None
        if key:
            self.committed_keys.add(key)
            self.in_flight.discard(key)
        self.last_output[a.tool] = dict(output or {})
        if self.flags["ckpt"]:
            obs = obs_after if obs_after is not None else self.observer.observe_consistent()
            irreversible = contract is not None and contract.reversibility == Reversibility.I
            k = self.ck.commit(self.q, self.history, sorted(self.committed_keys), obs, step, irreversible)
            self.vt += config.COST_SNAPSHOT
            rec.checkpoint = k.index
        rec.barrier = self.ck.barrier
        self.agent.on_output(a, output or {})

    def _reconstruct_output(self, a: Action, output, now) -> Dict[str, Any]:
        """For RESUME: fill output fields lost in transit from a read-only observation."""
        from backend.core.expressions import ExpressionError, evaluate
        contract = self.wf.contract(a.tool)
        out = dict(output or {})
        for field, expr in contract.resume_output.items():
            if field not in out:
                try:
                    out[field] = evaluate(expr, {"after": now, "args": a.args})
                except ExpressionError:
                    pass
        for field in contract.output_schema:
            if field not in out and isinstance(now, dict) and now.get(field) is not None:
                out[field] = now[field]
        return out

    def _finish(self, rec: StepRecord) -> None:
        rec.in_flight = sorted(self.in_flight)
        rec.virtual_time = round(self.vt, 3)
        self.res.steps.append(rec)

    def _fault_summary(self, res: RunResult) -> None:
        inj = self.injector
        if not inj:
            return
        res.fault_variant, res.fault_fired, res.fault_step = inj.variant, inj.fired, inj.fire_step
        if inj.fired:
            lo, hi = inj.fire_step, inj.fire_step + 2
            res.detected = any(lo <= s.step <= hi and s.verdict in (Verdict.BLOCK, Verdict.VIOLATE, Verdict.INCONCLUSIVE)
                               for s in res.steps)
