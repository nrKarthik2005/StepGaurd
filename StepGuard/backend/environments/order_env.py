"""Simulated order-processing service (in memory, no real payments).

Like many real services, checkout is NOT idempotent: calling it twice charges twice.
"""
from __future__ import annotations

from typing import Any, Dict

from .base import Environment, ToolError

PRICES = {"A12": 4.5, "B07": 12.0, "C33": 7.25, "D90": 3.0, "E41": 15.5, "F18": 9.75, "G02": 2.4, "H65": 21.0}


class OrderEnvironment(Environment):
    name = "order_service"
    family = "order_processing"

    def setup(self) -> None:
        self.s = dict(token=None, token_valid=False, cart_id=None, cart={}, orders=[], charges=0,
                      confirmed=False, n_carts=0)
        for t in ("authenticate", "create_cart", "add_item", "remove_item", "checkout", "confirm_order"):
            self.register(t, getattr(self, "_" + t))

    # ------------------------------------------------------------------ tools
    def _auth(self):
        if not self.s["token_valid"]:
            raise ToolError("401 unauthorized: session expired or missing", code=401, hint="authenticate")

    def _authenticate(self, a):
        self.s["token"] = "tok-%d" % (len(self.ledger) + 1)
        self.s["token_valid"] = True
        return {"token": self.s["token"], "expires_in": 900}

    def _create_cart(self, a):
        self._auth()
        if self.s["cart_id"] is None:
            self.s["n_carts"] += 1
            self.s["cart_id"] = "cart-%d" % self.s["n_carts"]
        return {"cart_id": self.s["cart_id"]}

    def _add_item(self, a):
        self._auth()
        if a.get("cart_id") != self.s["cart_id"] or self.s["cart_id"] is None:
            raise ToolError("400 bad request: unknown cart_id", code=400, hint="create_cart")
        sku, qty = a.get("sku"), a.get("qty")
        if sku not in PRICES or not isinstance(qty, int) or qty < 1:
            raise ToolError("422 invalid sku or quantity", code=422)
        self.s["cart"][sku] = self.s["cart"].get(sku, 0) + qty
        self.record_effect("add_item", sku, qty=qty)
        return {"status": "ok", "sku": sku, "qty": qty, "line_total": round(PRICES[sku] * qty, 2)}

    def _remove_item(self, a):
        self._auth()
        sku, qty = a.get("sku"), a.get("qty", 0)
        have = self.s["cart"].get(sku, 0)
        left = have - qty
        if left > 0:
            self.s["cart"][sku] = left
        else:
            self.s["cart"].pop(sku, None)
        self.record_effect("remove_item", sku, qty=min(qty, have))
        return {"status": "ok", "sku": sku}

    def _checkout(self, a):
        self._auth()
        if a.get("cart_id") != self.s["cart_id"]:
            raise ToolError("400 bad request: unknown cart_id", code=400, hint="create_cart")
        if not self.s["cart"]:
            raise ToolError("409 conflict: cart is empty", code=409)
        items = dict(self.s["cart"])
        amount = round(sum(PRICES[k] * v for k, v in items.items()), 2)
        order = {"order_id": "ord-%d" % (len(self.s["orders"]) + 1), "items": items, "amount": amount,
                 "status": "paid", "cart_id": self.s["cart_id"]}
        self.s["orders"].append(order)
        self.s["charges"] += 1
        self.s["cart"] = {}
        self.record_effect("checkout", self.s["cart_id"], amount=amount)
        return {"order_id": order["order_id"], "status": "paid", "amount": amount, "items": dict(items)}

    def _confirm_order(self, a):
        self._auth()
        ids = [o["order_id"] for o in self.s["orders"]]
        if a.get("order_id") not in ids:
            raise ToolError("404 not found: no such order", code=404, hint="checkout")
        self.s["confirmed"] = True
        return {"status": "confirmed", "order_id": a["order_id"]}

    # ------------------------------------------------------------------ observation
    def observe(self) -> Dict[str, Any]:
        s = self.s
        return {
            "session_valid": s["token_valid"],
            "cart_id": s["cart_id"],
            "cart": dict(s["cart"]),
            "order_count": len(s["orders"]),
            "orders": [{"order_id": o["order_id"], "items": dict(o["items"]), "cart_id": o["cart_id"],
                        "status": o["status"]} for o in s["orders"]],
            "charges": s["charges"],
            "confirmed": s["confirmed"],
        }

    def snapshot(self):
        # only the cart is restorable; orders and charges are external effects
        return {"cart": dict(self.s["cart"]), "cart_id": self.s["cart_id"]}

    def restore(self, h):
        self.s["cart"] = dict(h["cart"])
        self.s["cart_id"] = h["cart_id"]

    def truth(self):
        return self.deep(self.s)

    # ------------------------------------------------------------------ fault hooks
    def remove_prerequisite(self) -> str:
        self.s["token_valid"] = False
        return "session token expired"

    def corrupt_effect(self, tool, args, variant, before) -> str:
        if tool == "add_item":
            sku = args.get("sku")
            if variant == "suppress":
                self.s["cart"] = dict(before["cart"])
                self.ledger.pop()
                return "add_item reported ok but the cart was not changed"
            self.s["cart"][sku] = before["cart"].get(sku, 0) + args.get("qty", 0) + 1
            return "add_item reported ok but added the wrong quantity"
        return "no corruption defined"
