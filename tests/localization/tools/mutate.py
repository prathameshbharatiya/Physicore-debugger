"""Reviewable text-substitution mutation harness (TEST_PLAN 13). Mutants are applied to a COPY of src/ (never the baseline or the working
tree); each is run against the named test selection and must be KILLED (pytest fails). A control run (no mutation) must pass.

usage: python -m tests.localization.tools.mutate [mutants_module ...]   (default: every tests/localization/tools/mutants_*.py)"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_S = int(os.environ.get("MUTANT_TIMEOUT_S", "90"))


@dataclass(frozen=True)
class Mutant:
    name: str
    file: str          # relative to src/
    old: str
    new: str
    tests: tuple[str, ...]   # pytest selection (paths / -k expressions as separate args)


def _run(src: Path, tests: tuple[str, ...]) -> int:
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124                      # a mutant that makes the tests hang is detected (killed by timeout), never left running
    return p.returncode


def run(mutants: list[Mutant]) -> list[tuple[str, str]]:
    results = []
    with tempfile.TemporaryDirectory() as td:
        # control
        ctl = Path(td) / "control"
        shutil.copytree(ROOT / "src", ctl, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        sel = tuple(sorted({t for m in mutants for t in m.tests}))
        results.append(("CONTROL (unmodified)", "PASS" if _run(ctl, sel) == 0 else "FAIL (control must pass)"))
        for i, m in enumerate(mutants):
            d = Path(td) / f"m{i}"
            shutil.copytree(ROOT / "src", d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            f = d / m.file
            text = f.read_text()
            if text.count(m.old) != 1:
                results.append((m.name, f"INVALID MUTANT (pattern found {text.count(m.old)} times)"))
                continue
            f.write_text(text.replace(m.old, m.new))
            results.append((m.name, "KILLED" if _run(d, m.tests) != 0 else "SURVIVED"))
    return results


def main(argv: list[str]) -> int:
    mods = argv or sorted(p.stem for p in (Path(__file__).parent).glob("mutants_*.py"))
    mutants: list[Mutant] = []
    for m in mods:
        mutants += importlib.import_module(f"tests.localization.tools.{m}").MUTANTS
    res = run(mutants)
    width = max(len(n) for n, _ in res)
    for n, r in res:
        print(f"{n.ljust(width)}  {r}")
    bad = [r for r in res if r[1] not in ("KILLED", "PASS")]
    print(f"\n{sum(1 for _, r in res if r == 'KILLED')} killed, {len(bad)} not killed/invalid")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
