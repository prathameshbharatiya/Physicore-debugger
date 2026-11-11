"""DET: determinism (NFR-VW-01): same report bytes, same output bytes, whatever the environment."""
import hashlib
import json
import os
import shutil
import subprocess
import sys

from physicore_collector.investigation_view.service import run_render

from .fx import SRC, EXAMPLE_JSON, ROOT, gen, write_report


def digest(fmt, path):
    return hashlib.sha256(run_render(path, fmt).text.encode()).hexdigest()


def sub(path, fmt, **env):
    e = {**os.environ, "PYTHONPATH": str(SRC), **env}
    return subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "render", str(path), "--format", fmt], capture_output=True, env=e).stdout


def test_DET_01_repeated_runs_are_identical(tmp_path):
    p = write_report(tmp_path, gen(30, 3, m2_every=3, blockers=1))
    for fmt in ("markdown", "html"):
        assert digest(fmt, p) == digest(fmt, p)


def test_DET_02_hash_seeds_do_not_matter(tmp_path):
    p = write_report(tmp_path, gen(25, 3, m2_every=3, blockers=1))
    for fmt in ("markdown", "html"):
        outs = {sub(p, fmt, PYTHONHASHSEED=s) for s in ("0", "1", "12345", "random")}
        assert len(outs) == 1


def test_DET_03_the_working_directory_and_file_name_do_not_matter(tmp_path):
    d = gen(12, 2)
    a = write_report(tmp_path, d, "a.json"); (tmp_path / "x" / "y").mkdir(parents=True); b = write_report(tmp_path / "x" / "y", d, "other_name.json")
    for fmt in ("markdown", "html"):
        assert digest(fmt, a) == digest(fmt, b)


def test_DET_04_locale_and_timezone_do_not_matter(tmp_path):
    p = write_report(tmp_path, gen(12, 2))
    for fmt in ("markdown", "html"):
        assert sub(p, fmt, LC_ALL="C", TZ="UTC") == sub(p, fmt, LC_ALL="C.UTF-8", TZ="Asia/Kolkata") == sub(p, fmt, LANG="de_DE.UTF-8", TZ="America/Los_Angeles")


def test_DET_05_a_copy_of_the_file_gives_the_same_output(tmp_path):
    q = tmp_path / "copy.json"; shutil.copy(EXAMPLE_JSON, q)
    for fmt in ("markdown", "html"):
        assert digest(fmt, EXAMPLE_JSON) == digest(fmt, q)


def test_DET_06_different_key_order_in_the_input_changes_only_the_report_digest(tmp_path):
    d = json.loads(EXAMPLE_JSON.read_text())
    p1 = tmp_path / "1.json"; p1.write_text(json.dumps(d, sort_keys=True, indent=1))
    p2 = tmp_path / "2.json"; p2.write_text(json.dumps(d, sort_keys=False))
    a, b = run_render(p1, "markdown").text, run_render(p2, "markdown").text
    strip = lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("Report file:"))
    assert strip(a) == strip(b) and a != b


def test_DET_07_the_page_contains_no_timestamp_or_random_token(tmp_path):
    import re
    html = run_render(EXAMPLE_JSON, "html").text
    assert not re.search(r"20\d\d-\d\d-\d\dT", html) and "Math.random" not in html and "Date.now" not in html and "new Date" not in html


def test_DET_08_the_script_does_not_depend_on_time_or_randomness():
    from physicore_collector.investigation_view.html_assets import JS
    for bad in ("Math.random", "Date.now", "new Date", "performance.now", "crypto.getRandomValues"):
        assert bad not in JS


def test_DET_09_verification_output_is_deterministic(tmp_path):
    from .fx import EXAMPLE_BAG
    a = run_render(EXAMPLE_JSON, "html", EXAMPLE_BAG).text
    assert a == run_render(EXAMPLE_JSON, "html", EXAMPLE_BAG).text


def test_DET_10_view_does_not_change_when_called_twice():
    from .fx import view_of
    d = gen(10, 2, blockers=1)
    v1 = view_of(d); v2 = view_of(d)
    assert json.dumps(v1, sort_keys=True) == json.dumps(v2, sort_keys=True)
