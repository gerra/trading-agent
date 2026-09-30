"""Static policy for generated strategy code. `check_source(src) -> [violations]`.

Defence in depth, layer 1 of 3 (2 = subprocess resource limits, 3 = container hardening).
The goal is simple: generated code can compute signals from candles and nothing else.
"""

from __future__ import annotations

import ast

ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "math",
        "statistics",
        "typing",
        "dataclasses",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "enum",
        "trading_agent.core.models",
        "trading_agent.strategies.base",
        "trading_agent.strategies.indicators",
    }
)
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "breakpoint",
        "input",
        "exit",
        "quit",
        "memoryview",
        "type",
        "super",
        "object",
        "help",
        "print",
    }
)
ALLOWED_DECORATORS: frozenset[str] = frozenset(
    {"property", "staticmethod", "classmethod", "dataclass"}
)
ALLOWED_DUNDER_DEFS: frozenset[str] = frozenset({"__init__"})
MAX_SOURCE_CHARS = 20_000
MAX_AST_NODES = 6_000

POLICY_DESCRIPTION = f"""Generated strategy modules MUST:
- import only from: {", ".join(sorted(ALLOWED_MODULES))}
- define exactly one class that subclasses Strategy (from trading_agent.strategies.base)
- set ClassVars `id` (snake_case, matches the file name), `description`, `default_params`
- implement `on_bar(self, ctx: StrategyContext) -> list[Signal]` using self.buy(...) / self.sell(...)
- contain only imports, one class, plain functions and constant assignments at top level
- never use: {", ".join(sorted(FORBIDDEN_NAMES))}, any dunder attribute access, `import *`,
  async code, global/nonlocal, decorators other than {", ".join(sorted(ALLOWED_DECORATORS))}
- be under {MAX_SOURCE_CHARS} characters
- be deterministic: no randomness, no clock reads (use ctx.now), no IO of any kind
"""


def check_source(source: str) -> list[str]:
    violations: list[str] = []
    if len(source) > MAX_SOURCE_CHARS:
        violations.append(f"source too long ({len(source)} > {MAX_SOURCE_CHARS} chars)")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} (line {exc.lineno})"]

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        violations.append(f"too many AST nodes ({len(nodes)} > {MAX_AST_NODES})")

    strategy_classes = 0
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_MODULES:
                    violations.append(f"import of '{alias.name}' not allowed")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                violations.append("relative imports not allowed")
            elif mod not in ALLOWED_MODULES:
                violations.append(f"import from '{mod}' not allowed")
            if any(a.name == "*" for a in node.names):
                violations.append("'import *' not allowed")
        elif isinstance(node, ast.ClassDef):
            if any(_base_name(b) == "Strategy" for b in node.bases):
                strategy_classes += 1
        elif isinstance(node, ast.FunctionDef | ast.Assign | ast.AnnAssign):
            pass
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            pass  # docstring
        else:
            violations.append(
                f"top-level statement not allowed: {type(node).__name__} (line {node.lineno})"
            )
    if strategy_classes != 1:
        violations.append(f"expected exactly one Strategy subclass, found {strategy_classes}")

    for node in nodes:
        if isinstance(node, ast.Import | ast.ImportFrom) and node not in tree.body:
            violations.append(f"nested import at line {node.lineno}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append(f"use of forbidden name '{node.id}' at line {node.lineno}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            violations.append(f"dunder attribute access '{node.attr}' at line {node.lineno}")
        elif (
            isinstance(node, ast.FunctionDef)
            and node.name.startswith("__")
            and node.name not in ALLOWED_DUNDER_DEFS
        ):
            violations.append(f"defining dunder method '{node.name}' not allowed")
        elif isinstance(
            node,
            ast.AsyncFunctionDef
            | ast.AsyncFor
            | ast.AsyncWith
            | ast.Await
            | ast.Global
            | ast.Nonlocal,
        ):
            violations.append(f"{type(node).__name__} not allowed at line {node.lineno}")
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            for dec in node.decorator_list:
                name = _decorator_name(dec)
                if name not in ALLOWED_DECORATORS:
                    violations.append(f"decorator '{name}' not allowed at line {node.lineno}")
    return violations


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    return _base_name(node) if isinstance(node, ast.Name | ast.Attribute) else "?"
