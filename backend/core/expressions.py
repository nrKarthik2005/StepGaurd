"""A small, allowlisted expression language for YAML contracts.

Workflow files must never execute arbitrary Python. Expressions are parsed with `ast`
and evaluated by walking an explicit allowlist of node types:

  literals, names from the evaluation context, attribute/subscript access on data,
  arithmetic (+ - * / // %), comparisons (incl. `in`), and/or/not, conditional
  expressions, and calls to a fixed set of pure helper functions.

Anything else (imports, lambdas, comprehensions, dunder attributes, method calls,
calls to unknown functions) is rejected when the specification is loaded.
"""
from __future__ import annotations

import ast
import hashlib
import operator
from functools import lru_cache
from typing import Any, Dict


class ExpressionError(Exception):
    """Raised for invalid expressions or for values that cannot be evaluated."""


def _get(d, k, default=None):
    if isinstance(d, DotDict):
        d = d._d
    if isinstance(d, dict):
        return _wrap(d.get(k, default))
    if isinstance(d, (list, tuple)) and isinstance(k, int):
        return _wrap(d[k]) if -len(d) <= k < len(d) else default
    raise ExpressionError("get() needs a mapping")


def _unwrap(v):
    if isinstance(v, DotDict):
        return {k: _unwrap(x) for k, x in v._d.items()}
    if isinstance(v, list):
        return [_unwrap(x) for x in v]
    return v


FUNCTIONS = {
    "len": lambda x: len(_unwrap(x)),
    "abs": abs,
    "min": lambda *a: min(*[_unwrap(x) for x in a]),
    "max": lambda *a: max(*[_unwrap(x) for x in a]),
    "sum": lambda x: sum(_unwrap(x)),
    "round": round,
    "get": _get,
    "keys": lambda d: sorted(_unwrap(d).keys()),
    "values": lambda d: list(_unwrap(d).values()),
    "count": lambda seq, v: _unwrap(seq).count(_unwrap(v)),
    "sorted": lambda x: sorted(_unwrap(x)),
    "plain": _unwrap,
    "pluck": lambda seq, field: [(_unwrap(x) or {}).get(_unwrap(field)) for x in _unwrap(seq)],
    "sha256": lambda x: hashlib.sha256(str(_unwrap(x)).encode("utf-8")).hexdigest(),
}

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
        ast.Is: operator.is_, ast.IsNot: operator.is_not}
_ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
            ast.BinOp, ast.Compare, ast.Name, ast.Load, ast.Constant, ast.Attribute,
            ast.Subscript, ast.Call, ast.IfExp, ast.List, ast.Tuple, ast.Dict,
            *_BIN.keys(), *_CMP.keys())


class DotDict:
    """Read-only view that allows `obs.cart.items` style access on plain dicts."""
    __slots__ = ("_d",)

    def __init__(self, d: dict):
        self._d = d

    def __eq__(self, other):
        return _unwrap(self) == _unwrap(other)

    def __contains__(self, k):
        return k in self._d

    def __len__(self):
        return len(self._d)

    def __iter__(self):
        return iter(self._d)


def _wrap(v):
    return DotDict(v) if isinstance(v, dict) else v


@lru_cache(maxsize=4096)
def compile_expr(src: str) -> ast.Expression:
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"syntax error in expression {src!r}: {e.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ExpressionError(f"disallowed syntax {type(node).__name__} in {src!r}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExpressionError(f"private attribute access in {src!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise ExpressionError(f"only allowlisted functions may be called in {src!r}")
            if node.keywords:
                raise ExpressionError(f"keyword arguments are not allowed in {src!r}")
    return tree


def evaluate(src: str, context: Dict[str, Any]) -> Any:
    tree = compile_expr(src)
    return _unwrap(_eval(tree.body, context))


def _eval(n, ctx):
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, ast.Name):
        if n.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[n.id]
        if n.id not in ctx:
            raise ExpressionError(f"unknown name {n.id!r}")
        return _wrap(ctx[n.id])
    if isinstance(n, ast.Attribute):
        base = _eval(n.value, ctx)
        if isinstance(base, DotDict):
            if n.attr not in base._d:
                raise ExpressionError(f"missing field {n.attr!r}")
            return _wrap(base._d[n.attr])
        raise ExpressionError(f"attribute access on non-mapping ({n.attr})")
    if isinstance(n, ast.Subscript):
        base = _unwrap(_eval(n.value, ctx))
        key = _unwrap(_eval(n.slice, ctx))
        try:
            return _wrap(base[key])
        except (KeyError, IndexError, TypeError):
            raise ExpressionError(f"missing key {key!r}") from None
    if isinstance(n, ast.BoolOp):
        if isinstance(n.op, ast.And):
            for v in n.values:
                if not _unwrap(_eval(v, ctx)):
                    return False
            return True
        for v in n.values:
            if _unwrap(_eval(v, ctx)):
                return True
        return False
    if isinstance(n, ast.UnaryOp):
        v = _unwrap(_eval(n.operand, ctx))
        return (not v) if isinstance(n.op, ast.Not) else -v
    if isinstance(n, ast.BinOp):
        return _BIN[type(n.op)](_unwrap(_eval(n.left, ctx)), _unwrap(_eval(n.right, ctx)))
    if isinstance(n, ast.Compare):
        left = _unwrap(_eval(n.left, ctx))
        for op, comp in zip(n.ops, n.comparators):
            right = _unwrap(_eval(comp, ctx))
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(n, ast.IfExp):
        return _eval(n.body, ctx) if _unwrap(_eval(n.test, ctx)) else _eval(n.orelse, ctx)
    if isinstance(n, (ast.List, ast.Tuple)):
        return [_unwrap(_eval(e, ctx)) for e in n.elts]
    if isinstance(n, ast.Dict):
        return {_unwrap(_eval(k, ctx)): _unwrap(_eval(v, ctx)) for k, v in zip(n.keys, n.values)}
    if isinstance(n, ast.Call):
        args = [_eval(a, ctx) for a in n.args]
        try:
            return _wrap(FUNCTIONS[n.func.id](*args))
        except ExpressionError:
            raise
        except Exception as e:
            raise ExpressionError(f"{n.func.id}() failed: {e}") from None
    raise ExpressionError(f"unsupported node {type(n).__name__}")
