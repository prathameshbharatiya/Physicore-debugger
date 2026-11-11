"""The registry of fixed sentences of M3e. Every generated sentence of the Markdown report and of the HTML page is one of these; {name} marks the
only places where cleaned and escaped data may appear. The wording check of M3d (no cause, probability, score or rank language) is applied to all
of them by the tests."""
from __future__ import annotations

from physicore_collector.localization.vocabulary import SCOPE_SENTENCE, SEVERITY_DISCLAIMER

T = {
    "title": "Investigation report",
    "subtitle": "View of a localization report (schema {schema}, tool version {tool}). This view adds no analysis: every statement below is in the report.",
    "scope": "Scope: {sentence}",
    "severity": "Severity: {sentence}",
    "order_note": "Order is not a statement about what produced a symptom: an arrow or a label describes the receipt order of recorded messages and the declared dependency, nothing more.",
    "exit": "Exit code {code}: {reasons}.",
    "problem": "Input problem ({source}) at {path}: {message}",
    "counts": "Counts: {symptoms} symptom(s), {links} link(s) reported of {links_total}, {components} component(s), {chains} chain(s) reported, {blockers} blocker(s), {caveats} caveat(s), {refs} evidence reference(s).",
    "caps": "At most {cap} rows are listed per table and at most {items} items per cell; every omitted row or item is counted where it is cut and is present in the JSON report.",
    "no_deps": "No dependencies were declared: this report gives symptoms, lanes, layers and earliest symptoms only; it has no links, chains or hypotheses.",
    "no_symptoms": "No symptom was observed by the detectors that ran. This is a statement about those detectors, not about the system.",
    "none": "None.",
    "more": "{n} more {what} not listed here; the JSON report lists all of them.",
    "fd_lead": "These symptoms come from the expectation contract: each is the earliest report, per lane, that an expectation was not met. A divergence from an expectation is not a statement about what produced it.",
    "fd_none": "No symptom from an expectation contract is present in this report.",
    "lane_head": "Lane {n} of {total}",
    "lane_domain": "Clock domain: {domain}. Symptoms: {count}. Layers: {layers}. Axis start: {start}.",
    "lane_axis": "This lane has its own time axis; it is never compared with another lane.",
    "lane_statement": "Statement for this lane: {sentence}",
    "lane_earliest": "Earliest symptom(s) in this lane: {ids}.",
    "lane_blockers": "Blockers recorded for this lane: {ids}.",
    "cross_lane": "Across lanes: {sentence}",
    "label_head": "What each link label means (the label is about receipt order and the declared window only):",
    "label_item": "{label}: {meaning}",
    "link_note": "Notes on links: declared_edge_unverified means the dependency was declared by a person and cannot be verified by this tool; path_via_unobserved_nodes means the declared path passes through nodes that had no symptom between the two.",
    "hyp_lead": "The statements below are not ranked and carry no score or probability. Each says what the declared dependencies and the observations do or do not exclude, and what further evidence would discriminate.",
    "chains_exact": "Chains: {n} in total (exact).",
    "chains_unknown": "Chains: the total was not computed within the count limit (bounded_unknown); at least {n} exist.",
    "chains_trunc": "Chains listed: {n}; the list is incomplete ({why}).",
    "loc_lead": "Localization states where symptoms were observed; it does not state where a fault is.",
    "undeclared": "Pairs of symptoms with no declared path in either direction (counted per lane): {n}. Pairs with a declared path but no common lane (not compared): {m}.",
    "pairs": "Declared path pairs related: {related}; examined: {examined}; not examined: {not_examined}.",
    "links_status": "Links reported: {n} of {total}; suppressed: {suppressed}; status: {status}.",
    "m2": "Expectation investigation status: {status}.",
    "deps_none": "Dependencies: none declared.",
    "deps_declared": "Dependencies: declared ({edges} edge(s)); file digest {sha}; semantic hash {sem}.",
    "ver_lead": "Verification against the recording at {path} was performed on this run: {verified} verified, {mismatch} mismatch, {not_checked} not checked. Verified means the digest and the message position, topic and log time in the recording agree with the reference.",
    "ver_none": "No verification against the recording was requested; digests and references are shown as reported.",
    "prov_report": "Report file: SHA-256 {sha}, {size} byte(s).",
    "prov_params": "Parameters hash: {hash}.",
    "prov_inputs": "Inputs: detector configuration {cfg}; expectations {exp}.",
    "limit_item": "Limit {name}: {value}.",
    "limitation": "Limitation {n}: {text}",
    "prov_artifact": "- Evidence artifact {ref}: SHA-256 {sha}.",
    "footer": "End of report. Real-recording validation and decoder licensing clearance are outstanding; see the limitations.",
}

LABEL_MEANING = {
    "consistent_with_propagation": "the recorder received the symptom on the upstream node strictly before the symptom on the downstream node, within the declared window.",
    "outside_declared_window": "the receipt order follows the declared direction, but the separation is outside the declared window.",
    "inconsistent_order": "the symptom on the downstream node was received strictly before the symptom on the upstream node.",
    "undetermined": "the recorded intervals do not allow the order or the window check to be decided (they touch or overlap).",
}

COLS = {
    "fd": ["Symptom", "Lane", "Interval", "Offset", "Basis", "Rule", "Topic"],
    "lane": ["Symptom", "Layer", "Interval", "Offset from lane start", "Basis", "Topic", "Severity as reported"],
    "symptom": ["Symptom", "Source", "Detector or rule", "Category", "Topic", "Node", "Onset interval", "Basis", "Severity as reported", "Source coverage",
                "Truncated", "Identities", "Notes", "Evidence"],
    "link": ["Link", "Lane", "From", "To", "Edges", "Label", "Separation", "Window", "Notes"],
    "via": ["Link", "Node", "Symptoms between", "Coverage", "Detectors"],
    "chain": ["Chain", "Lane", "Head", "Symptoms in order", "Nodes", "Defence", "Blockers"],
    "component": ["Component", "Lanes", "Symptoms", "Links", "Defence", "Blockers"],
    "loc": ["Component", "Lane", "First-layer nodes", "Last-layer nodes"],
    "unloc": ["Symptom", "Topic", "Node"],
    "topic": ["Lane", "Topic", "Layer", "Symptoms"],
    "lhyp": ["Link", "Template", "Status", "Statement"],
    "chyp": ["Chain", "Template", "Status", "Statement", "Declared ancestors not on the chain", "Evidence that would discriminate"],
    "blocker": ["Blocker", "Code", "Detail", "Detector", "Topic", "Lane", "Count", "Source", "Artifacts", "Observations"],
    "caveat": ["Code", "Detail", "Detector"],
    "edge": ["Edge", "From", "To", "Symptoms on first", "Symptoms on second", "Status"],
    "det": ["Detector", "Status"],
    "evidence": ["Ref", "Artifact", "Message", "Topic", "Time", "Clock domain", "SHA-256", "Cited by", "Verification"],
}

TABLE_TITLES = {
    "fd": "Symptoms from the expectation contract", "lane": "Symptoms in this lane in order of onset", "symptom": "All symptoms", "link": "Links between symptoms",
    "via": "Intermediate nodes on declared paths", "chain": "Chains", "component": "Components", "loc": "Localization by component and lane",
    "unloc": "Symptoms on nodes without a declared edge", "topic": "Symptoms by topic and layer", "lhyp": "Declared-edge statements per link",
    "chyp": "Statements per chain", "blocker": "Blockers", "caveat": "Caveats", "edge": "Coverage of declared edges", "det": "Detector status",
    "evidence": "Evidence references",
}
LEGEND = {"title": "Timeline legend", "bar": "A bar is the onset interval of one symptom on this lane's own axis; a bar of minimum width is a single instant.",
          "arrow": "An arrow joins two symptoms along a declared edge, from the one received earlier to the one received later; its line style shows the link label. It states receipt order and the declared window, nothing more.",
          "axis": "Axis labels are offsets in milliseconds from the lane's first onset, computed with integers."}

HEADINGS = ["Summary", "First divergence", "Lanes and earliest symptoms", "Symptoms", "Links", "Chains and components", "Hypotheses",
            "Blockers and caveats", "Coverage", "Limits and limitations", "Evidence references", "Provenance"]

FIXED_SENTENCES_FROM_VOCABULARY = (SCOPE_SENTENCE, SEVERITY_DISCLAIMER)
