"""REST endpoints. All results come from real runs stored in SQLite / results/."""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

from backend import config
from backend.agents.llm_adapter import LLMAgent
from backend.api.schemas import (ExperimentRequest, ExperimentStarted, HealthResponse, SimulationRequest,
                                 WorkflowSummary)
from backend.core.audit_logger import get_logger, tail_log
from backend.core.workflow_engine import load_all
from backend.database.repository import Repository
from backend.experiments.report import generate_report
from backend.experiments.runner import ExperimentConfig, MODES, new_experiment_id, run_experiment, run_single

VERSION = "1.0.0"
MAX_EPISODES = 30000
router = APIRouter()
log = get_logger("api")
_RUNNING: Dict[str, threading.Thread] = {}


def repo() -> Repository:
    return Repository()


# ---------------------------------------------------------------------- health / workflows
@router.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    wfs = load_all()
    return HealthResponse(status="ok", version=VERSION, workflows=len(wfs), database=str(config.DB_PATH),
                          llm_adapter_configured=LLMAgent.available())


@router.get("/workflows", response_model=List[WorkflowSummary], tags=["workflows"])
def workflows():
    out = []
    for wf in load_all().values():
        s = wf.spec
        out.append(WorkflowSummary(
            name=s.name, version=s.version, family=s.family, description=s.description, sha256=wf.sha256,
            states=s.states, initial=s.initial, final=s.final,
            transitions=[{"from": t.source, "action": t.action, "to": t.target} for t in s.transitions],
            tools=[{"name": t.name, "idempotent": t.idempotent, "reversibility": t.reversibility.value,
                    "requires": t.requires, "key_fields": t.key_fields, "compensation": t.compensation,
                    "preconditions": [c.name for c in t.preconditions], "effect": [c.name for c in t.effect],
                    "output_schema": t.output_schema, "recovery": [r.value for r in t.recovery]}
                   for t in s.tools.values()]))
    return out


@router.get("/workflows/{name}", tags=["workflows"])
def workflow_detail(name: str):
    wfs = load_all()
    if name not in wfs:
        raise HTTPException(404, f"unknown workflow '{name}'")
    wf = wfs[name]
    return {"spec": wf.spec.model_dump(mode="json", by_alias=True), "sha256": wf.sha256, "yaml": wf.source}


# ---------------------------------------------------------------------- experiments
@router.get("/experiments", tags=["experiments"])
def list_experiments(limit: int = Query(100, ge=1, le=1000)):
    return repo().list_experiments(limit)


@router.post("/experiments/run", response_model=ExperimentStarted, status_code=202, tags=["experiments"])
def start_experiment(req: ExperimentRequest):
    wfs = load_all()
    workflows = req.workflows or list(wfs)
    unknown = [w for w in workflows if w not in wfs]
    if unknown:
        raise HTTPException(422, f"unknown workflows {unknown}")
    faults = None if req.conditions is None else [None if c == "clean" else c for c in req.conditions]
    cfg = ExperimentConfig(mode=req.mode, workflows=workflows, read_lag=req.read_lag, note=req.note,
                           n_templates=req.n_templates, seeds=req.seeds)
    if req.configurations:
        cfg.configurations = req.configurations
    if faults is not None:
        cfg.faults = faults
    cfg = cfg.resolved()
    n = len(cfg.workflows) * cfg.n_templates * len(cfg.seeds) * len(cfg.faults) * len(cfg.configurations)
    if n > MAX_EPISODES:
        raise HTTPException(422, f"{n} episodes requested; the API limit is {MAX_EPISODES}. Use scripts/run_experiments.py.")
    exp_id = new_experiment_id(cfg, {w: wfs[w].sha256 for w in cfg.workflows})
    background = req.background if req.background is not None else n > 2000
    if not background:
        summary = run_experiment(cfg, experiment_id=exp_id)
        generate_report(summary)
        return ExperimentStarted(experiment_id=exp_id, status="completed", n_episodes=summary["n_episodes"],
                                 detail="finished synchronously")

    def job():
        try:
            summary = run_experiment(cfg, experiment_id=exp_id)
            generate_report(summary)
        except Exception:
            log.exception("background experiment %s failed", exp_id)
        finally:
            _RUNNING.pop(exp_id, None)

    t = threading.Thread(target=job, name=exp_id, daemon=True)
    _RUNNING[exp_id] = t
    t.start()
    return ExperimentStarted(experiment_id=exp_id, status="running", n_episodes=n,
                             detail="running in the background; poll GET /experiments/{id}")


@router.get("/experiments/{experiment_id}", tags=["experiments"])
def get_experiment(experiment_id: str):
    e = repo().get_experiment(experiment_id)
    if e is None:
        raise HTTPException(404, f"experiment '{experiment_id}' not found")
    e["artifacts"] = {k: str(config.RESULTS_DIR / experiment_id / f) for k, f in
                      (("traces", "traces.jsonl"), ("episodes", "episodes.csv"), ("summary", "summary.json"))}
    e["running"] = experiment_id in _RUNNING
    return e


@router.get("/experiments/{experiment_id}/episodes", tags=["experiments"])
def get_episodes(experiment_id: str):
    if repo().get_experiment(experiment_id) is None:
        raise HTTPException(404, f"experiment '{experiment_id}' not found")
    return repo().episodes(experiment_id)


@router.get("/experiments/{experiment_id}/trace", tags=["experiments"])
def get_trace(experiment_id: str, episode: Optional[str] = None, configuration: Optional[str] = None,
              verdict: Optional[str] = Query(None, pattern=r"^(ACCEPT|BLOCK|VIOLATE|INCONCLUSIVE|UNCHECKED)$"),
              limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0)):
    if repo().get_experiment(experiment_id) is None:
        raise HTTPException(404, f"experiment '{experiment_id}' not found")
    return repo().trace(experiment_id, episode, configuration, verdict, limit, offset)


@router.get("/experiments/{experiment_id}/recovery", tags=["experiments"])
def get_recovery(experiment_id: str, configuration: Optional[str] = None,
                 limit: int = Query(50000, ge=1, le=200000)):
    if repo().get_experiment(experiment_id) is None:
        raise HTTPException(404, f"experiment '{experiment_id}' not found")
    return repo().recovery(experiment_id, configuration, limit)


@router.get("/experiments/{experiment_id}/export", tags=["experiments"])
def export(experiment_id: str, kind: str = Query("csv", pattern=r"^(csv|json|jsonl)$")):
    name = {"csv": "episodes.csv", "json": "summary.json", "jsonl": "traces.jsonl"}[kind]
    p = config.RESULTS_DIR / experiment_id / name
    if not p.exists():
        raise HTTPException(404, f"{name} not found for '{experiment_id}'")
    return FileResponse(p, filename=f"{experiment_id}-{name}")


@router.post("/experiments/{experiment_id}/report", tags=["experiments"])
def make_report(experiment_id: str):
    e = repo().get_experiment(experiment_id)
    if e is None or not e.get("summary"):
        raise HTTPException(404, f"no completed experiment '{experiment_id}'")
    path = generate_report(e["summary"])
    return {"report": str(path), "markdown": path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------- simulations
@router.post("/simulations/run", tags=["simulations"])
def simulate(req: SimulationRequest):
    if req.workflow not in load_all():
        raise HTTPException(404, f"unknown workflow '{req.workflow}'")
    try:
        return run_single(req.workflow, req.configuration, req.fault, req.template, req.seed, req.read_lag,
                          persist=req.persist, agent_kind=req.agent)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/simulations", tags=["simulations"])
def list_simulations(limit: int = Query(50, ge=1, le=500)):
    return repo().list_simulations(limit)


@router.get("/simulations/{sim_id}", tags=["simulations"])
def get_simulation(sim_id: str):
    s = repo().get_simulation(sim_id)
    if s is None:
        raise HTTPException(404, f"simulation '{sim_id}' not found")
    return s


# ---------------------------------------------------------------------- metrics / logs
@router.get("/metrics/summary", tags=["metrics"])
def metrics_summary(experiment_id: Optional[str] = None):
    e = repo().get_experiment(experiment_id) if experiment_id else repo().latest_completed()
    if e is None or not e.get("summary"):
        raise HTTPException(404, "no completed experiment yet; run POST /experiments/run first")
    s = e["summary"]
    return {"experiment_id": e["id"], "mode": e["mode"], "analysis": s.get("analysis"),
            "n_episodes": s.get("n_episodes"), "metrics": s["metrics"], "per_condition": s["per_condition"],
            "per_family": s["per_family"], "comparisons": s["comparisons"], "hypotheses": s.get("hypotheses", [])}


@router.get("/logs", response_class=PlainTextResponse, tags=["system"])
def logs(lines: int = Query(200, ge=1, le=5000)):
    return "".join(tail_log(lines))


@router.get("/modes", tags=["experiments"])
def modes():
    return MODES
