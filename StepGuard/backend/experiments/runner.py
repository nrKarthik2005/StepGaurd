"""Paired fault-injection experiment runner.

Every configuration runs exactly the same scenarios (workflow, template, seed, fault) with
the same derived random streams for the agent, the fault injector and the observer.
Results are written to results/<experiment_id>/ (JSONL traces, CSV, JSON summary) and to SQLite.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from backend.agents.rule_based_agent import RuleBasedAgent
from backend.config import RESULTS_DIR, SANDBOX_DIR, ensure_dirs
from backend.core.action_registry import ENVIRONMENTS
from backend.core.audit_logger import TraceWriter, get_logger
from backend.core.fault_injector import FaultInjector
from backend.core.models import Configuration, RunResult
from backend.core.runtime_controller import Budgets, RuntimeController
from backend.core.workflow_engine import Workflow, load_all
from backend.experiments.baselines import run_baseline
from backend.experiments.oracle import Oracle
from backend.experiments.scenarios import Scenario, build_scenarios, make_task, stable_seed

log = get_logger("runner")
ALL_CONFIGS = [c.value for c in Configuration]
MODES = {
    "quick": dict(n_templates=5, seeds=[0]),
    "standard": dict(n_templates=10, seeds=[0, 1]),
    "full": dict(n_templates=25, seeds=[0, 1, 2]),
}


@dataclass
class AgentSettings:
    p_skip: float = 0.04
    p_dup: float = 0.02
    p_fix: float = 0.5
    patience: int = 4


@dataclass
class ExperimentConfig:
    mode: str = "quick"
    workflows: List[str] = field(default_factory=lambda: ["document_processing", "order_processing",
                                                         "file_management", "data_pipeline"])
    configurations: List[str] = field(default_factory=lambda: list(ALL_CONFIGS))
    faults: List[Optional[str]] = field(default_factory=lambda: [None, "E1", "E2", "E3", "E4", "E5", "E6"])
    n_templates: Optional[int] = None
    seeds: Optional[List[int]] = None
    read_lag: float = 0.03
    agent: AgentSettings = field(default_factory=AgentSettings)
    budgets: Budgets = field(default_factory=Budgets)
    note: str = ""

    def resolved(self) -> "ExperimentConfig":
        m = MODES.get(self.mode, MODES["quick"])
        if self.n_templates is None:
            self.n_templates = m["n_templates"]
        if self.seeds is None:
            self.seeds = list(m["seeds"])
        if Configuration.A.value not in self.configurations:
            self.configurations = [Configuration.A.value] + list(self.configurations)  # paired reference
        return self

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


def run_episode(wf: Workflow, scenario: Scenario, configuration: str, read_lag: float = 0.0,
                agent_settings: Optional[AgentSettings] = None, budgets: Optional[Budgets] = None,
                sandbox_root: Path = SANDBOX_DIR, keep_sandbox: bool = False, agent_kind: str = "rule_based") -> RunResult:
    """Run one episode. All random streams are derived from the scenario, never from the
    configuration, so the same scenario is reproduced exactly under every configuration."""
    s = agent_settings or AgentSettings()
    task = make_task(wf.spec.family, scenario.template, scenario.seed)
    env = ENVIRONMENTS[wf.spec.environment](task, sandbox_root=sandbox_root)
    try:
        base = stable_seed(scenario.workflow, scenario.template, scenario.seed, scenario.condition)
        agent = RuleBasedAgent(wf, task, random.Random(base), s.p_skip, s.p_dup, s.p_fix, s.patience)
        plan_counts = agent.plan_counts()
        if agent_kind == "llm":
            from backend.agents.llm_adapter import LLMAgent
            if not LLMAgent.available():
                raise ValueError("LLM agent requested but STEPGUARD_LLM_BASE_URL / STEPGUARD_LLM_MODEL are not set")
            renderer = agent
            agent = LLMAgent(wf, task)
            agent.render = renderer.render            # fault injection renders actions from the reference plan
        injector = (FaultInjector(scenario.fault, wf, random.Random(base + 1), plan_counts, agent)
                    if scenario.fault else None)
        oracle = Oracle(wf, env, task)
        cfg = Configuration(configuration)
        if cfg in (Configuration.A, Configuration.B):
            res = run_baseline(wf, env, agent, injector, oracle, cfg, task, scenario.task_id, scenario.seed, budgets)
        else:
            res = RuntimeController(wf, env, agent, injector, oracle, cfg, task, scenario.task_id, scenario.seed,
                                    read_lag=read_lag, budgets=budgets, rng=random.Random(base + 2)).run()
        if injector is not None and injector.events:
            for st in res.steps:
                if st.step == injector.fire_step and st.fault is None:
                    st.fault = injector.fault
        return res
    finally:
        if not keep_sandbox:
            env.cleanup()


def new_experiment_id(cfg: ExperimentConfig, spec_hashes: Dict[str, str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(json.dumps({"cfg": cfg.to_dict(), "specs": spec_hashes}, sort_keys=True,
                                       default=str).encode()).hexdigest()[:8]
    return f"exp-{stamp}-{digest}"


def run_experiment(cfg: ExperimentConfig, progress: Optional[Callable[[int, int], None]] = None,
                   experiment_id: Optional[str] = None, persist: bool = True) -> Dict:
    """Run a paired experiment and write all artefacts. Returns the summary dictionary."""
    from backend.database.repository import Repository
    from backend.experiments.metrics import compute_summary, episode_row
    ensure_dirs()
    cfg = cfg.resolved()
    workflows = load_all()
    missing = [w for w in cfg.workflows if w not in workflows]
    if missing:
        raise ValueError(f"unknown workflows {missing}")
    # configuration freeze: spec hashes are recorded and re-checked at the end of the run
    spec_hashes = {w: workflows[w].sha256 for w in cfg.workflows}
    exp_id = experiment_id or new_experiment_id(cfg, spec_hashes)
    out_dir = RESULTS_DIR / exp_id
    out_dir.mkdir(parents=True, exist_ok=True)
    scenarios = build_scenarios(cfg.workflows, cfg.n_templates, cfg.seeds, cfg.faults)
    total = len(scenarios) * len(cfg.configurations)
    repo = Repository() if persist else None
    if repo:
        repo.save_workflows(workflows)
        repo.create_experiment(exp_id, cfg.mode, cfg.to_dict(), spec_hashes, total)
    log.info("experiment %s started: %d episodes", exp_id, total)
    try:
        return _execute(cfg, workflows, scenarios, spec_hashes, exp_id, out_dir, total, repo, progress)
    except Exception as e:
        log.exception("experiment %s failed", exp_id)
        if repo:
            repo.fail_experiment(exp_id, f"{type(e).__name__}: {e}")
        raise


def _execute(cfg, workflows, scenarios, spec_hashes, exp_id, out_dir, total, repo, progress) -> Dict:
    from backend.experiments.metrics import compute_summary, episode_row
    t0 = time.time()
    rows, results = [], []
    done = 0
    with TraceWriter(out_dir / "traces.jsonl") as tw:
        for sc in scenarios:
            wf = workflows[sc.workflow]
            for conf in cfg.configurations:
                res = run_episode(wf, sc, conf, cfg.read_lag, cfg.agent, cfg.budgets)
                ep_id = f"{sc.task_id}-{sc.condition}-{conf}"
                rec = res.model_dump(mode="json")
                rec.update(episode_id=ep_id, experiment_id=exp_id, template=sc.template, condition=sc.condition)
                tw.write(rec)
                row = episode_row(res, sc, ep_id)
                rows.append(row)
                results.append((ep_id, sc, res))
                done += 1
                if progress and (done % 25 == 0 or done == total):
                    progress(done, total)
    # verify the specification was not changed during the run
    after = {w: h.sha256 for w, h in load_all(refresh=True).items() if w in cfg.workflows}
    frozen = after == spec_hashes
    summary = compute_summary(rows, cfg.configurations)
    summary.update(experiment_id=exp_id, mode=cfg.mode, n_episodes=len(rows), spec_hashes=spec_hashes,
                   spec_frozen=frozen, duration_s=round(time.time() - t0, 2), config=cfg.to_dict(),
                   created_at=datetime.now(timezone.utc).isoformat(),
                   analysis="confirmatory" if cfg.mode == "full" and frozen else "exploratory")
    import pandas as pd
    pd.DataFrame(rows).to_csv(out_dir / "episodes.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out_dir / "config.json").write_text(json.dumps({"config": cfg.to_dict(), "spec_hashes": spec_hashes},
                                                    indent=2, default=str), encoding="utf-8")
    if repo:
        repo.save_results(exp_id, results, rows)
        repo.finish_experiment(exp_id, summary)
    log.info("experiment %s finished in %.1fs (spec frozen: %s)", exp_id, summary["duration_s"], frozen)
    return summary


def run_single(workflow: str, configuration: str, fault: Optional[str], template: int = 0, seed: int = 0,
               read_lag: float = 0.0, persist: bool = True, agent_settings: Optional[AgentSettings] = None,
               agent_kind: str = "rule_based") -> Dict:
    """One episode for the Execution Lab / POST /simulations/run."""
    from backend.database.repository import Repository
    wfs = load_all()
    if workflow not in wfs:
        raise KeyError(f"unknown workflow '{workflow}'")
    wf = wfs[workflow]
    sc = Scenario(workflow, template, seed, fault)
    res = run_episode(wf, sc, configuration, read_lag, agent_settings, agent_kind=agent_kind)
    task = make_task(wf.spec.family, template, seed)
    out = res.model_dump(mode="json")
    out.update(task=_task_preview(task), workflow_sha256=wf.sha256,
               scenario={"workflow": workflow, "template": template, "seed": seed, "fault": fault,
                         "configuration": configuration, "read_lag": read_lag, "agent": agent_kind})
    if persist:
        sim_id = Repository().save_simulation(out, wf.sha256)
        out["simulation_id"] = sim_id
    return out


def _task_preview(task: Dict) -> Dict:
    out = {}
    for k, v in task.items():
        if isinstance(v, str) and len(v) > 400:
            out[k] = v[:400] + " ..."
        else:
            out[k] = v
    return out
