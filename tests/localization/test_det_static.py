"""DET-06 / NFR-02 / NFR-03 static checks over src/physicore_collector/localization (AST). Grows with each step; every module present is scanned."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "src" / "physicore_collector" / "localization"
BANNED_IMPORTS = {"time", "datetime", "random", "secrets", "uuid", "numpy", "decimal", "statistics", "math", "cmath"}


def _modules():
    return sorted(PKG.glob("*.py"))


def _tree(p):
    return ast.parse(p.read_text(), filename=str(p))


def test_package_modules_present():
    names = {p.stem for p in _modules()}
    assert {"__init__", "vocabulary", "intervals", "report"} <= names


def test_DET_06_no_float_literals_or_float_calls():
    bad = []
    for p in _modules():
        for n in ast.walk(_tree(p)):
            if isinstance(n, ast.Constant) and isinstance(n.value, float):
                bad.append((p.name, n.lineno, "float literal"))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float":
                bad.append((p.name, n.lineno, "float()"))
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
                bad.append((p.name, n.lineno, "true division"))
    assert not bad, bad


def test_DET_06_no_wall_clock_randomness_or_numeric_libs():
    bad = []
    for p in _modules():
        for n in ast.walk(_tree(p)):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mods = [n.module.split(".")[0]]
            bad += [(p.name, n.lineno, m) for m in mods if m in BANNED_IMPORTS]
    assert not bad, bad


def _iterates_a_set(node) -> bool:
    return (isinstance(node, (ast.Set, ast.SetComp))
            or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("set", "frozenset")))


def test_DET_06_no_direct_iteration_over_a_set_without_sorted():
    bad = []
    for p in _modules():
        for n in ast.walk(_tree(p)):
            if isinstance(n, (ast.For, ast.comprehension)) and _iterates_a_set(n.iter):
                bad.append((p.name, getattr(n, "lineno", 0), "for over set"))
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("list", "tuple", "next", "iter")
                    and n.args and _iterates_a_set(n.args[0])):
                bad.append((p.name, n.lineno, f"{n.func.id}(set)"))
    assert not bad, bad


def test_SCL_04_no_recursive_functions():
    """Direct and mutual recursion by name among module-level functions and methods (static; allow-list is empty)."""
    bad = []
    for p in _modules():
        tree = _tree(p)
        funcs = {}
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls = set()
                for c in ast.walk(n):
                    if isinstance(c, ast.Call):
                        f = c.func
                        if isinstance(f, ast.Name):
                            calls.add(f.id)
                        elif isinstance(f, ast.Attribute):
                            calls.add(f.attr)
                funcs.setdefault(n.name, set()).update(calls)
        for name in funcs:
            seen, stack = set(), list(funcs[name] & funcs.keys())
            while stack:
                x = stack.pop()
                if x == name:
                    bad.append((p.name, name))
                    break
                if x in seen:
                    continue
                seen.add(x)
                stack += list(funcs.get(x, set()) & funcs.keys())
    assert not bad, bad
