"""Immutable prospective shadow records. This module has no broker transport.

Only candidates that passed the historical/data gates may be frozen. None of
the initial V2 survivor-panel experiments qualify. Synthetic tests exercise this
path without creating an actual candidate or broker order.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import numpy as np

from quantlab.data import calendar
from .governance import canonical, fingerprint, require_available
from .validation import block_inference


def verify_candidate(path):
    candidate = json.loads(Path(path).read_text())
    definition = {k: v for k, v in candidate.items() if k not in {"candidate_id", "frozen_at"}}
    if fingerprint(definition) != candidate["candidate_id"]:
        raise ValueError("Frozen candidate was modified; use a new candidate ID")
    return candidate


def verify_dependencies(candidate, dependencies):
    """Caller supplies observed hashes of the actual code/config/data definition."""
    for key in ("source_hash", "config_hash", "model_artifact"):
        if candidate[key] != dependencies.get(key):
            raise ValueError("Frozen dependency mismatch")


def issue(directory, candidate_path, forecast, dependencies, now):
    candidate = verify_candidate(candidate_path)
    verify_dependencies(candidate, dependencies)
    now = pd.Timestamp(now)
    if now.tzinfo is None or now < pd.Timestamp(candidate["frozen_at"]):
        raise ValueError("Forecast must be issued after the real candidate freeze")
    feature_session = pd.Timestamp(forecast["feature_session"])
    cal = calendar(str(feature_session.date()), str((feature_session+pd.Timedelta(days=20)).date()))
    if not cal.is_session(feature_session):
        raise ValueError("Feature date is not an exchange session")
    entry_session = cal.next_session(feature_session)
    schedule = candidate["selection_rules"]
    if not isinstance(schedule, dict) or not {"anchor_session", "cadence"}.issubset(schedule):
        raise ValueError("Frozen candidate requires an explicit rebalance schedule")
    anchor, cadence = pd.Timestamp(schedule["anchor_session"]), schedule["cadence"]
    if type(cadence) is not int or cadence < 1 or entry_session < anchor:
        raise ValueError("Invalid frozen rebalance schedule")
    schedule_cal = calendar(str(anchor.date()), str(entry_session.date()))
    if not schedule_cal.is_session(anchor) or (len(schedule_cal.sessions_in_range(anchor, entry_session))-1) % cadence:
        raise ValueError("Off-cadence shadow issuance")
    earliest = cal.session_open(entry_session)
    known = cal.session_close(feature_session)+pd.Timedelta(hours=1)
    if not known <= now < earliest:
        raise ValueError("Late/stale/incomplete forecast")
    if pd.Timestamp(forecast["entry_at"]) != earliest:
        raise ValueError("Forecast entry must be the next legitimate session")
    require_available(forecast["availability"], now)
    targets = forecast["targets"]
    if any(w < 0 or not np.isfinite(w) for w in targets.values()) or sum(targets.values()) > 1+1e-12:
        raise ValueError("Invalid unlevered shadow targets")
    if set(targets)-set(candidate["universe"]):
        raise ValueError("Candidate universe changed")
    key = candidate["candidate_id"]+"_"+entry_session.strftime("%Y%m%d")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"candidate_id": candidate["candidate_id"], "issued_at": now.isoformat(),
               "forecast": forecast, "mode": "SHADOW_ONLY", "actual_paper_fill": None}
    payload["sha256"] = fingerprint(payload)
    # Exclusive creation protects replay and duplicate candidate/session issuance.
    with (directory/(key+".json")).open("x", encoding="utf-8") as stream:
        stream.write(canonical(payload)+"\n")
    return key


def record_outcome(directory, key, observation, now):
    directory = Path(directory)
    forecast_path = directory/(key+".json")
    record = json.loads(forecast_path.read_text())
    body = {k: v for k, v in record.items() if k != "sha256"}
    if fingerprint(body) != record["sha256"]:
        raise ValueError("Forecast record was modified")
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Observed outcome requires timezone-aware clock")
    entry = pd.Timestamp(record["forecast"]["entry_at"])
    for time in ("fill_at", "mark_at", "observed_at"):
        t = pd.Timestamp(observation[time])
        if t.tzinfo is None or not entry <= t <= now:
            raise ValueError("Outcome cannot predate entry or come from the future")
    if not pd.Timestamp(observation["fill_at"]) <= pd.Timestamp(observation["mark_at"]) <= pd.Timestamp(observation["observed_at"]):
        raise ValueError("Outcome observation order invalid")
    if observation.get("mode") != "SHADOW_ONLY" or observation.get("actual_paper_fill") is not None:
        raise ValueError("Shadow records cannot claim actual broker fills")
    required = {"intended_orders", "simulated_fills", "slippage", "pnl", "drawdown", "attribution", "snapshot_sha256"}
    if required - observation.keys():
        raise ValueError("Incomplete prospective attribution")
    payload = {"forecast_sha256": record["sha256"], "observation": observation, "recorded_at": now.isoformat()}
    payload["sha256"] = fingerprint(payload)
    with (directory/(key+"_outcome.json")).open("x", encoding="utf-8") as stream:
        stream.write(canonical(payload)+"\n")
    return payload


def prospective_gate(daily, champion_daily, rules, now):
    """Minimum prospective evidence from matched, already-observed daily records.

    This only computes an evidence gate. It never promotes, rewrites a policy,
    arms a batch, or enables an account. Artifact verification remains required.
    """
    required = {"net_return", "observed_at", "rebalance", "closed_lots"}
    if required-set(daily) or "net_return" not in champion_daily or not daily.index.equals(champion_daily.index):
        raise ValueError("Matched prospective records required")
    if daily.empty or daily.index.has_duplicates or not daily.index.is_monotonic_increasing:
        raise ValueError("Unique chronological prospective sessions required")
    expected = calendar(str(daily.index[0].date()), str(daily.index[-1].date())).sessions_in_range(daily.index[0], daily.index[-1])
    if not daily.index.equals(expected):
        raise ValueError("Prospective history has missing sessions")
    observed = pd.to_datetime(daily.observed_at, utc=True)
    current = pd.Timestamp(now)
    if current.tzinfo is None or (observed > current).any():
        raise ValueError("Future prospective outcome")
    closes = calendar(str(daily.index[0].date()), str(daily.index[-1].date())).schedule.loc[daily.index, "close"]
    if (observed.to_numpy() < closes.to_numpy()).any():
        raise ValueError("Outcome observed before completed session")
    r, ref = daily.net_return, champion_daily.net_return
    if not np.isfinite(r).all() or not np.isfinite(ref).all() or (r <= -1).any() or (ref <= -1).any():
        raise ValueError("Invalid prospective returns")
    nav = (1+r).cumprod()
    drawdown = (nav/nav.cummax().clip(lower=1)-1).min()
    inference = block_inference((r-ref).to_numpy()) if len(r) >= 20 else None
    checks = {"sessions": len(r) >= rules["minimum_sessions"],
              "rebalances": int(daily.rebalance.sum()) >= rules["minimum_rebalances"],
              "closed_lots": int(daily.closed_lots.sum()) >= rules["minimum_closed_lots"],
              "economic_increment": float((r-ref).mean()*252) >= rules["minimum_annual_mean_excess"],
              "drawdown": drawdown >= rules["max_drawdown"],
              "positive_net": nav.iloc[-1] > 1,
              "bootstrap": inference is not None and inference["lower"][0] > 0}
    return {"passed": all(checks.values()), "checks": {k: bool(v) for k, v in checks.items()},
            "promotion": "Separate full-gate review required; no execution permission granted"}
