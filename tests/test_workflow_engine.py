"""Workflow specifications: loading, validation, transitions, stage abstraction, safe expressions."""
import pytest
import yaml

from backend.core.expressions import ExpressionError, compile_expr, evaluate
from backend.core.models import WorkflowSpec
from backend.core.workflow_engine import SpecError, Workflow, load_workflow_file

EXPECTED_STATES = {
    "order_processing": ["START", "AUTHENTICATED", "CART_CREATED", "ITEM_ADDED", "CHECKOUT_COMPLETED", "CONFIRMED"],
    "document_processing": ["START", "FILE_LOADED", "FILE_VALIDATED", "REPORT_GENERATED", "COMPLETED"],
    "file_management": ["START", "FILE_CREATED", "FILE_VALIDATED", "FILE_PUBLISHED", "COMPLETED"],
    "data_pipeline": ["START", "DATA_LOADED", "DATA_VALIDATED", "DATA_TRANSFORMED", "OUTPUT_VERIFIED", "COMPLETED"],
}


def test_all_four_workflows_load_with_required_states(workflows):
    assert set(workflows) == set(EXPECTED_STATES)
    for name, states in EXPECTED_STATES.items():
        wf = workflows[name]
        assert wf.spec.states == states
        assert wf.initial == "START"
        assert len(wf.sha256) == 64


def test_every_fault_type_has_a_target(workflows):
    for wf in workflows.values():
        assert set(wf.spec.fault_targets) == {"E1", "E2", "E3", "E4", "E5", "E6"}


def test_ideal_plan_is_accepted_and_skips_are_rejected(order_wf):
    ok = ["authenticate", "create_cart", "add_item", "add_item", "checkout", "confirm_order"]
    assert order_wf.accepts(ok)
    assert not order_wf.accepts(["authenticate", "create_cart", "checkout", "confirm_order"])
    assert not order_wf.accepts(["authenticate", "create_cart", "add_item"])          # not final
    assert order_wf.next_state("START", "checkout") is None
    assert order_wf.next_state("ITEM_ADDED", "checkout") == "CHECKOUT_COMPLETED"
    assert "checkout" in order_wf.enabled("ITEM_ADDED")


def test_stage_abstraction(order_wf):
    base = dict(session_valid=False, cart_id=None, cart={}, order_count=0, orders=[], charges=0, confirmed=False)
    assert order_wf.stage_of(base) == "START"
    assert order_wf.stage_of({**base, "session_valid": True}) == "AUTHENTICATED"
    assert order_wf.stage_of({**base, "session_valid": True, "cart_id": "c1"}) == "CART_CREATED"
    assert order_wf.stage_of({**base, "cart_id": "c1", "cart": {"A12": 1}}) == "ITEM_ADDED"
    assert order_wf.stage_of({**base, "order_count": 1}) == "CHECKOUT_COMPLETED"
    assert order_wf.stage_of({**base, "confirmed": True}) == "CONFIRMED"
    assert order_wf.stage_of(None) is None


def _spec_dict(order_wf):
    return yaml.safe_load(order_wf.source)


def test_rejects_transition_to_unknown_state(order_wf):
    d = _spec_dict(order_wf)
    d["transitions"].append({"from": "START", "action": "authenticate", "to": "NOWHERE"})
    with pytest.raises(Exception):
        Workflow(WorkflowSpec.model_validate(d))


def test_rejects_unsafe_expression_in_yaml(order_wf):
    d = _spec_dict(order_wf)
    d["invariants"].append({"name": "evil", "expr": "__import__('os').system('echo pwned')"})
    with pytest.raises((SpecError, ExpressionError, ValueError)):
        Workflow(WorkflowSpec.model_validate(d))


def test_rejects_tool_not_implemented_by_environment(order_wf):
    d = _spec_dict(order_wf)
    d["tools"]["delete_everything"] = {"description": "x", "idempotent": True, "reversibility": "R"}
    d["transitions"].append({"from": "START", "action": "delete_everything", "to": "START"})
    with pytest.raises((SpecError, ValueError)):
        Workflow(WorkflowSpec.model_validate(d))


def test_compensable_tool_requires_compensation(order_wf):
    d = _spec_dict(order_wf)
    d["tools"]["add_item"].pop("compensation")
    with pytest.raises(Exception):
        WorkflowSpec.model_validate(d)


def test_load_workflow_file_hashes_content(tmp_path, order_wf):
    p = tmp_path / "wf.yaml"
    p.write_bytes(order_wf.source.encode("utf-8"))
    wf = load_workflow_file(p)
    assert wf.sha256 == order_wf.sha256
    p.write_bytes(order_wf.source.replace("version: \"1.0.0\"", "version: \"1.0.1\"").encode("utf-8"))
    assert load_workflow_file(p).sha256 != order_wf.sha256


@pytest.mark.parametrize("src", [
    "__import__('os')", "obs.__class__", "().__class__.__bases__", "open('x')", "eval('1')",
    "[x for x in obs]", "obs.keys()", "lambda: 1", "exec('1')", "obs._private",
])
def test_expression_evaluator_rejects_unsafe_constructs(src):
    with pytest.raises(ExpressionError):
        compile_expr(src)
        evaluate(src, {"obs": {"a": 1}})


def test_expression_evaluator_allowlisted_functions():
    ctx = {"obs": {"cart": {"A": 2, "B": 3}, "orders": [{"id": "o1"}, {"id": "o2"}], "n": None},
           "args": {"sku": "A", "qty": 2}}
    assert evaluate("get(obs.cart, args.sku, 0) + args.qty", ctx) == 4
    assert evaluate("sum(values(obs.cart))", ctx) == 5
    assert evaluate("'o2' in pluck(obs.orders, 'id')", ctx) is True
    assert evaluate("obs.orders[-1].id", ctx) == "o2"
    assert evaluate("len(sha256('abc'))", ctx) == 64
    assert evaluate("obs.n is None", ctx) is True
    with pytest.raises(ExpressionError):
        evaluate("obs.missing.field", ctx)
