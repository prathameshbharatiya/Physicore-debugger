"""Fixtures for the M4e tests: localization reports of corpus scenarios written to files."""
import json
from pathlib import Path

from physicore_collector.localization.service import run_localization
from tests.rootcause.corpus import generate as G


def scenario(tmp: Path, fam: str, seed: int = 1):
    d = tmp / f"{fam}-{seed}"
    d.mkdir(parents=True, exist_ok=True)
    r = G.generate(fam, seed, d)
    kw = {}
    if (r / "dependencies.yaml").exists():
        kw["dependencies"] = r / "dependencies.yaml"
    if (r / "expectations.json").exists():
        kw["expectations"] = r / "expectations.json"
    rep = run_localization(r / "bag", r / "anomaly.json", **kw)
    p = d / "report.json"
    p.write_text(rep.model_dump_json())
    truth = json.loads((r / "truth.json").read_text())
    return {"report": p, "bag": r / "bag", "deps": kw.get("dependencies"), "dir": r, "truth": truth}
