"""The four-valued verdicts and the fields recorded with every decision."""
from backend.core.models import Verdict
from backend.core.workflow_engine import load_all
from backend.experiments.runner import run_episode
from backend.experiments.scenarios import Scenario

REQUIRED_FIELDS = ["step", "action", "args", "stage_before", "expected_stage", "observed_stage", "verdict",
                   "failed_predicate", "message", "probe", "recovery", "admissible", "checkpoint", "barrier",
                   "in_flight", "timestamp", "verification_ms"]


def _run(wf, fault, conf="SG_FULL", template=0, seed=0, read_lag=0.0):
    return run_episode(load_all()[wf], Scenario(wf, template, seed, fault), conf, read_lag)


def test_verdict_values():
    assert {v.value for v in Verdict} >= {"ACCEPT", "BLOCK", "VIOLATE", "INCONCLUSIVE"}


def test_clean_run_accepts_every_step():
    for wf in load_all():
        res = _run(wf, None)
        assert res.completed, wf
        assert all(s.verdict == Verdict.ACCEPT for s in res.steps if s.origin != "recovery"), wf


def test_block_on_missing_prerequisite_records_fields():
    res = _run("order_processing", "E2")
    blocks = [s for s in res.steps if s.verdict == Verdict.BLOCK]
    assert blocks and blocks[0].failed_predicate == "session_valid"
    assert not blocks[0].executed
    d = blocks[0].model_dump()
    for f in REQUIRED_FIELDS:
        assert f in d
    assert d["stage_before"] is not None and d["timestamp"] > 0
    assert res.completed


def test_violate_on_silent_failure():
    seen = False
    for t in range(12):
        res = _run("order_processing", "E4", template=t)
        if res.fault_variant == "suppress":
            v = [s for s in res.steps if s.verdict == Verdict.VIOLATE]
            assert v and v[0].failed_predicate == "qty_increased"
            assert v[0].recovery.value != "NONE"
            assert res.completed
            seen = True
            break
    assert seen, "no suppress variant drawn in 12 templates"


def test_inconclusive_on_timeout_after_effect_then_resume():
    for t in range(20):
        res = _run("order_processing", "E6", template=t)
        if res.fault_variant == "after":
            s = next(s for s in res.steps if s.step == res.fault_step)
            assert s.verdict in (Verdict.INCONCLUSIVE, Verdict.VIOLATE)
            assert s.probe.value == "APPLIED" and s.recovery.value == "RESUME"
            assert res.completed and res.duplicate_side_effects == 0
            return
    raise AssertionError("no E6 'after' variant drawn")


def test_unchecked_only_without_verifier():
    res = _run("file_management", "E1", conf="A_UNGUARDED")
    assert all(s.verdict == Verdict.UNCHECKED for s in res.steps)
    res = _run("file_management", "E1", conf="SG_FULL")
    assert not any(s.verdict == Verdict.UNCHECKED for s in res.steps)
