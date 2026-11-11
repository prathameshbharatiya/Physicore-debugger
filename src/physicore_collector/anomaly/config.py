"""Anomaly configuration (DESIGN 5). A separate file from the Phase 2 contract (that model is strict and accepted).

Rules (DESIGN 5.3): unknown keys are rejected at every level; every required parameter of an ENABLED detector is mandatory and has no
default; `rate_change.low_ratio` / `high_ratio` are individually optional but at least one is required; ratios are decimal strings
parsed to exact rationals; any violation is an invalid configuration (never a silent skip, never a default). Loading never raises for a
bad file: the problems come back in `LoadedConfig.problems` (same pattern as the accepted `load_contract`)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from .findings import DETECTOR_IDS, canonical_json

CONFIG_VERSION = 1
_DEC = re.compile(r"\d+(\.\d+)?")


@dataclass(frozen=True)
class ConfigProblem:
    path: str
    message: str


@dataclass(frozen=True)
class Limits:
    max_retained_observations: int
    max_text_bytes: int
    max_findings_per_detector: int


@dataclass(frozen=True)
class AnomalyConfig:
    limits: Limits
    detectors: dict[str, dict[str, Any]]     # normalised parameters of every ENABLED detector, by detector id
    disabled: tuple[str, ...]                # detector ids present with `enabled: false`
    config_hash: str

    def enabled(self, detector_id: str) -> bool:
        return detector_id in self.detectors

    def parameters(self, detector_id: str) -> dict[str, Any]:
        return self.detectors[detector_id]


@dataclass
class LoadedConfig:
    config: AnomalyConfig | None
    problems: list[ConfigProblem] = field(default_factory=list)


# ---------------------------------------------------------------- small strict validators (each appends to `problems`)

def _is_int(v: Any) -> bool:
    return type(v) is int  # bool is rejected


class _V:
    def __init__(self) -> None:
        self.problems: list[ConfigProblem] = []

    def bad(self, path: str, msg: str) -> None:
        self.problems.append(ConfigProblem(path, msg))

    def mapping(self, v: Any, path: str) -> dict | None:
        if not isinstance(v, dict):
            self.bad(path, "must be a mapping")
            return None
        for k in v:
            if not isinstance(k, str):
                self.bad(path, f"key {k!r} must be a string")
        return v

    def unknown(self, m: dict, allowed: set[str], path: str, *, late_hint: bool = False) -> None:
        for k in sorted(str(k) for k in m if k not in allowed):
            if k == "tierB.late_message" and late_hint:
                self.bad(f"{path}.{k}", "unknown key: late-message detection is deferred and not part of M3c")
            else:
                self.bad(f"{path}.{k}" if path else k, "unknown key (unknown configuration keys are rejected)")

    def pos_int(self, m: dict, key: str, path: str, *, minimum: int = 1, required: bool = True) -> int | None:
        if key not in m:
            if required:
                self.bad(f"{path}.{key}", "required parameter is missing (no default exists)")
            return None
        v = m[key]
        if not _is_int(v):
            self.bad(f"{path}.{key}", "must be an integer (not a boolean, float or string)")
            return None
        if v < minimum:
            self.bad(f"{path}.{key}", f"must be an integer >= {minimum}")
            return None
        return v

    def ratio(self, m: dict, key: str, path: str, *, required: bool, maximum: Fraction | None = None) -> Fraction | None:
        if key not in m:
            if required:
                self.bad(f"{path}.{key}", "required parameter is missing (no default exists)")
            return None
        v = m[key]
        if not isinstance(v, str) or not _DEC.fullmatch(v):
            self.bad(f"{path}.{key}", "must be a positive decimal string such as \"1.5\" (numbers, null and empty strings are invalid; omit the key instead)")
            return None
        f = Fraction(v)
        if f <= 0:
            self.bad(f"{path}.{key}", "must be greater than 0")
            return None
        if maximum is not None and f > maximum:
            self.bad(f"{path}.{key}", f"must be <= {maximum}")
            return None
        return f

    def str_list(self, m: dict, key: str, path: str, *, required: bool, nonempty: bool = True) -> list[str] | None:
        if key not in m:
            if required:
                self.bad(f"{path}.{key}", "required parameter is missing (no default exists)")
            return None
        v = m[key]
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            self.bad(f"{path}.{key}", "must be a list of non-empty strings")
            return None
        if nonempty and not v:
            self.bad(f"{path}.{key}", "must not be empty")
            return None
        if len(set(v)) != len(v):
            self.bad(f"{path}.{key}", "must not contain duplicates")
            return None
        return list(v)


def _fr(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}"


# ---------------------------------------------------------------- per-detector validation

def _tier_a_topics(v: _V, m: dict, path: str) -> Any:
    if "topics" not in m:
        return "auto"
    t = m["topics"]
    if t == "auto":
        return "auto"
    return v.str_list(m, "topics", path, required=True)


def _val_ros_log(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "topics"}, p)
    return {"topics": _tier_a_topics(v, m, p)}


def _val_diag(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "topics", "absent_elements_break_run_after"}, p)
    out: dict[str, Any] = {"topics": _tier_a_topics(v, m, p)}
    k = v.pos_int(m, "absent_elements_break_run_after", p, required=False)
    if k is not None:
        out["absent_elements_break_run_after"] = k
    return out


def _val_goal(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "topics"}, p)
    return {"topics": _tier_a_topics(v, m, p)}


def _val_battery(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "topics", "health_flags", "thresholds"}, p)
    out: dict[str, Any] = {"topics": _tier_a_topics(v, m, p)}
    hf = m.get("health_flags", True)
    if not isinstance(hf, bool):
        v.bad(f"{p}.health_flags", "must be a boolean")
        hf = True
    out["health_flags"] = hf
    if "thresholds" in m:
        t = v.mapping(m["thresholds"], f"{p}.thresholds")
        if t is not None:
            tp = f"{p}.thresholds"
            v.unknown(t, {"percentage_below", "percentage_scale", "voltage_below"}, tp)
            th: dict[str, Any] = {}
            pb = v.ratio(t, "percentage_below", tp, required=False)
            if "percentage_scale" in t:
                if t["percentage_scale"] not in ("fraction", "percent"):
                    v.bad(f"{tp}.percentage_scale", "must be \"fraction\" or \"percent\"")
                else:
                    th["percentage_scale"] = t["percentage_scale"]
            if pb is not None:
                if "percentage_scale" not in t:
                    v.bad(f"{tp}.percentage_scale", "required when percentage_below is configured (the scale of BatteryState.percentage is not assumed)")
                th["percentage_below"] = _fr(pb)
            elif "percentage_scale" in t:
                v.bad(f"{tp}.percentage_scale", "given without percentage_below; remove it or configure the threshold")
            vb = v.ratio(t, "voltage_below", tp, required=False)
            if vb is not None:
                th["voltage_below"] = _fr(vb)
            out["thresholds"] = th
    return out


def _val_gap(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "topics", "min_intervals", "multiplier", "absolute_floor_ns"}, p)
    out: dict[str, Any] = {"topics": v.str_list(m, "topics", p, required=True)}
    out["min_intervals"] = v.pos_int(m, "min_intervals", p)
    mult = v.ratio(m, "multiplier", p, required=True)
    out["multiplier"] = _fr(mult) if mult is not None else None
    out["absolute_floor_ns"] = v.pos_int(m, "absolute_floor_ns", p)
    return out


def _val_rate(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "topics", "window_ns", "min_windows", "min_baseline_count", "trim_start_windows", "trim_end_windows",
                  "low_ratio", "high_ratio"}, p)
    out: dict[str, Any] = {"topics": v.str_list(m, "topics", p, required=True)}
    for k in ("window_ns", "min_windows", "min_baseline_count"):
        out[k] = v.pos_int(m, k, p)
    for k in ("trim_start_windows", "trim_end_windows"):
        out[k] = v.pos_int(m, k, p, minimum=0)   # 0 is an explicit, recorded choice
    lo = v.ratio(m, "low_ratio", p, required=False)
    hi = v.ratio(m, "high_ratio", p, required=False)
    if "low_ratio" not in m and "high_ratio" not in m:
        v.bad(p, "at least one of low_ratio / high_ratio is required (an omitted direction is disabled; both omitted is invalid)")
    if lo is not None:
        out["low_ratio"] = _fr(lo)
    if hi is not None:
        out["high_ratio"] = _fr(hi)
    return out


def _val_stamp(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "topics", "fields", "partition_by"}, p)
    out: dict[str, Any] = {"topics": v.str_list(m, "topics", p, required=True), "fields": v.str_list(m, "fields", p, required=True)}
    pb = v.str_list(m, "partition_by", p, required=False, nonempty=False)
    out["partition_by"] = pb if pb is not None else []
    return out


def _val_array(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "rules"}, p)
    rules = m.get("rules")
    if "rules" not in m:
        v.bad(f"{p}.rules", "required parameter is missing (no expected length is ever invented)")
        return {"rules": []}
    if not isinstance(rules, list) or not rules:
        v.bad(f"{p}.rules", "must be a non-empty list of rules")
        return {"rules": []}
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rules):
        rp = f"{p}.rules[{i}]"
        rm = v.mapping(r, rp)
        if rm is None:
            continue
        v.unknown(rm, {"topic", "path", "expected_length", "baseline", "min_observations", "min_share"}, rp)
        topic = rm.get("topic"); path = rm.get("path")
        if not isinstance(topic, str) or not topic:
            v.bad(f"{rp}.topic", "required non-empty string")
        if not isinstance(path, str) or not path:
            v.bad(f"{rp}.path", "required non-empty string")
        has_exp = "expected_length" in rm
        has_base = "baseline" in rm
        item: dict[str, Any] = {"topic": topic, "path": path}
        if has_exp == has_base:
            v.bad(rp, "exactly one of expected_length or a complete baseline block (baseline: mode, min_observations, min_share) is required")
        elif has_exp:
            e = v.pos_int(rm, "expected_length", rp, minimum=0)
            for extra in ("min_observations", "min_share"):
                if extra in rm:
                    v.bad(f"{rp}.{extra}", "only valid with baseline: mode")
            item["expected_length"] = e
        else:
            if rm["baseline"] != "mode":
                v.bad(f"{rp}.baseline", "only \"mode\" is supported")
            item["baseline"] = "mode"
            item["min_observations"] = v.pos_int(rm, "min_observations", rp)
            sh = v.ratio(rm, "min_share", rp, required=True, maximum=Fraction(1))
            item["min_share"] = _fr(sh) if sh is not None else None
        out.append(item)
    return {"rules": out}


def _val_nonfinite(v: _V, m: dict, p: str) -> dict:
    v.unknown(m, {"enabled", "topics", "include_paths", "exclude_paths"}, p)
    out: dict[str, Any] = {"topics": v.str_list(m, "topics", p, required=True)}
    for k in ("include_paths", "exclude_paths"):
        lst = v.str_list(m, k, p, required=False, nonempty=False)
        out[k] = lst if lst is not None else []
    return out


_VALIDATORS = {
    "tierA.ros_log": _val_ros_log, "tierA.diagnostics": _val_diag, "tierA.goal_status": _val_goal, "tierA.battery": _val_battery,
    "tierB.publishing_gap": _val_gap, "tierB.rate_change": _val_rate, "tierB.stamp_order": _val_stamp,
    "tierB.array_length": _val_array, "tierB.non_finite": _val_nonfinite,
}
assert set(_VALIDATORS) == set(DETECTOR_IDS)


# ---------------------------------------------------------------- public API

def validate_config(raw: Any) -> LoadedConfig:
    """Validate an already-parsed document. Never raises."""
    v = _V()
    top = v.mapping(raw, "$")
    if top is None:
        return LoadedConfig(None, v.problems)
    v.unknown(top, {"anomaly_config_version", "limits", "detectors"}, "")
    ver = top.get("anomaly_config_version")
    if not _is_int(ver) or ver != CONFIG_VERSION:
        v.bad("anomaly_config_version", f"must be the integer {CONFIG_VERSION}")
    limits = None
    lm = v.mapping(top.get("limits"), "limits") if "limits" in top else (v.bad("limits", "required section is missing (no defaults exist)") or None)
    if lm is not None:
        v.unknown(lm, {"max_retained_observations", "max_text_bytes", "max_findings_per_detector"}, "limits")
        vals = [v.pos_int(lm, k, "limits") for k in ("max_retained_observations", "max_text_bytes", "max_findings_per_detector")]
        if all(x is not None for x in vals):
            limits = Limits(*vals)  # type: ignore[arg-type]
    detectors: dict[str, dict[str, Any]] = {}
    disabled: list[str] = []
    dm = v.mapping(top.get("detectors"), "detectors") if "detectors" in top else (v.bad("detectors", "required section is missing") or None)
    if dm is not None:
        for did in sorted(str(k) for k in dm):
            if did not in _VALIDATORS:
                if did == "tierB.late_message":
                    v.bad(f"detectors.{did}", "unknown key: late-message detection is deferred and not part of M3c")
                else:
                    v.bad(f"detectors.{did}", "unknown detector id (unknown configuration keys are rejected)")
                continue
            block = v.mapping(dm[did], f"detectors.{did}")
            if block is None:
                continue
            en = block.get("enabled", True)
            if not isinstance(en, bool):
                v.bad(f"detectors.{did}.enabled", "must be a boolean")
                continue
            if not en:
                probe = _V()   # a disabled detector needs no parameters, but unknown keys are still rejected
                _VALIDATORS[did](probe, block, f"detectors.{did}")
                v.problems.extend(p for p in probe.problems if p.message.startswith("unknown key"))
                disabled.append(did)
                continue
            detectors[did] = _VALIDATORS[did](v, block, f"detectors.{did}")
    if v.problems or limits is None:
        if not v.problems:
            v.bad("limits", "invalid")
        return LoadedConfig(None, v.problems)
    norm = {"anomaly_config_version": CONFIG_VERSION,
            "limits": {"max_retained_observations": limits.max_retained_observations, "max_text_bytes": limits.max_text_bytes,
                       "max_findings_per_detector": limits.max_findings_per_detector},
            "detectors": detectors, "disabled": sorted(disabled)}
    h = hashlib.sha256(canonical_json(norm).encode("utf-8")).hexdigest()
    return LoadedConfig(AnomalyConfig(limits, detectors, tuple(sorted(disabled)), h), [])


def load_config(path: str | Path) -> LoadedConfig:
    """Read a config file (.json, .yaml, .yml). Read-only; never raises for a bad or unreadable file."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return LoadedConfig(None, [ConfigProblem("$", f"cannot read the configuration file: {e}")])
    try:
        raw = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as e:
        return LoadedConfig(None, [ConfigProblem("$", f"not valid {'JSON' if p.suffix.lower() == '.json' else 'YAML'}: {e}")])
    return validate_config(raw)
