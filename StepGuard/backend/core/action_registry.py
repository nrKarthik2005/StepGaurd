"""Explicit registries: environment classes and named predicates.

Workflow YAML files may only refer to environments and predicates registered here; there is
no dynamic import or code execution driven by a specification.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from backend.environments.document_env import DocumentEnvironment
from backend.environments.file_env import FileEnvironment
from backend.environments.order_env import OrderEnvironment
from backend.environments.pipeline_env import PipelineEnvironment

ENVIRONMENTS = {
    "order_service": OrderEnvironment,
    "document_store": DocumentEnvironment,
    "file_store": FileEnvironment,
    "csv_pipeline": PipelineEnvironment,
}

# A predicate receives the evaluation context and the condition's params and returns
# (passed, message, suggested_fix) where suggested_fix is an optional {"tool", "args"} dict.
PredicateResult = Tuple[bool, Optional[str], Optional[Dict[str, Any]]]
PREDICATES: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], PredicateResult]] = {}


def predicate(name: str):
    def deco(fn):
        PREDICATES[name] = fn
        return fn
    return deco


@predicate("cart_matches_request")
def cart_matches_request(ctx, params) -> PredicateResult:
    """The cart must contain exactly the requested quantities. If an item is short,
    suggest the add_item call that would complete it."""
    cart = ctx["obs"].get("cart", {})
    wanted = ctx["task"].get("items", {})
    if cart == wanted:
        return True, None, None
    for sku, qty in wanted.items():
        have = cart.get(sku, 0)
        if have < qty:
            return (False, f"cart is missing {qty - have} x {sku}",
                    {"tool": "add_item", "args": {"cart_id": ctx["obs"].get("cart_id"), "sku": sku, "qty": qty - have}})
    extra = {k: v for k, v in cart.items() if v > wanted.get(k, 0)}
    return False, f"cart has unrequested quantities {extra}", None
