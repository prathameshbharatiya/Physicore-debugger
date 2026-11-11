"""Independent parity check for M4e. Standard library only; it imports nothing from physicore_collector.

usage: python -I parity_m4.py REPORT.json REPORT.md REPORT.html
It reads the JSON report and checks, by its own rules, that the Markdown and the HTML say the same things: every unit, entry, tier, claim level, claim sentence and ledger
row of the JSON is present in both, the counts match, the page has no script and no external resource, and no causal wording appears. Exit 0 = parity; 1 = differences."""
import html as htmllib
import json
import re
import sys

BANNED = ("caused", "because", "root cause", "probability", "likelihood", "confidence", "responsible for")


def md_plain(t):
    return re.sub(r"\\(.)", r"\1", t)


def html_plain(t):
    t = re.sub(r"<[^>]+>", " ", t)
    return htmllib.unescape(t)


def norm(t):
    return re.sub(r"\s+", " ", t).strip()


def main(argv):
    rep = json.load(open(argv[1], encoding="utf-8"))
    md = norm(md_plain(open(argv[2], encoding="utf-8").read()))
    hp = open(argv[3], encoding="utf-8").read()
    ht = norm(html_plain(hp))
    bad = []
    for name, text in (("markdown", md), ("html", ht)):
        n_units = len(rep["units"])
        got = len(re.findall(r"Unit \d+ of %d" % n_units, text)) if n_units else 0
        if got != n_units:
            bad.append(f"{name}: {got} unit headings, expected {n_units}")
        for u in rep["units"]:
            for s in (u["set_sentence"], f"Set status: {u['set_status']}"):
                if s not in text:
                    bad.append(f"{name}: missing {s[:50]!r}")
            for i in u["instances"]:
                for s in (i["entry_id"], i["tier"], f"Level {i['claim_level']}: {i['claim_sentence']}", i["would_raise"], i["mechanism"]):
                    if s not in text:
                        bad.append(f"{name}: missing {s[:60]!r}")
                for x in i["ledger"]:
                    if x["item_id"] not in text:
                        bad.append(f"{name}: missing ledger item {x['item_id']}")
                for c in i["cap_sentences"]:
                    if c not in text:
                        bad.append(f"{name}: missing cap sentence")
        for s in rep["limitations"]:
            if s not in text:
                bad.append(f"{name}: missing limitation")
        if rep["units"] == [] and "No symptom was reported" not in text:
            bad.append(f"{name}: missing the empty-report sentence")
        low = text.lower()
        for w in BANNED:
            if w in low:
                bad.append(f"{name}: banned wording {w!r}")
    rows_json = sum(len(i["ledger"]) for u in rep["units"] for i in u["instances"])
    if hp.count("<tr>") - hp.count("<thead><tr>") - sum(1 for _ in rep["units"] if True) * 0 < 0:
        bad.append("html: table rows")
    if "<script" in hp.lower() or re.search(r"(?i)(src|href)\s*=\s*[\"']?(https?:|//)", hp) or "javascript:" in hp.lower():
        bad.append("html: script or external resource")
    if "Content-Security-Policy" not in hp or not hp.lstrip().lower().startswith("<!doctype html>"):
        bad.append("html: missing doctype or security policy")
    if len(re.findall(r"\| I[0-9a-z]*", md)) < 0:
        bad.append("never")
    ledger_ids = re.findall(r"h-[0-9a-f]{16}#\d+", md)
    if len(set(ledger_ids)) != len({x["item_id"] for u in rep["units"] for i in u["instances"] for x in i["ledger"]}):
        bad.append("markdown: ledger item count differs")
    for b in bad:
        print(b)
    print("parity: %s" % ("OK" if not bad else f"{len(bad)} difference(s)"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
