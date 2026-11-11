"""Report primitives of M3d (Step 1): strict base model, the three statement kinds, interval model, canonical JSON, and the
walkers used by the wording / no-score / no-float checks. Record models (Symptom, Link, Chain, ...) are added in later steps on this base."""
from __future__ import annotations

import json
import re
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, field_validator, model_validator

from .intervals import Interval
from .vocabulary import ORDER_RELATIONS, RULE_IDS, find_forbidden, forbidden_key_words


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=False)


class IntervalModel(Strict):
    domain: StrictStr
    lower_ns: StrictInt
    upper_ns: StrictInt

    @model_validator(mode="after")
    def _ordered(self) -> "IntervalModel":
        if not self.domain:
            raise ValueError("an interval needs a clock domain")
        if self.lower_ns > self.upper_ns:
            raise ValueError("interval lower bound exceeds upper bound")
        return self

    def to_interval(self) -> Interval:
        return Interval(self.domain, self.lower_ns, self.upper_ns)

    @classmethod
    def of(cls, iv: Interval) -> "IntervalModel":
        return cls(domain=iv.domain, lower_ns=iv.lower_ns, upper_ns=iv.upper_ns)


_SHA = re.compile(r"[0-9a-f]{64}")


class ObservedStatement(Strict):
    """Comes from a finding of M3c or M2; the id is the source finding's own id."""
    kind: Literal["observed"] = "observed"
    source_kind: Literal["m3c_finding", "m2_finding"]
    source_id: StrictStr


class DerivedOrderStatement(Strict):
    """A deterministic comparison of two observed intervals: the rule id and both intervals are shown."""
    kind: Literal["derived_order"] = "derived_order"
    rule_id: StrictStr
    earlier: IntervalModel
    later: IntervalModel
    relation: StrictStr

    @field_validator("rule_id")
    @classmethod
    def _rule(cls, v: str) -> str:
        if v not in RULE_IDS:
            raise ValueError(f"rule_id must be one of {RULE_IDS}")
        return v

    @field_validator("relation")
    @classmethod
    def _rel(cls, v: str) -> str:
        if v not in ORDER_RELATIONS:
            raise ValueError(f"relation must be one of {ORDER_RELATIONS}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "DerivedOrderStatement":
        if self.earlier.domain != self.later.domain:
            raise ValueError("a derived order compares intervals of one clock domain only")
        if self.relation == "before" and not self.earlier.to_interval().precedes(self.later.to_interval()):
            raise ValueError("relation 'before' requires earlier.upper_ns < later.lower_ns")
        if self.relation == "unordered" and self.earlier.to_interval().order(self.later.to_interval()) != "unordered":
            raise ValueError("relation 'unordered' requires non-strictly-ordered intervals")
        return self


class DeclaredStatement(Strict):
    """Comes from the dependency file; never presented as observed."""
    kind: Literal["declared"] = "declared"
    dependencies_sha256: StrictStr
    semantic_hash: StrictStr
    edge_id: StrictStr
    declared_by: StrictStr

    @field_validator("dependencies_sha256", "semantic_hash")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _SHA.fullmatch(v):
            raise ValueError("a digest must be 64 lowercase hex characters")
        return v

    @field_validator("edge_id", "declared_by")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v


Statement = ObservedStatement | DerivedOrderStatement | DeclaredStatement


# ---------------------------------------------------------------- canonical JSON and walkers

def walk(obj: Any, path: str = "$") -> Iterator[tuple[str, Any, str | None]]:
    """Iterative walk -> (path, value, key-name-or-None) for every node (no recursion: reports may be deep)."""
    stack: list[tuple[str, Any, str | None]] = [(path, obj, None)]
    while stack:
        p, v, k = stack.pop()
        yield p, v, k
        if isinstance(v, dict):
            for kk in sorted(v, reverse=True):
                stack.append((f"{p}.{kk}", v[kk], str(kk)))
        elif isinstance(v, (list, tuple)):
            for i in range(len(v) - 1, -1, -1):
                stack.append((f"{p}[{i}]", v[i], None))


def find_floats(obj: Any) -> list[str]:
    return [p for p, v, _ in walk(obj) if isinstance(v, float)]


def find_forbidden_keys(obj: Any) -> list[str]:
    """Paths whose KEY names a score-like or causal concept (confidence, probability, score, rank, root_cause, likelihood, weight)."""
    return [p for p, _v, k in walk(obj) if k is not None and forbidden_key_words(k)]


def find_forbidden_values(obj: Any, *, skip_keys: frozenset[str] = frozenset()) -> list[tuple[str, list[str]]]:
    """Forbidden wording in string VALUES, excluding fields that carry inert data (identity text, observed values) named in `skip_keys`."""
    out = []
    for p, v, k in walk(obj):
        if isinstance(v, str):
            last = p.rsplit(".", 1)[-1].split("[", 1)[0]
            if last in skip_keys:
                continue
            bad = find_forbidden(v)
            if bad:
                out.append((p, bad))
    return out


def canonical_json(obj: Any) -> str:
    """Compact, key-sorted JSON; integers and strings only (a float anywhere is an error)."""
    fl = find_floats(obj)
    if fl:
        raise TypeError(f"floats are not allowed in M3d output (at {fl[0]})")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def report_json(model: BaseModel) -> str:
    """Deterministic JSON of a model: sorted keys, 2-space indent, absent optionals omitted, trailing newline, no floats."""
    data = model.model_dump(mode="json", exclude_none=True)
    fl = find_floats(data)
    if fl:
        raise TypeError(f"floats are not allowed in M3d output (at {fl[0]})")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
