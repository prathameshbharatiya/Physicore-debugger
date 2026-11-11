"""Benchmarks of the investigation view. Usage: python -m tests.investigation_view.tools.bench [N ...]. Prints measured numbers only; no thresholds are asserted."""
import json
import resource
import sys
import time

from physicore_collector.investigation_view.html_static import render_html
from physicore_collector.investigation_view.load import parse_report_bytes
from physicore_collector.investigation_view.markdown import render_markdown
from physicore_collector.investigation_view.model import build_view
from tests.investigation_view.fx import gen


def main(sizes):
    for n in sizes:
        raw = json.dumps(gen(n, lanes=2, blockers=5), sort_keys=True).encode()
        t0 = time.perf_counter(); res = parse_report_bytes(raw); t1 = time.perf_counter()
        if res.report is None:
            print(n, "report rejected:", res.problems[:1]); continue
        v = build_view(res.report, sha256=res.sha256, size=res.size); t2 = time.perf_counter()
        md = render_markdown(v); t3 = time.perf_counter()
        html = render_html(v); t4 = time.perf_counter()
        print(f"symptoms={n} report={len(raw)/1e6:.1f}MB load={t1-t0:.2f}s view={t2-t1:.2f}s markdown={t3-t2:.2f}s ({len(md)/1e6:.2f}MB) "
              f"html={t4-t3:.2f}s ({len(html)/1e6:.2f}MB) peak_rss={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.0f}MB", flush=True)


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]] or [100, 1000, 10000])
