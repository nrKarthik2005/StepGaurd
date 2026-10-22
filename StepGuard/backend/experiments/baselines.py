"""Baseline configurations A (unguarded) and B (basic runtime interceptor).

Both share the agent, environment, fault injector, oracle and cost model with StepGuard, so
the comparison is paired. Baseline B is a competent interceptor, not a straw man: it validates
argument schemas and caller roles against the same contracts StepGuard uses, and retries
timeouts and retryable errors with bounded backoff. It does not model the workflow, does not
validate outputs or effects, and keeps no checkpoints.
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, Optional

from backend import config
from backend.core.contract_engine import ContractEngine
from backend.core.models import Configuration, RunResult, StepRecord, Strategy, Verdict
from backend.core.runtime_controller import Budgets
from backend.core.tool_executor import ToolExecutor
from backend.core.workflow_engine import Workflow


def run_baseline(workflow: Workflow, env, agent, injector, oracle, configuration: Configuration,
                 task: Dict[str, Any], task_id: str, seed: int, budgets: Optional[Budgets] = None,
                 max_backoff_retries: int = 2) -> RunResult:
    cfg = Configuration(configuration)
    if cfg not in (Configuration.A, Configuration.B):
        raise ValueError("run_baseline only runs configurations A and B")
    b = budgets or Budgets()
    t_wall = time.perf_counter()
    ce = ContractEngine(workflow)
    executor = ToolExecutor(env, injector)
    res = RunResult(workflow=workflow.name, family=workflow.spec.family, configuration=cfg.value,
                    task_id=task_id, seed=seed, fault=injector.fault if injector else None)
    vt, step = 0.0, 0
    while res.executed < b.max_executions and res.proposals < b.max_proposals:
        a = injector.pop_replay(step + 1) if injector else None
        if a is None:
            a = agent.propose()
            res.proposals += 1
            vt += config.COST_AGENT_STEP
            if a is None:
                break
            step += 1
            if injector:
                a = injector.on_propose(a, env, step)
        else:
            step += 1
        unsafe, _ = oracle.unsafe(a)
        res.unsafe_proposed += unsafe
        res.correct_steps += (not unsafe)
        rec = StepRecord(step=step, action=a.tool, args=dict(a.args), origin=a.origin, verdict=Verdict.UNCHECKED,
                         oracle_unsafe=unsafe, timestamp=time.time())
        # ---------------------------------------------- B: argument schema and role check
        if cfg == Configuration.B:
            t0 = time.perf_counter()
            c = workflow.contract(a.tool)
            if c is None:
                ok, msg = False, f"unknown tool {a.tool}"
            elif a.role not in c.roles:
                ok, msg = False, f"role {a.role} not permitted"
            else:
                ok, msg = ce.validate_args(c, a.args)
            rec.verification_ms += (time.perf_counter() - t0) * 1000
            if not ok:
                res.blocks += 1
                res.unsafe_blocked += unsafe
                if not unsafe:
                    res.false_flags += 1
                rec.verdict, rec.failed_predicate, rec.message = Verdict.BLOCK, "argument_schema", msg
                rec.virtual_time = round(vt, 3)
                res.steps.append(rec)
                agent.on_error(a, f"400 invalid request: {msg}", None)
                continue
        # ---------------------------------------------- execute (B: bounded retry with backoff)
        attempts = 1 + (max_backoff_retries if cfg == Configuration.B else 0)
        for attempt in range(attempts):
            res.executed += 1
            res.unsafe_executed += oracle.unsafe(a)[0] if attempt else unsafe
            vt += config.COST_TOOL_CALL
            ex = executor.execute(a, step)
            rec.executed = True
            rec.output, rec.error = ex.output, ex.error
            if injector and injector.fired and injector.fire_step == step:
                rec.fault = injector.fault
            if ex.ok:
                agent.on_output(a, ex.output)
                break
            if cfg == Configuration.B and ex.retryable and attempt < attempts - 1:
                res.retries += 1
                rec.recovery = Strategy.RETRY
                rec.recovery_reason = "retryable exception: bounded retry with backoff"
                vt += config.COST_BACKOFF * (2 ** attempt)
                continue
            agent.on_error(a, ex.error, ex.hint)
            break
        rec.virtual_time = round(vt, 3)
        res.steps.append(rec)
    else:
        res.escalated, res.escalation_reason = False, "budget exhausted"

    res.completed = oracle.goal_met()
    res.duplicate_side_effects = oracle.duplicate_side_effects()
    res.tool_calls = executor.calls
    res.virtual_time = round(vt, 3)
    res.verification_ms = round(sum(s.verification_ms for s in res.steps), 3)
    if injector:
        res.fault_variant, res.fault_fired, res.fault_step = injector.variant, injector.fired, injector.fire_step
        if injector.fired and cfg == Configuration.B:
            lo, hi = injector.fire_step, injector.fire_step + 2
            res.detected = any(lo <= s.step <= hi and (s.verdict == Verdict.BLOCK or s.recovery == Strategy.RETRY)
                               for s in res.steps)
    res.wall_ms = round((time.perf_counter() - t_wall) * 1000, 3)
    return res
