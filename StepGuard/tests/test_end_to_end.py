"""End to end: a paired experiment with persistence, plus safety properties of the prototype."""
import csv
import json
import sqlite3

import httpx
import pytest

from backend import config
from backend.agents.llm_adapter import LLMAgent, parse_action
from backend.core.audit_logger import get_logger, redact
from backend.core.workflow_engine import load_all
from backend.experiments.report import generate_report
from backend.experiments.runner import ExperimentConfig, run_experiment


@pytest.fixture(scope="module")
def summary():
    cfg = ExperimentConfig(mode="quick", n_templates=2, seeds=[0], note="pytest")
    return run_experiment(cfg)


def test_artifacts_written(summary):
    d = config.RESULTS_DIR / summary["experiment_id"]
    for f in ("traces.jsonl", "episodes.csv", "summary.json", "config.json"):
        assert (d / f).exists(), f
    n = summary["n_episodes"]
    assert n == 4 * 2 * 1 * 7 * 7
    with open(d / "episodes.csv", newline="", encoding="utf-8") as fh:
        assert sum(1 for _ in csv.DictReader(fh)) == n
    with open(d / "traces.jsonl", encoding="utf-8") as fh:
        first = json.loads(fh.readline())
    assert first["experiment_id"] == summary["experiment_id"] and first["steps"]
    assert summary["spec_frozen"] is True and summary["analysis"] == "exploratory"


def test_database_persistence(summary):
    con = sqlite3.connect(config.DB_PATH)
    eid = summary["experiment_id"]
    q = lambda sql: con.execute(sql, (eid,)).fetchone()[0]  # noqa: E731
    assert q("SELECT COUNT(*) FROM episodes WHERE experiment_id=?") == summary["n_episodes"]
    assert q("SELECT COUNT(*) FROM step_events st JOIN episodes e ON e.id=st.episode_id WHERE e.experiment_id=?") > 0
    assert q("SELECT COUNT(*) FROM injected_faults f JOIN episodes e ON e.id=f.episode_id WHERE e.experiment_id=?") > 0
    assert q("SELECT COUNT(*) FROM recovery_decisions r JOIN episodes e ON e.id=r.episode_id "
             "WHERE e.experiment_id=?") > 0
    assert q("SELECT COUNT(DISTINCT configuration) FROM metric_summaries WHERE experiment_id=?") == 7
    assert q("SELECT status FROM experiments WHERE id=?") == "completed"
    assert con.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] >= 4
    con.close()


def test_stepguard_safety_properties(summary):
    m = summary["metrics"]
    sg = m["SG_FULL"]
    assert sg["TCR_clean"] == 1.0
    assert sg["duplicate_side_effects"] == 0
    # StepGuard must not complete less often than the unguarded agent on identical scenarios
    assert sg["TCR_fault"] >= m["A_UNGUARDED"]["TCR_fault"]
    by_cond = {r["condition"]: r for r in summary["per_condition"]}
    assert by_cond["E1"]["SG_FULL"] == 1.0 and by_cond["E2"]["SG_FULL"] == 1.0


def test_report_uses_measured_values(summary):
    p = generate_report(summary)
    text = p.read_text(encoding="utf-8")
    assert summary["experiment_id"] in text
    tcr = summary["metrics"]["SG_FULL"]["TCR_fault"]
    assert f"{tcr*100:.1f}%" in text
    assert "exploratory" in text


def test_spec_change_is_detected(monkeypatch, tmp_path):
    """Changing a workflow file during a run marks the run as not frozen (hence not confirmatory)."""
    import backend.experiments.runner as runner
    real = runner.load_all
    calls = {"n": 0}

    def fake(refresh=False, **kw):
        calls["n"] += 1
        wfs = dict(real())
        if refresh and calls["n"] > 1:
            wf = wfs["file_management"]
            clone = type(wf).__new__(type(wf))
            clone.__dict__.update(wf.__dict__, sha256="0" * 64)
            wfs["file_management"] = clone
        return wfs
    monkeypatch.setattr(runner, "load_all", fake)
    s = run_experiment(ExperimentConfig(mode="full", workflows=["file_management"], n_templates=1, seeds=[0],
                                        configurations=["SG_FULL"], faults=[None]), persist=False)
    assert s["spec_frozen"] is False and s["analysis"] == "exploratory"


# ---------------------------------------------------------------------- optional LLM adapter
def test_llm_parse_action():
    tools = ["authenticate", "checkout"]
    a = parse_action('Sure! {"tool": "checkout", "args": {"cart_id": "c1"}}', tools)
    assert a.tool == "checkout" and a.args == {"cart_id": "c1"} and a.origin == "llm"
    assert parse_action('{"done": true}', tools) is None
    for bad in ("no json here", '{"tool": "rm -rf"}', '{"tool": "checkout", "args": [1]}', "{not json}", "[1,2]"):
        with pytest.raises(ValueError):
            parse_action(bad, tools)


def test_llm_missing_config_and_malformed_responses(monkeypatch):
    wf = load_all()["order_processing"]
    assert LLMAgent.available() is False
    with pytest.raises(ValueError):
        from backend.experiments.runner import run_single
        run_single("order_processing", "SG_FULL", None, persist=False, agent_kind="llm")

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "I think you should checkout"}}]})
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "test")
    agent = LLMAgent(wf, {"user": "u"}, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert agent.propose() is None and agent.bad_responses == agent.max_bad

    def good(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tool": "authenticate", '
                                                                           '"args": {"user": "u"}}'}}]})
    agent = LLMAgent(wf, {"user": "u"}, client=httpx.Client(transport=httpx.MockTransport(good)))
    a = agent.propose()
    assert a.tool == "authenticate"            # a proposal only: the adapter never executes tools

    def down(request):
        raise httpx.ConnectError("refused")
    agent = LLMAgent(wf, {"user": "u"}, client=httpx.Client(transport=httpx.MockTransport(down)))
    assert agent.propose() is None


def test_secrets_are_redacted_from_logs(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test-SECRET-123")
    r = redact({"api_key": "sk-test-SECRET-123", "Authorization": "Bearer x", "nested": {"password": "p"}, "ok": 1})
    assert "SECRET" not in json.dumps(r) and r["ok"] == 1
    get_logger("test").info("calling model")
    log_text = config.LOG_PATH.read_text(encoding="utf-8") if config.LOG_PATH.exists() else ""
    assert "sk-test-SECRET-123" not in log_text
