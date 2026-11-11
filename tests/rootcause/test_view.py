"""VIEW (M4e): one block list feeds Markdown and HTML; text is escaped; the page is static and self-contained."""
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from physicore_collector.rootcause import investigate as V, view as W
from tests.rootcause.hyp_fx import scenario

TOOL = Path(__file__).parent / "tools" / "parity_m4.py"


@pytest.fixture(scope="module")
def reps(tmp_path_factory):
    t = tmp_path_factory.mktemp("view")
    out = {}
    for k in ("F03", "F02", "F09", "F01"):
        s = scenario(t, k)
        out[k] = V.investigate(s["report"], dependencies_path=s["deps"], evidence_path=s["bag"], verify=True)
    return out | {"tmp": t}


def files(rep, tmp, name):
    j, m, h = (tmp / f"{name}.json", tmp / f"{name}.md", tmp / f"{name}.html")
    j.write_text(V.to_json(rep))
    m.write_text(W.render_markdown(rep))
    h.write_text(W.render_html(rep))
    return j, m, h


def parity(j, m, h):
    return subprocess.run([sys.executable, "-I", str(TOOL), str(j), str(m), str(h)], capture_output=True, text=True)


@pytest.mark.parametrize("k", ["F03", "F02", "F09", "F01"])
def test_VIEW_01_independent_parity_verifier_agrees(reps, k):
    r = parity(*files(reps[k], reps["tmp"], k))
    assert r.returncode == 0 and "parity: OK" in r.stdout, r.stdout


def test_VIEW_02_the_verifier_is_independent_and_detects_differences(reps):
    src = TOOL.read_text()
    assert "physicore" not in src.replace("imports nothing from physicore_collector", "")
    j, m, h = files(reps["F03"], reps["tmp"], "d")
    m.write_text(m.read_text().replace("command_not_followed", "something_else"))
    assert parity(j, m, h).returncode == 1
    j, m, h = files(reps["F03"], reps["tmp"], "d2")
    h.write_text(h.read_text().replace("</body>", "<script>1</script></body>"))
    assert parity(j, m, h).returncode == 1
    j, m, h = files(reps["F03"], reps["tmp"], "d3")
    h.write_text(h.read_text().replace("Level 2:", "Level 3:"))
    assert parity(j, m, h).returncode == 1
    j, m, h = files(reps["F03"], reps["tmp"], "d4")
    m.write_text(m.read_text() + "\nThis was caused by the driver.\n")
    assert parity(j, m, h).returncode == 1


def test_VIEW_03_the_page_is_static_and_self_contained(reps):
    h = W.render_html(reps["F03"])
    assert h.startswith("<!doctype html>") and "<script" not in h.lower() and "Content-Security-Policy" in h and "default-src 'none'" in h
    assert not re.search(r"(?i)(src|href)=", h) and "http" not in h.lower().replace("http-equiv", "")
    assert "<title>Hypothesis report</title>" in h and h.count("<h1>") == 1


def test_VIEW_04_data_is_escaped_in_both_formats(reps):
    r = copy.deepcopy(reps["F03"])
    evil = '<img src=x onerror=alert(1)> [x](http://evil.example) `code` | pipe‮\x07'
    r["units"][0]["topics"] = [evil]
    r["units"][0]["missing_evidence"] = [evil]
    r["units"][0]["instances"][0]["entry_id"] = evil
    h, m = W.render_html(r), W.render_markdown(r)
    assert "<img" not in h and "&lt;img" in h and "‮" not in h and "‮" not in m and "\x07" not in h
    assert not re.search(r"(?<!\\)[<>\[\]`|]", m.split("Unit 1 of 1")[1].split("\n")[0]) and "](http" not in m and "\\|" in m and "\\`" in m


def test_VIEW_05_long_data_is_truncated_but_fixed_sentences_are_not(reps):
    r = copy.deepcopy(reps["F03"])
    r["units"][0]["topics"] = ["/" + "a" * 500]
    m = W.render_markdown(r)
    assert "…" in m and "a" * 200 not in m
    assert V.K.LEVEL_SENTENCE[2] in m.replace("\\", "") and r["units"][0]["instances"][0]["mechanism"].replace(".", "") in m.replace("\\", "").replace(".", "")


def test_VIEW_06_both_formats_are_deterministic_and_html_carries_the_level_badge(reps):
    r = reps["F03"]
    assert W.render_markdown(r) == W.render_markdown(copy.deepcopy(r)) and W.render_html(r) == W.render_html(copy.deepcopy(r))
    assert 'class="badge L2"' in W.render_html(r)


def test_VIEW_07_the_empty_report_says_so(reps):
    m = W.render_markdown(reps["F09"])
    assert "No symptom was reported" in m and "## Unit" not in m


def test_VIEW_08_every_section_of_a_unit_appears(reps):
    m = W.render_markdown(reps["F02"]).replace("\\", "")
    for s in ("Set status:", "What would raise it:", "Bindings:", "The claim ladder", "Limits", "Catalogue digest", "proposed"):
        assert s in m, s
    r = copy.deepcopy(reps["F03"])
    r["sensitivity"] = [{"without": "dependency_file", "robust": False, "changed_units": ["u1"]}]
    r["problems"] = ["A note."]
    m2 = W.render_markdown(r).replace("\\", "")
    assert "Without the dependency_file: the verdicts changed for u1." in m2 and "Notes on the inputs" in m2


def test_VIEW_09_no_causal_words_in_any_generated_text(reps):
    for k in ("F03", "F02", "F01"):
        text = (W.render_markdown(reps[k]) + W.render_html(reps[k])).lower().replace("\\", "")
        for w in ("caused", "because", "root cause", "probability", "likelihood"):
            assert w not in text, (k, w)
