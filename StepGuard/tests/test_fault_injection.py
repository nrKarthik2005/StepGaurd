"""Deterministic, seeded, configuration-independent fault injection."""
import random

import pytest

from backend.core.fault_injector import FaultInjector
from backend.core.models import Action
from backend.core.workflow_engine import load_all
from backend.experiments.runner import run_episode
from backend.experiments.scenarios import Scenario, build_scenarios, make_task, stable_seed

FAULTS = ["E1", "E2", "E3", "E4", "E5", "E6"]


def _strip(res):
    d = res.model_dump(mode="json")
    d.pop("wall_ms", None)
    d.pop("verification_ms", None)
    for s in d["steps"]:
        s.pop("timestamp", None)
        s.pop("verification_ms", None)
    return d


@pytest.mark.parametrize("fault", FAULTS)
def test_same_scenario_same_trace(fault):
    wf = load_all()["data_pipeline"]
    sc = Scenario("data_pipeline", 3, 1, fault)
    assert _strip(run_episode(wf, sc, "SG_FULL", 0.03)) == _strip(run_episode(wf, sc, "SG_FULL", 0.03))


@pytest.mark.parametrize("wf_name", ["order_processing", "document_processing", "file_management", "data_pipeline"])
@pytest.mark.parametrize("fault", FAULTS)
def test_every_fault_fires_identically_across_configurations(wf_name, fault):
    wf = load_all()[wf_name]
    fired = {}
    for conf in ("A_UNGUARDED", "B_INTERCEPTOR", "SG_FULL"):
        res = run_episode(wf, Scenario(wf_name, 1, 0, fault), conf)
        fired[conf] = (res.fault_fired, res.fault_variant)
    assert fired["A_UNGUARDED"][0], f"{fault} did not fire in {wf_name}"
    assert len({v[1] for v in fired.values()}) == 1


def test_stable_seed_is_reproducible_and_distinct():
    assert stable_seed("a", 1, 2, "E1") == stable_seed("a", 1, 2, "E1")
    assert stable_seed("a", 1, 2, "E1") != stable_seed("a", 1, 2, "E2")
    assert make_task("data_pipeline", 4, 1) == make_task("data_pipeline", 4, 1)


def test_build_scenarios_is_full_factorial():
    sc = build_scenarios(["order_processing", "file_management"], 3, [0, 1])
    assert len(sc) == 2 * 3 * 2 * 7
    assert len({(s.task_id, s.condition) for s in sc}) == len(sc)


def test_e1_replaces_proposal(order_wf):
    inj = FaultInjector("E1", order_wf, random.Random(0), {"checkout": 1})
    a = inj.on_propose(Action(tool="checkout", args={"cart_id": "c"}), env=None, step=5)
    assert a.tool == "confirm_order" and a.origin == "fault_injector" and inj.fired and inj.fire_step == 5


def test_e3_drops_output_field_but_applies_effect(order_wf, order_env):
    env, _ = order_env
    env.call("authenticate", {"user": "u"})
    inj = FaultInjector("E3", order_wf, random.Random(0), {"create_cart": 1})
    out = inj.execute(env, Action(tool="create_cart"), 2)
    assert "cart_id" not in out and env.observe()["cart_id"] is not None


def test_e5_queues_a_replay(order_wf, order_env):
    env, task = order_env
    env.call("authenticate", {"user": "u"})
    cart = env.call("create_cart", {})["cart_id"]
    for it in task["item_list"]:
        env.call("add_item", {"cart_id": cart, **it})
    inj = FaultInjector("E5", order_wf, random.Random(0), {"checkout": 1})
    inj.execute(env, Action(tool="checkout", args={"cart_id": cart}), 7)
    replay = inj.pop_replay(8)
    assert replay.tool == "checkout" and replay.origin == "fault_injector" and inj.fired
    assert inj.pop_replay(9) is None


def test_no_fault_means_no_injector_effect(order_wf):
    inj = FaultInjector(None, order_wf, random.Random(0), {})
    a = Action(tool="checkout")
    assert inj.on_propose(a, None, 1) is a and not inj.fired


def test_sandbox_is_isolated_and_cleaned(tmp_root):
    from backend.environments.file_env import FileEnvironment
    env = FileEnvironment(make_task("file_management", 0, 0), sandbox_root=tmp_root / "sandbox")
    root = env.sandbox
    assert str(root).startswith(str(tmp_root))
    env.cleanup()
    assert not root.exists()
