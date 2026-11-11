"""Expectation contract (schema 1.0): what the robot was EXPECTED to do, supplied explicitly by a person or a spec.

The Investigator never derives an expectation from a recording. A contract is the smallest useful vocabulary:

  check kinds   range       a numeric signal stays within [min, max] (either bound optional, inclusive)
                tolerance   a numeric signal stays within |value - target| <= tolerance (inclusive)
                equals      a signal equals a value (string, boolean or number)
                transition  once a signal enters `from_value`, it must reach `to_value` within `deadline_ns`
                event_response  after a TRIGGER event (this expectation's signal observed entering a condition) a RESPONSE (a sample of another,
                            or the same, topic/signal satisfying a condition) must be received strictly later and within `deadline_ns`.
                            Both on mcap_log_time (recorder receipt time); never payload clocks
  applies_when  optional prerequisites on OTHER (or the same) signals: the latest earlier sample of each must satisfy it, and (optional
                `max_age_ns`) must not be older than that, otherwise the prerequisite is undecided (stale), never assumed
  signal paths  dotted keys; an all-digit step indexes a list (`position.0`); `field[key=value]` picks the element of an array at the
                position where a sibling array holds a value (`position[name=joint2]`, for JointState-style parallel arrays)
  time sources  log_time (the recorder's receipt time), payload_field (one integer-ns field), header_stamp (a builtin_interfaces/Time
                struct, default `header.stamp`, converted exactly as sec * 10^9 + nanosec; zero is a valid stamp). Each payload clock is
                its own domain, `payload_stamp:<topic>:<field>`; nothing converts between domains. Payload clocks apply to the single-signal
                checks only, not to event_response.
  window        inclusive [start_ns, end_ns] on one declared clock domain (either end optional)
  source        who/what asserted the expectation (required; never inferred)

Numbers are parsed as exact decimals (not binary floats), so a tolerance boundary is decided exactly: 0.1 + 0.2 style
rounding cannot move a value across a limit. This is not a rules language: there is no expression syntax, no arithmetic
across signals and no unit conversion (a value is compared in the units the message carries).
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import Field, StrictBool, StrictStr, ValidationError, field_validator, model_serializer, model_validator

from ..schemas import _Strict
from .cdr import signal_path_problem  # noqa: F401  (one grammar for contracts and for the readers)

CONTRACT_SCHEMA_VERSION = "1.0"
MAX_CONTRACT_BYTES = 1024 * 1024

LOG_TIME = "mcap_log_time"
_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"

Num = Annotated[Decimal, Field(strict=True)]  # a number must be written as a number, never as text
Scalar = Union[StrictBool, StrictStr, Num]  # bool is tried first so `true` never becomes the number 1


class Source(_Strict):
    """Provenance of the expectation itself."""

    kind: Literal["operator_supplied", "specification", "test_fixture"]
    reference: str = Field(min_length=1, max_length=500)  # e.g. a document id and section, a ticket, a fixture name
    description: str | None = Field(default=None, max_length=2000)


class Window(_Strict):
    """Inclusive bounds, in the expectation's own clock domain. Both ends optional (unbounded on that side)."""

    start_ns: int | None = Field(default=None, ge=0)
    end_ns: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "Window":
        if self.start_ns is not None and self.end_ns is not None and self.start_ns > self.end_ns:
            raise ValueError("window start_ns is after end_ns")
        return self


class Prerequisite(_Strict):
    """`applies_when` entry: the expectation is evaluated only while the latest EARLIER sample of `topic`.`signal` satisfies it."""

    topic: str = Field(min_length=1, max_length=500)
    signal: str
    op: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    value: Scalar
    # Freshness: the latest earlier sample must be at most this many ns older than the evaluated instant (inclusive), else the
    # prerequisite is undecided ("prerequisite_stale"). None = no limit (the Phase 2 behaviour, and absent from the canonical JSON).
    max_age_ns: Annotated[int, Field(strict=True, gt=0)] | None = None

    @field_validator("signal")
    @classmethod
    def _signal(cls, v: str) -> str:
        why = signal_path_problem(v)
        if why:
            raise ValueError(f"signal {why}")
        return v

    @model_validator(mode="after")
    def _ordering_ops_need_numbers(self) -> "Prerequisite":
        if self.op not in ("eq", "ne") and not isinstance(self.value, Decimal):
            raise ValueError(f"operator '{self.op}' needs a numeric value")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent_max_age(self, handler: Any) -> Any:  # a contract without max_age_ns keeps its Phase 2 canonical JSON (and sha256)
        d = handler(self)
        if d.get("max_age_ns") is None:
            d.pop("max_age_ns", None)
        return d


def domain_of(topic: str, time_source: str, time_field: str | None) -> str:
    """The clock domain a sample belongs to. One definition for the contract and the engine."""
    return "mcap_log_time" if time_source == "log_time" else f"payload_stamp:{topic}:{time_field}"


def _op_value_problem(op: str, value: Any) -> str | None:
    if op not in ("eq", "ne") and not isinstance(value, Decimal):
        return f"operator '{op}' needs a numeric value"
    return None


class ResponseSpec(_Strict):
    """The response condition of an event_response check. The response is always read on the recorder's receipt clock (mcap_log_time)."""

    topic: str = Field(min_length=1, max_length=500)
    signal: str
    op: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    value: Scalar

    @field_validator("signal")
    @classmethod
    def _signal(cls, v: str) -> str:
        why = signal_path_problem(v)
        if why:
            raise ValueError(f"signal {why}")
        return v

    @model_validator(mode="after")
    def _coherent(self) -> "ResponseSpec":
        why = _op_value_problem(self.op, self.value)
        if why:
            raise ValueError(why)
        return self


class RangeCheck(_Strict):
    kind: Literal["range"]
    min: Num | None = None
    max: Num | None = None

    @model_validator(mode="after")
    def _bounds(self) -> "RangeCheck":
        if self.min is None and self.max is None:
            raise ValueError("a range check needs at least one of min / max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("range min is greater than max")
        return self


class ToleranceCheck(_Strict):
    kind: Literal["tolerance"]
    target: Num
    tolerance: Annotated[Decimal, Field(strict=True, ge=0)]


class EqualsCheck(_Strict):
    kind: Literal["equals"]
    value: Scalar


class TransitionCheck(_Strict):
    kind: Literal["transition"]
    from_value: Scalar
    to_value: Scalar
    deadline_ns: int = Field(gt=0)

    @model_validator(mode="after")
    def _differ(self) -> "TransitionCheck":
        if type(self.from_value) is type(self.to_value) and self.from_value == self.to_value:
            raise ValueError("a transition needs from_value different from to_value")
        return self


class EventResponseCheck(_Strict):
    """The expectation's own topic/signal is the TRIGGER stream: an event is a sample satisfying (trigger_op, trigger_value) whose
    OBSERVED predecessor did not. The RESPONSE is the first sample of `response.topic`.`response.signal` satisfying (response.op,
    response.value) strictly later than the event and no later than `deadline_ns` after it.

    Both streams are on mcap_log_time: the recorder's RECEIPT time. The check measures what the recorder received and in which order; it
    says nothing about when the events physically occurred, and no payload clock takes part in it. No causal claim is made."""

    kind: Literal["event_response"]
    trigger_op: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    trigger_value: Scalar
    response: ResponseSpec
    deadline_ns: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def _coherent(self) -> "EventResponseCheck":
        why = _op_value_problem(self.trigger_op, self.trigger_value)
        if why:
            raise ValueError("trigger " + why)
        return self


Check = Annotated[Union[RangeCheck, ToleranceCheck, EqualsCheck, TransitionCheck, EventResponseCheck], Field(discriminator="kind")]


class Expectation(_Strict):
    expectation_id: str = Field(pattern=_ID)
    description: str = Field(min_length=1, max_length=2000)
    topic: str = Field(min_length=1, max_length=500)
    signal: str
    # Where the sample's time comes from. `log_time` = the MCAP log_time. `payload_field` = an integer-nanosecond field inside
    # the message, or `header_stamp` = a builtin_interfaces/Time struct (sec, nanosec; default field `header.stamp`): both are a DIFFERENT
    # clock from log_time, named `payload_stamp:<topic>:<time_field>`.
    time_source: Literal["log_time", "payload_field", "header_stamp"] = "log_time"
    time_field: str | None = None
    # The clock domain the window is written in. Must be the domain of the samples; if it is not, the expectation is reported
    # as incomparable_timebases (no offset is guessed). For log_time use `mcap_log_time`.
    clock_domain: str = Field(min_length=1, max_length=300)
    window: Window = Field(default_factory=Window)
    applies_when: list[Prerequisite] = Field(default_factory=list, max_length=8)
    check: Check
    source: Source

    @field_validator("signal")
    @classmethod
    def _signal(cls, v: str) -> str:
        why = signal_path_problem(v)
        if why:
            raise ValueError(f"signal {why}")
        return v

    @model_validator(mode="after")
    def _coherent(self) -> "Expectation":
        if self.time_source != "log_time":
            if self.time_source == "header_stamp" and self.time_field is None:
                self.time_field = "header.stamp"
            if self.time_field is None:
                raise ValueError("time_source 'payload_field' needs time_field")
            why = signal_path_problem(self.time_field)
            if why:
                raise ValueError(f"time_field {why}")
            if self.applies_when:
                raise ValueError("applies_when needs time_source 'log_time': prerequisites on other signals cannot be ordered against a payload clock")
        elif self.time_field is not None:
            raise ValueError("time_field is only allowed with a payload time_source ('payload_field' or 'header_stamp')")
        if isinstance(self.check, EventResponseCheck) and self.time_source != "log_time":
            raise ValueError("an event_response check needs time_source 'log_time': it compares recorder receipt order only, "
                             "and no payload clock (header.stamp or other) takes part in it")
        return self


class Contract(_Strict):
    contract_schema_version: Literal["1.0"]
    contract_id: str = Field(pattern=_ID)
    description: str | None = Field(default=None, max_length=2000)
    expectations: list[Expectation] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _unique_ids(self) -> "Contract":
        seen: set[str] = set()
        for e in self.expectations:
            if e.expectation_id in seen:
                raise ValueError(f"duplicate expectation_id '{e.expectation_id}'")
            seen.add(e.expectation_id)
        return self


class ContractProblem(_Strict):
    code: str
    message: str
    location: str | None = None  # e.g. "expectations[2].check.tolerance"


class LoadedContract(_Strict):
    contract: Contract | None
    sha256: str | None  # of the canonical (validated) contract; None when invalid
    problems: list[ContractProblem] = Field(default_factory=list)


# ---------------------------------------------------------------------------

def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in a contract")


def _decimalize(x: Any) -> Any:
    """YAML floats/ints -> exact Decimal; bool is left alone (it is not a number here)."""
    if isinstance(x, bool):
        return x
    if isinstance(x, float):
        d = Decimal(repr(x))
        if not d.is_finite():
            raise ValueError("NaN/Infinity is not allowed in a contract")
        return d
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, dict):
        return {k: _decimalize(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_decimalize(v) for v in x]
    return x


def _int_fields_back(raw: Any) -> Any:
    """Integer-valued fields (window bounds, deadline) must stay ints for the schema; numbers elsewhere stay Decimal."""
    if isinstance(raw, dict):
        out = {}
        for k, v in raw.items():
            if k in ("start_ns", "end_ns", "deadline_ns", "max_age_ns") and isinstance(v, Decimal) and v == v.to_integral_value():
                out[k] = int(v)
            else:
                out[k] = _int_fields_back(v)
        return out
    if isinstance(raw, list):
        return [_int_fields_back(v) for v in raw]
    return raw


def canonical_json(contract: Contract) -> str:
    return json.dumps(contract.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _problem(code: str, message: str, location: str | None = None) -> LoadedContract:
    return LoadedContract(contract=None, sha256=None, problems=[ContractProblem(code=code, message=message, location=location)])


def parse_contract(data: Any) -> LoadedContract:
    """Validate an already-parsed mapping. Never raises for bad content."""
    try:
        raw = _int_fields_back(_decimalize(data))
    except (ValueError, InvalidOperation) as e:
        return _problem("contract_invalid_number", str(e))
    if not isinstance(raw, dict):
        return _problem("contract_not_a_mapping", "the contract must be a JSON/YAML object")
    ver = raw.get("contract_schema_version")
    if ver != CONTRACT_SCHEMA_VERSION:
        return _problem("contract_schema_version_unsupported",
                        f"contract_schema_version must be '{CONTRACT_SCHEMA_VERSION}' (got {ver!r}); this tool does not guess other versions",
                        "contract_schema_version")
    try:
        c = Contract.model_validate(raw)
    except ValidationError as e:
        probs = [ContractProblem(code="contract_invalid", message=err["msg"], location=".".join(str(p) for p in err["loc"]) or None)
                 for err in e.errors(include_url=False)]
        probs.sort(key=lambda p: (p.location or "", p.message))
        return LoadedContract(contract=None, sha256=None, problems=probs[:50])
    return LoadedContract(contract=c, sha256=hashlib.sha256(canonical_json(c).encode()).hexdigest())


def load_contract(path: str | Path) -> LoadedContract:
    """Read a contract file (.json, .yaml or .yml). Read-only; never raises for a bad or unreadable file: the problem comes
    back in `problems` and the caller reports `invalid_expectation`."""
    p = Path(path)
    try:
        if p.is_symlink():
            return _problem("contract_unreadable", "the contract path is a symlink; it was not followed")
        if not p.is_file():
            return _problem("contract_unreadable", f"contract file not found or not a regular file: '{p.name}'")
        if p.stat().st_size > MAX_CONTRACT_BYTES:
            return _problem("contract_too_large", f"contract is larger than {MAX_CONTRACT_BYTES} bytes")
        raw = p.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _problem("contract_unreadable", "the contract is not valid UTF-8")
    except OSError as e:
        return _problem("contract_unreadable", f"the contract could not be read: {e.strerror or e}")
    try:
        if p.suffix.lower() in (".yaml", ".yml"):
            data = yaml.safe_load(text)
        else:
            data = json.loads(text, parse_float=Decimal, parse_int=Decimal, parse_constant=_no_constant)
    except (ValueError, yaml.YAMLError) as e:
        return _problem("contract_malformed", f"the contract could not be parsed: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
    return parse_contract(data)
