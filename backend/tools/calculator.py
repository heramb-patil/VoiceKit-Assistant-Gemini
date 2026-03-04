"""Calculator tool - evaluate math expressions safely."""

import ast
import math
import operator


_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pi": math.pi, "e": math.e, "inf": math.inf,
}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]
        raise ValueError(f"Unknown name: {node.id!r}")
    if isinstance(node, ast.BinOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary operator")
        return op(_eval_node(node.operand))
    if isinstance(node, ast.Call):
        func = _eval_node(node.func)
        args = [_eval_node(a) for a in node.args]
        return func(*args)
    raise ValueError(f"Unsupported expression type: {type(node).__name__}")


async def calculate(expression: str) -> str:
    """Evaluate a mathematical expression and return the result.

    Supports: +, -, *, /, **, %, sqrt, sin, cos, tan, log, floor, ceil, round, pi, e.

    Args:
        expression: The math expression to evaluate (e.g. '2 ** 10', 'sqrt(144)', '15% of 240').
    """
    # Handle "X% of Y" shorthand
    import re
    pct_match = re.match(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", expression.strip(), re.I)
    if pct_match:
        pct, total = float(pct_match.group(1)), float(pct_match.group(2))
        result = pct / 100 * total
        return f"{pct}% of {total} = {result}"

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree.body)
        # Format nicely: drop float decimal if it's a whole number
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"{expression} = {result}"
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as exc:
        return f"Could not evaluate '{expression}': {exc}"
