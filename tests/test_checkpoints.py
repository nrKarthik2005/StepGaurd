"""Checkpoints, the irreversible barrier, rollback with compensation, verified restoration."""
import random

from backend.core.checkpoint_manager import CheckpointManager
from backend.core.models import Action
from backend.core.state_observer import StateObserver


def _mgr(order_wf, env):
    return CheckpointManager(env, StateObserver(env, random.Random(0)), order_wf)


def _prepare(env):
    env.call("authenticate", {"user": "u"})
    return env.call("create_cart", {})["cart_id"]


def test_commit_and_barrier(order_wf, order_env):
    env, _ = order_env
    m = _mgr(order_wf, env)
    k0 = m.commit("START", [], [], env.observe(), 0)
    assert k0.index == 0 and m.barrier == 0 and m.latest is k0
    m.commit("AUTHENTICATED", ["authenticate"], [], env.observe(), 1)
    k2 = m.commit("CHECKOUT_COMPLETED", ["authenticate", "checkout"], ["checkout:c"], env.observe(), 2,
                  irreversible=True)
    assert m.barrier == k2.index
    assert [e.event for e in m.events] == ["COMMIT", "COMMIT", "COMMIT", "BARRIER"]


def test_feasibility_rules(order_wf, order_env):
    env, _ = order_env
    m = _mgr(order_wf, env)
    for i in range(3):
        m.commit("S", [], [], env.observe(), i)
    assert m.feasible(1, [Action(tool="add_item")])[0]                       # compensable
    assert m.feasible(1, [Action(tool="create_cart")])[0]                    # reversible
    ok, why = m.feasible(1, [Action(tool="checkout")])
    assert not ok and "irreversible" in why
    m.commit("X", [], [], env.observe(), 4, irreversible=True)               # barrier moves to k3
    ok, why = m.feasible(1, [])
    assert not ok and "barrier" in why
    assert not m.feasible(99, [])[0]


def test_rollback_compensates_observed_effect_and_verifies(order_wf, order_env):
    env, _ = order_env
    cart = _prepare(env)
    m = _mgr(order_wf, env)
    k = m.commit("CART_CREATED", ["authenticate", "create_cart"], [], env.observe(), 2)
    a = Action(tool="add_item", args={"cart_id": cart, "sku": "G02", "qty": 2})
    before = env.observe()
    env.call("add_item", a.args)
    env.s["cart"]["G02"] += 1                       # silent corruption: 3 landed instead of 2
    after = env.observe()
    out = m.rollback(k.index, [(a, before, after)], step=3)
    assert out.ok, out.reason
    assert out.compensations == 1
    assert env.observe()["cart"] == {}
    assert m.events[-1].event == "RESTORE" and m.events[-1].verified
    removed = [e for e in env.ledger if e["op"] == "remove_item"]
    assert removed and removed[-1]["qty"] == 3      # sized from what was observed, not the request


def test_nothing_to_compensate_when_effect_absent(order_wf, order_env):
    env, _ = order_env
    cart = _prepare(env)
    m = _mgr(order_wf, env)
    k = m.commit("CART_CREATED", [], [], env.observe(), 2)
    a = Action(tool="add_item", args={"cart_id": cart, "sku": "G02", "qty": 2})
    snap = env.observe()
    out = m.rollback(k.index, [(a, snap, snap)], step=3)
    assert out.ok and out.compensations == 0


def test_restoration_failure_is_detected(order_wf, order_env):
    env, _ = order_env
    _prepare(env)
    m = _mgr(order_wf, env)
    k = m.commit("CART_CREATED", [], [], env.observe(), 2)
    env.restore = lambda h: None                    # a restore that silently does nothing
    env.s["cart"]["G02"] = 1
    out = m.rollback(k.index, [], step=3)
    assert not out.ok
    assert m.events[-1].event == "RESTORE_FAILED" and not m.events[-1].verified


def test_runtime_records_checkpoints_and_barrier():
    from backend.core.workflow_engine import load_all
    from backend.experiments.runner import run_episode
    from backend.experiments.scenarios import Scenario
    res = run_episode(load_all()["order_processing"], Scenario("order_processing", 0, 0, None), "SG_FULL")
    events = [e.event for e in res.checkpoints]
    assert "COMMIT" in events and "BARRIER" in events
    after_checkout = [s for s in res.steps if s.action == "confirm_order"][0]
    assert after_checkout.barrier > 0
    res = run_episode(load_all()["order_processing"], Scenario("order_processing", 0, 0, None), "SG_NO_CHECKPOINT")
    assert not res.checkpoints
