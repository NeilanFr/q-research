"""Append-only preregistration, availability, and promotion gates."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from quantlab.data import now_utc


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def read_ledger(path):
    rows, previous = [], "0" * 64
    if not Path(path).exists():
        return rows
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        payload = {k: v for k, v in row.items() if k != "sha256"}
        if row["previous"] != previous or fingerprint(payload) != row["sha256"]:
            raise ValueError("Ledger hash chain is broken")
        rows.append(row)
        previous = row["sha256"]
    return rows


@contextmanager
def exclusive(path):
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(now_utc())
    try:
        yield
    finally:
        lock.unlink()


def append_event(path, kind, payload):
    path = Path(path)
    with exclusive(path):
        rows = read_ledger(path)
        if kind == "registered":
            required = {"experiment_id", "hypothesis", "rationale", "feature_family", "parameters",
                        "parameter_search_space", "entry_rule", "exit_rule", "position_sizing",
                        "rebalance_frequency", "benchmark", "cost_assumptions", "train_period",
                        "validation_period", "holdout_status", "maximum_trials_allowed"}
            if required - payload.keys():
                raise ValueError(f"Missing preregistration fields: {required - payload.keys()}")
            if any(r["kind"] == kind and r["payload"]["experiment_id"] == payload["experiment_id"] for r in rows):
                raise ValueError("Experiment already registered; never rewrite its hypothesis")
        row = {"timestamp": now_utc(), "kind": kind, "payload": payload,
               "previous": rows[-1]["sha256"] if rows else "0" * 64}
        row["sha256"] = fingerprint(row)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(row) + "\n")
        return row


def require_available(records, decision_at):
    decision = pd.Timestamp(decision_at)
    if decision.tzinfo is None:
        raise ValueError("Timezone-aware decision required")
    for record in records:
        for name in ("source_timestamp", "observed_at", "known_at", "calculated_at"):
            t = pd.Timestamp(record[name])
            if t.tzinfo is None or t > decision:
                raise ValueError(f"Input {name} is not available at the decision")
        if pd.Timestamp(record["earliest_execution_at"]) <= decision:
            raise ValueError("Execution must follow the completed-bar decision")


def asof_records(records, decision_at):
    """Bitemporal membership, sector, and corporate-action facts; no backdating."""
    t = pd.Timestamp(decision_at)
    required = {"symbol", "effective_at", "known_at", "value"}
    if required - set(records):
        raise ValueError("Missing point-in-time metadata")
    frame = records.copy()
    for col in ("effective_at", "known_at"):
        frame[col] = pd.to_datetime(frame[col], utc=True)
    if t.tzinfo is None:
        raise ValueError("Timezone-aware as-of time required")
    frame = frame[(frame.effective_at <= t) & (frame.known_at <= t)]
    return frame.sort_values(["effective_at", "known_at"]).drop_duplicates("symbol", keep="last").set_index("symbol").value


PROMOTION_GATES = ("preregistered", "discovery", "validation", "execution_stress", "robustness",
                   "multiple_testing", "point_in_time_data", "no_leakage", "frozen", "prospective")


def promotion_allowed(evidence):
    return all(evidence.get(name) is True for name in PROMOTION_GATES)


def freeze_candidate(path, definition, evidence):
    """Historical passes permit freezing; future evidence is still required to promote."""
    required = set(PROMOTION_GATES) - {"frozen", "prospective"}
    if any(evidence.get(k) is not True for k in required):
        raise ValueError("Candidate cannot skip a research stage")
    keys = {"source_hash", "config_hash", "feature_definitions", "parameters", "universe",
            "execution_assumptions", "model_artifact", "selection_rules"}
    if keys - definition.keys():
        raise ValueError("Incomplete candidate freeze")
    value = {**definition, "candidate_id": fingerprint(definition), "frozen_at": now_utc()}
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
    return value
