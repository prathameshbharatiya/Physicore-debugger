"""Text-substitution mutation harness for M3e (same discipline as M3d: mutants run against a COPY of src/, a control must pass, every mutant must be KILLED
or be a justified equivalent listed in its mutants module).

usage: python -m tests.investigation_view.tools.mutate [--check] [module ...]    (default: mutants_core, mutants_js)
  --check   only validate that every pattern occurs exactly once (no test is run)
Environment: MUTANT_TIMEOUT_S (default 120), MUTANT_EXTRA_PATH (added to PYTHONPATH of the test runs, e.g. the Playwright shim), MUTANT_ONLY (substring filter)."""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.localization.tools.mutate import Mutant

ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_S = int(os.environ.get("MUTANT_TIMEOUT_S", "120"))


def _run(src: Path, tests: tuple[str, ...]) -> int:
    extra = os.environ.get("MUTANT_EXTRA_PATH", "")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(x for x in (str(src), str(ROOT), extra) if x), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=ROOT, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124
    return p.returncode


def run(mutants: list[Mutant], check: bool = False) -> list[tuple[str, str]]:
    only = os.environ.get("MUTANT_ONLY", "")
    mutants = [m for m in mutants if only in m.name]
    results = []
    with tempfile.TemporaryDirectory() as td:
        ctl = Path(td) / "control"
        shutil.copytree(ROOT / "src", ctl, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if not check:
            sel = tuple(sorted({t for m in mutants for t in m.tests}))
            results.append(("CONTROL (unmodified)", "PASS" if _run(ctl, sel) == 0 else "FAIL (control must pass)"))
        for i, m in enumerate(mutants):
            text = (ROOT / "src" / m.file).read_text()
            if text.count(m.old) != 1:
                results.append((m.name, f"INVALID MUTANT (pattern found {text.count(m.old)} times)"))
                continue
            if check:
                results.append((m.name, "VALID"))
                continue
            d = Path(td) / f"m{i}"
            shutil.copytree(ROOT / "src", d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (d / m.file).write_text(text.replace(m.old, m.new))
            results.append((m.name, "KILLED" if _run(d, m.tests) != 0 else "SURVIVED"))
            print(results[-1][0], "->", results[-1][1], flush=True)
    return results


def main(argv: list[str]) -> int:
    check = "--check" in argv
    mods = [a for a in argv if not a.startswith("--")] or ["mutants_core", "mutants_js"]
    ms: list[Mutant] = []
    for m in mods:
        ms += importlib.import_module(f"tests.investigation_view.tools.{m}").MUTANTS
    res = run(ms, check)
    bad = [r for r in res if r[1] not in ("KILLED", "PASS", "VALID")]
    print(f"\n{len(res)} results; killed {sum(1 for r in res if r[1] == 'KILLED')}; not killed or invalid: {len(bad)}")
    for n, s in bad:
        print("  ", s, "-", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
