"""Deterministic first-divergence Investigator (Phase 2). Separate from the Collector: it reads an evidence inventory and an
explicit expectation contract and writes nothing. See docs/INVESTIGATOR.md."""

from .contract import CONTRACT_SCHEMA_VERSION, Contract, LoadedContract, load_contract, parse_contract
from .engine import (
    INVESTIGATION_SCHEMA_VERSION, InvestigationResult, Status, exit_code, investigate, investigation_json, render_investigation,
)

__all__ = [
    "CONTRACT_SCHEMA_VERSION", "Contract", "LoadedContract", "load_contract", "parse_contract", "INVESTIGATION_SCHEMA_VERSION",
    "InvestigationResult", "Status", "exit_code", "investigate", "investigation_json", "render_investigation",
]
