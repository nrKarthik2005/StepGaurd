"""Metric definitions, undefined denominators and statistics."""
import math

from backend.experiments.metrics import compute_summary, ratio
from backend.experiments.statistics import cluster_bootstrap_diff, cohens_h, holm, mcnemar_exact, wilson


def row(conf, cond, completed, wf="order_processing", t=0, **kw):
    base = dict(episode_id=f"{wf}-{t}-{cond}-{conf}", workflow=wf, family=wf, template=t, seed=0, condition=cond,
                configuration=conf, completed=int(completed), escalated=0, fault_fired=int(cond != "clean"),
                fault_variant=None, fault_step=3, detected=0, executed=5, unsafe_executed=0, unsafe_proposed=0,
                unsafe_blocked=0, correct_steps=5, false_flags=0, proposals=5, blocks=0, retries=0, tool_calls=5,
                state_reads=0, duplicate_side_effects=0, virtual_time=10.0, verification_ms=0.0,
                recovery_latency=None, steps=5, wall_ms=1.0, final_stage=None, escalation_reason=None)
    base.update(kw)
    return base


def test_ratio_handles_zero_denominator():
    assert ratio(0, 0) is None
    assert ratio(1, 4) == 0.25


def test_wilson_interval():
    assert wilson(0, 0) == (None, None)
    lo, hi = wilson(8, 10)
    assert 0.44 < lo < 0.5 and 0.94 < hi < 0.97
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo > 0.69


def test_mcnemar_holm_cohens_h():
    assert mcnemar_exact(0, 0) == 1.0
    assert math.isclose(mcnemar_exact(10, 0), 2 * 0.5 ** 10, rel_tol=1e-9)
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert math.isclose(adj["a"], 0.03) and math.isclose(adj["c"], 0.06) and math.isclose(adj["b"], 0.06)
    assert cohens_h(0.5, 0.5) == 0 and cohens_h(0.9, 0.5) > 0


def test_cluster_bootstrap_is_seeded():
    d = {"a": [1, 0, 1], "b": [0, 0, 1], "c": [1, 1, 1]}
    assert cluster_bootstrap_diff(d) == cluster_bootstrap_diff(d)
    mean, lo, hi = cluster_bootstrap_diff(d)
    assert lo <= mean <= hi
    assert cluster_bootstrap_diff({"a": [1.0]})[1] is None          # one cluster: no interval


def test_summary_metrics_from_rows():
    rows = []
    for t in range(4):
        rows += [row("A_UNGUARDED", "clean", True, t=t), row("A_UNGUARDED", "E1", t % 2 == 0, t=t,
                                                                unsafe_executed=1, unsafe_proposed=1)]
        rows += [row("SG_FULL", "clean", True, t=t, virtual_time=11.0, state_reads=4),
                 row("SG_FULL", "E1", True, t=t, unsafe_proposed=1, unsafe_blocked=1, detected=1)]
    s = compute_summary(rows, ["A_UNGUARDED", "SG_FULL"])
    a, sg = s["metrics"]["A_UNGUARDED"], s["metrics"]["SG_FULL"]
    assert a["TCR_clean"] == 1.0 and a["TCR_fault"] == 0.5
    assert sg["TCR_fault"] == 1.0
    assert a["EDR"] is None and a["FPRv"] is None                   # no verifier
    assert sg["EDR"] == 1.0 and sg["UAPR"] == 1.0 and a["UAPR"] == 0.0
    assert math.isclose(sg["ETO"], 0.1)
    assert sg["dReads"] == 2          # mean over all paired episodes: (+4 clean, 0 fault)
    comp = s["comparisons"]["SG_FULL_vs_A_UNGUARDED"]
    assert comp["n_pairs"] == 4 and comp["only_first"] == 2 and comp["only_second"] == 0
    assert [r["condition"] for r in s["per_condition"]] == ["clean", "E1"]


def test_metrics_undefined_when_no_faults_fire():
    rows = [row("A_UNGUARDED", "clean", True), row("SG_FULL", "clean", True)]
    m = compute_summary(rows, ["A_UNGUARDED", "SG_FULL"])["metrics"]["SG_FULL"]
    assert m["TCR_fault"] is None and m["EDR"] is None and m["RSR"] is None
