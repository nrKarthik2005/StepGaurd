"""Pre- and post-condition checking and the effect probe."""
from backend.core.contract_engine import ContractEngine, action_key
from backend.core.models import Action, ProbeResult, Verdict


def _ready(env):
    env.call("authenticate", {"user": "u"})
    cart = env.call("create_cart", {})["cart_id"]
    return cart


def test_blocks_unknown_tool_and_wrong_role(order_wf, order_env):
    env, task = order_env
    ce = ContractEngine(order_wf)
    r = ce.precheck("START", Action(tool="format_disk"), env.observe(), task, set(), set(), set())
    assert (r.verdict, r.failed) == (Verdict.BLOCK, "known_tool")
    r = ce.precheck("START", Action(tool="authenticate", args={"user": "u"}, role="guest"), env.observe(), task,
                    set(), set(), set())
    assert (r.verdict, r.failed) == (Verdict.BLOCK, "permission")


def test_blocks_out_of_order_action_with_enabled_set(order_wf, order_env):
    env, task = order_env
    r = ContractEngine(order_wf).precheck("START", Action(tool="checkout", args={"cart_id": "x"}), env.observe(),
                                          task, set(), set(), set())
    assert r.verdict == Verdict.BLOCK and r.failed == "transition_defined"
    assert r.enabled == ["authenticate"]


def test_blocks_missing_dependency_and_suggests_it(order_wf, order_env):
    env, task = order_env
    cart = _ready(env)
    r = ContractEngine(order_wf).precheck("CART_CREATED", Action(tool="add_item", args={"cart_id": cart, "sku": "G02",
                                                                                        "qty": 1}),
                                          env.observe(), task, {"authenticate"}, set(), set())
    assert r.verdict == Verdict.BLOCK and r.failed == "dependencies" and r.fix == {"tool": "create_cart"}


def test_argument_schema(order_wf, order_env):
    env, task = order_env
    cart = _ready(env)
    ce = ContractEngine(order_wf)
    done = {"authenticate", "create_cart"}
    for args, needle in (({"cart_id": cart, "sku": "bad", "qty": 1}, "does not match"),
                         ({"cart_id": cart, "sku": "G02", "qty": 0}, "below minimum"),
                         ({"cart_id": cart, "sku": "G02", "qty": "2"}, "must be int"),
                         ({"cart_id": cart, "sku": "G02"}, "missing argument"),
                         ({"cart_id": cart, "sku": "G02", "qty": 1, "price": 0}, "unexpected")):
        r = ce.precheck("CART_CREATED", Action(tool="add_item", args=args), env.observe(), task, done, set(), set())
        assert r.verdict == Verdict.BLOCK and r.failed == "argument_schema" and needle in r.message, args


def test_precondition_failure_carries_fix(order_wf, order_env):
    env, task = order_env
    cart = _ready(env)
    env.remove_prerequisite()                         # session expires (fault E2)
    r = ContractEngine(order_wf).precheck(
        "CART_CREATED", Action(tool="add_item", args={"cart_id": cart, "sku": "G02", "qty": 1}), env.observe(), task,
        {"authenticate", "create_cart"}, set(), set())
    assert r.verdict == Verdict.BLOCK and r.failed == "session_valid" and r.fix == {"tool": "authenticate"}


def test_unrequested_sku_is_blocked(order_wf, order_env):
    env, task = order_env
    cart = _ready(env)
    sku = next(s for s in ("A12", "B07", "C33", "D90", "E41", "F18", "G02", "H65") if s not in task["items"])
    r = ContractEngine(order_wf).precheck(
        "CART_CREATED", Action(tool="add_item", args={"cart_id": cart, "sku": sku, "qty": 1}), env.observe(), task,
        {"authenticate", "create_cart"}, set(), set())
    assert r.verdict == Verdict.BLOCK and r.failed == "sku_requested"


def test_duplicate_non_idempotent_blocked_when_committed_or_in_flight(order_wf, order_env):
    env, task = order_env
    ce = ContractEngine(order_wf)
    a = Action(tool="checkout", args={"cart_id": "cart-1"})
    key = action_key(order_wf.contract("checkout"), a)
    assert key == "checkout:cart-1"
    for committed, flight in (({key}, set()), (set(), {key})):
        r = ce.precheck("ITEM_ADDED", a, env.observe(), task, {"add_item"}, committed, flight)
        assert r.verdict == Verdict.BLOCK and r.failed == "no_duplicate" and r.duplicate
    # idempotent tools are never treated as duplicates
    assert action_key(order_wf.contract("authenticate"), Action(tool="authenticate")) is None


def test_unobservable_state_blocks(order_wf, order_env):
    env, task = order_env
    r = ContractEngine(order_wf).precheck("START", Action(tool="authenticate", args={"user": "u"}), None, task,
                                          set(), set(), set())
    assert (r.verdict, r.failed) == (Verdict.BLOCK, "observable")


def test_accept_for_correct_execution(order_wf, order_env):
    env, task = order_env
    cart = _ready(env)
    ce = ContractEngine(order_wf)
    a = Action(tool="add_item", args={"cart_id": cart, "sku": "G02", "qty": 3})
    pre = ce.precheck("CART_CREATED", a, env.observe(), task, {"authenticate", "create_cart"}, set(), set())
    assert pre.verdict == Verdict.ACCEPT
    before = env.observe()
    out = env.call("add_item", a.args)
    post = ce.postcheck("CART_CREATED", a, out, before, env.observe(), task)
    assert post.verdict == Verdict.ACCEPT and post.observed_stage == "ITEM_ADDED"


def test_postcheck_violations_and_inconclusive(order_wf, order_env):
    env, task = order_env
    cart = _ready(env)
    ce = ContractEngine(order_wf)
    a = Action(tool="add_item", args={"cart_id": cart, "sku": "G02", "qty": 3})
    before = env.observe()
    out = env.call("add_item", a.args)
    after = env.observe()
    r = ce.postcheck("CART_CREATED", a, {"status": "ok"}, before, after, task)
    assert (r.verdict, r.failed) == (Verdict.VIOLATE, "output_schema")
    r = ce.postcheck("CART_CREATED", a, out, before, before, task)              # effect not visible
    assert (r.verdict, r.failed) == (Verdict.VIOLATE, "qty_increased")
    r = ce.postcheck("CART_CREATED", a, out, before, None, task)                # unobservable
    assert r.verdict == Verdict.INCONCLUSIVE
    bad = dict(after, cart={"G02": -1})
    r = ce.postcheck("CART_CREATED", Action(tool="authenticate", args={"user": "u"}), {"token": "t", "expires_in": 1},
                     before, dict(bad, session_valid=True), task)
    assert r.verdict == Verdict.VIOLATE


def test_probe(order_wf):
    ce = ContractEngine(order_wf)
    a = Action(tool="add_item", args={"cart_id": "c", "sku": "A12", "qty": 2})
    before = {"cart": {}}
    assert ce.probe(a, None, before, {"cart": {"A12": 2}}, {}) == ProbeResult.APPLIED
    assert ce.probe(a, None, before, {"cart": {}}, {}) == ProbeResult.ABSENT
    assert ce.probe(a, None, before, {"cart": {"A12": 3}}, {}) == ProbeResult.UNKNOWN
    assert ce.probe(a, None, before, None, {}) == ProbeResult.UNKNOWN
