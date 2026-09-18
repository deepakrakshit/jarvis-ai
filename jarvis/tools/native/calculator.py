"""JARVIS Native Safe Mathematical Calculator.

Evaluates mathematical expressions safely using an AST parser, avoiding arbitrary eval.
"""

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

# Allowed safe binary and unary operators
OPERATORS: dict[type[ast.AST], Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitXor: operator.pow,  # Mathematical convention: caret denotes exponentiation
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Allowed safe math functions
FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "ceil": math.ceil,
    "floor": math.floor,
    "abs": abs,
    "round": round,
}

CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    if isinstance(node, ast.Name):
        if node.id in CONSTANTS:
            return float(CONSTANTS[node.id])
        raise ValueError(f"Unknown variable or constant: '{node.id}'")

    if isinstance(node, ast.BinOp):
        bin_op_type = type(node.op)
        if bin_op_type not in OPERATORS:
            raise ValueError(f"Unsupported binary operator: {bin_op_type.__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op_fn = OPERATORS[bin_op_type]
        return float(op_fn(left, right))

    if isinstance(node, ast.UnaryOp):
        unary_op_type = type(node.op)
        if unary_op_type not in OPERATORS:
            raise ValueError(f"Unsupported unary operator: {unary_op_type.__name__}")
        operand = _eval_node(node.operand)
        unary_fn = OPERATORS[unary_op_type]
        return float(unary_fn(operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise ValueError(f"Unsupported or dangerous function call: {ast.dump(node.func)}")
        func = FUNCTIONS[node.func.id]
        args = [_eval_node(arg) for arg in node.args]
        return float(func(*args))

    raise ValueError(f"Unsupported syntax expression: {type(node).__name__}")


def evaluate_expression(expression: str) -> dict[str, Any]:
    """Safely evaluate a mathematical expression."""
    clean_expr = expression.strip().replace("^", "**")
    if not clean_expr:
        raise ValueError("Expression cannot be empty.")

    try:
        parsed = ast.parse(clean_expr, mode="eval")
        result = _eval_node(parsed.body)
        return {
            "expression": clean_expr,
            "result": result,
            "status": "success",
        }
    except Exception as exc:
        raise ValueError(f"Failed to evaluate expression '{clean_expr}': {exc}") from exc
