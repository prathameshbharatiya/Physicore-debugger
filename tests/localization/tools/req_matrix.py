"""Requirement -> test traceability (IMPLEMENTATION_PLAN Step 9 gate: "no empty row"). Reads the requirement table of M3D_REQUIREMENTS.md (path as argv[1] or
env M3D_REQUIREMENTS), maps every test family named in the "verified by" column to the collected test functions of tests/localization (by test-name
family token `test_<FAM>_` or by the file groups below) and prints / writes a markdown matrix. Exit status 1 when any requirement has no test.

This shows that a family of tests exists for each requirement and how many; it does not show that the tests are sufficient (the mutation runs do that)."""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "tests" / "localization"
FILES = {  # family -> files whose tests belong to it
    "INT": ["test_int.py"], "SYM": ["test_sym.py"], "PRE": ["test_pre.py"], "BLK": ["test_blk.py"], "DEP": ["test_dep.py"], "CON": ["test_con.py"],
    "CHN": ["test_chn.py"], "PRV": ["test_svc.py", "test_ver.py", "test_blk.py"], "WRD": ["test_svc.py", "test_det.py", "test_report_base.py"],
    "RPT": ["test_svc.py", "test_report_base.py"], "CLI": ["test_svc.py"], "DET": ["test_det.py", "test_det2.py", "test_det_static.py"],
    "SCL": ["test_scl.py"], "ADV": ["test_svc.py", "test_det.py", "test_dep.py", "test_con.py", "test_chn.py", "test_sym.py", "test_blk.py"],
    "PRES": ["test_preservation.py"], "UNC": ["test_con.py", "test_chn.py"],
}


def count_tests(files: list[str]) -> int:
    n = 0
    for f in files:
        p = TESTS / f
        if p.exists():
            n += len(re.findall(r"^def test_", p.read_text(), re.M))
    return n


def mutants() -> int:
    return sum(len(re.findall(r"^\s+Mutant\(", p.read_text(), re.M)) for p in (TESTS / "tools").glob("mutants_step*.py"))


def main() -> int:
    req = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("M3D_REQUIREMENTS", "/home/claude/work/m3d_design/M3D_REQUIREMENTS.md"))
    rows = []
    for line in req.read_text().splitlines():
        m = re.match(r"\| ((?:N?FR)-[A-Z]+-\d+|NFR-\d+) \|(.*)\| ([A-Z, ]+) \|\s*$", line)
        if m:
            rows.append((m.group(1), [f.strip() for f in m.group(3).split(",")], m.group(2).strip()))
    out = ["# Requirement matrix", "", "| Requirement | Families | Tests in those families (files) | Empty? |", "|---|---|---|---|"]
    empty = []
    for rid, fams, _ in rows:
        parts, total = [], 0
        for f in fams:
            if f == "MUT":
                k = mutants()
                parts.append(f"MUT: {k} mutants")
            else:
                k = count_tests(FILES.get(f, []))
                parts.append(f"{f}: {k}")
            total += k
        out.append(f"| {rid} | {', '.join(fams)} | {'; '.join(parts)} | {'EMPTY' if total == 0 else 'no'} |")
        if total == 0:
            empty.append(rid)
    out += ["", f"{len(rows)} requirements; empty rows: {len(empty)} {empty}"]
    text = "\n".join(out) + "\n"
    print(text)
    return 1 if empty or not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
