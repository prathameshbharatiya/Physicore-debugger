"""M4b: the catalogue as a review document. The text is generated from the catalogue, so it cannot drift from it (a test compares the two).

The document is for a human reviewer. Nothing in it says that an entry has been reviewed unless the entry itself carries a reviewer and a date.
"""
from __future__ import annotations

from .catalogue import Catalogue, builtin

HEADER = """# M4 hypothesis catalogue: review pack

Generated from the built-in catalogue (digest `{digest}`). Do not edit by hand; a test fails if this file differs from the catalogue.

Each entry is a candidate mechanism, written as one neutral sentence. An entry is **proposed** until a named reviewer signs it off in the catalogue itself
(`review.status: reviewed`, with `reviewed_by` and `reviewed_on`). Until then this tool presents it as a candidate whose review is pending.

What to check, for each entry: (1) the sentence says only what the mechanism is; (2) the predictions are things a recording could show if the mechanism were
at work; (3) the evidence it names is what would separate it from its rivals; (4) its rivals are the mechanisms it is most often confused with.

Summary: {n} entries; {p} proposed, {r} reviewed.
"""


def _row(label, items):
    return f"- {label}: " + ("; ".join(items) if items else "none") + "\n"


def render(cat: Catalogue | None = None) -> str:
    cat = cat or builtin()
    n = len(cat.entries)
    p = sum(1 for e in cat.entries if e["review"]["status"] == "proposed")
    r = sum(1 for e in cat.entries if e["review"]["status"] == "reviewed")
    out = [HEADER.format(digest=cat.digest, n=n, p=p, r=r)]
    for e in cat.entries:
        rv = e["review"]
        out.append(f"\n## `{e['id']}`  (version {e['version']}, family {e['family']})\n\n")
        out.append(f"{e['mechanism']}\n\n")
        out.append(f"- Review status: {rv['status']}" + (f" ({rv['note']})" if rv.get("note") else "") + "\n")
        if rv["status"] == "reviewed":
            out.append(f"- Reviewed by {rv['reviewed_by']} on {rv['reviewed_on']}\n")
        ap = e["applies_to"]
        out.append(_row("Applies to symptom categories", ap["symptom_categories"]))
        if ap["component_roles"]:
            out.append(_row("Only for component roles", ap["component_roles"]))
        out.append(_row("Slots", [f"{s['name']} = {s['selects']}" for s in e["slots"]]))
        out.append(_row("Predictions", [f"{q['id']}: {q['text']} (rule: {q['rule']['op']})" for q in e["predictions"]]))
        out.append(_row("Checks", [f"{k['id']}: {k['description']} (M2 {k['kind']}, constraint {k['constraint_kind']}, slot {k['slot']})" for k in e["checks"]]))
        out.append(_row("Evidence that would help", [f"{q['token']}: {q['description']} ({q['usual_availability'].replace('_', ' ')})" for q in e["evidence_requirements"]]))
        out.append(_row("Rivals", [f"`{x}`" for x in e["rivals"]]))
    return "".join(out)


__all__ = ["render"]
