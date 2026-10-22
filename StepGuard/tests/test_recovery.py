"""Recovery admissibility and selection, and recovery behaviour in full runs."""
import pytest

from backend.core.models import ProbeResult, Strategy, Verdict
from backend.core.recovery_manager import RecoveryManager, classify
from backend.core.workflow_engine import load_all
from backend.experiments.runner import run_episode
from backend.experiments.scenarios import Scenario


@pytest.fixture
def c(order_wf):
    return {t: order_wf.contract(t) for t in ("authenticate", "add_item", "checkout")}


def test_retry_needs_idempotence_or_absent_effect(c):
    rm = RecoveryManager()
    adm = rm.admissible(c["checkout"], ProbeResult.UNKNOWN, False, False, 0, False)
    assert Strategy.RETRY not in adm and adm == [Strategy.ESCALATE]
    assert Strategy.RETRY in rm.admissible(c["checkout"], ProbeResult.ABSENT, False, False, 0, False)
    assert Strategy.RETRY in rm.admissible(c["authenticate"], ProbeResult.UNKNOWN, False, False, 0, False)


def test_resume_needs_applied_probe_and_post(c):
    rm = RecoveryManager()
    assert Strategy.RESUME in rm.admissible(c["checkout"], ProbeResult.APPLIED, True, False, 0, False)
    assert Strategy.RESUME not in rm.admissible(c["checkout"], ProbeResult.APPLIED, False, False, 0, False)
    assert Strategy.RESUME not in rm.admissible(c["checkout"], ProbeResult.UNKNOWN, True, False, 0, False)


def test_rollback_needs_checkpoints_and_feasibility(c):
    assert Strategy.ROLLBACK in RecoveryManager().admissible(c["add_item"], ProbeResult.UNKNOWN, False, True, 0, False)
    assert Strategy.ROLLBACK not in RecoveryManager().admissible(c["add_item"], ProbeResult.UNKNOWN, False, False, 0,
                                                                 False)
    assert Strategy.ROLLBACK not in RecoveryManager(checkpoints_enabled=False).admissible(
        c["add_item"], ProbeResult.UNKNOWN, False, True, 0, False)


def test_escalate_always_admissible_and_budget(c):
    rm = RecoveryManager(max_retries=2)
    d = rm.decide(c["authenticate"], "effect_absent", ProbeResult.ABSENT, False, False, retries=2, tool_error=False)
    assert d.strategy == Strategy.ESCALATE and "budget" in d.reason


def test_selective_policy_by_failure_class(c):
    rm = RecoveryManager()
    assert rm.decide(c["checkout"], "inconclusive", ProbeResult.APPLIED, True, False, 0, False).strategy == Strategy.RESUME
    assert rm.decide(c["add_item"], "effect", ProbeResult.UNKNOWN, False, True, 0, False).strategy == Strategy.ROLLBACK
    assert rm.decide(c["add_item"], "effect_absent", ProbeResult.ABSENT, False, True, 0, False).strategy == Strategy.RETRY
    assert rm.decide(c["add_item"], "tool_error", ProbeResult.ABSENT, False, False, 0, True).strategy == Strategy.REPAIR
    assert rm.decide(c["checkout"], "effect", ProbeResult.UNKNOWN, False, False, 0, False).strategy == Strategy.ESCALATE


def test_unselective_policy_only_retries(c):
    rm = RecoveryManager(policy="unselective")
    assert rm.decide(c["add_item"], "effect", ProbeResult.UNKNOWN, False, True, 0, False).strategy == Strategy.ESCALATE
    assert rm.decide(c["authenticate"], "effect", ProbeResult.UNKNOWN, False, True, 0, False).strategy == Strategy.RETRY
    with pytest.raises(ValueError):
        RecoveryManager(policy="random")


def test_classify():
    assert classify(Verdict.VIOLATE, "x", ProbeResult.UNKNOWN, True) == "tool_error"
    assert classify(Verdict.INCONCLUSIVE, "x", ProbeResult.UNKNOWN, False) == "inconclusive"
    assert classify(Verdict.VIOLATE, "output_schema", ProbeResult.APPLIED, False) == "output_schema"
    assert classify(Verdict.VIOLATE, "single_charge", ProbeResult.APPLIED, False, ["single_charge"]) == "invariant"
    assert classify(Verdict.VIOLATE, "qty", ProbeResult.ABSENT, False) == "effect_absent"


def _find(wf, fault, variant, conf="SG_FULL"):
    for t in range(25):
        res = run_episode(load_all()[wf], Scenario(wf, t, 0, fault), conf)
        if res.fault_variant == variant:
            return res
    raise AssertionError(f"variant {variant} not drawn")


def test_timeout_before_effect_is_retried_once():
    res = _find("order_processing", "E6", "before")
    s = next(s for s in res.steps if s.step == res.fault_step)
    assert s.probe == ProbeResult.ABSENT and s.recovery == Strategy.RETRY
    assert res.completed and res.duplicate_side_effects == 0


def test_baseline_b_duplicates_charge_on_timeout_after_effect():
    """Honest baseline: B retries timeouts blindly, as generic retry middleware does."""
    res = _find("order_processing", "E6", "after", conf="B_INTERCEPTOR")
    assert res.duplicate_side_effects >= 1
    res = _find("order_processing", "E6", "after", conf="SG_FULL")
    assert res.duplicate_side_effects == 0


def test_silent_corruption_rolled_back_with_compensation():
    res = _find("order_processing", "E4", "corrupt")
    rb = [s for s in res.steps if s.recovery == Strategy.ROLLBACK]
    assert rb and rb[0].restoration_verified is True
    assert res.completed
