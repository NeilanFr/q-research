"""Immutable prospective predictions and separate paper-account record types."""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

from .core import make_signals, score_weights, simulate
from .data import ROOT, calendar, digest, latest_completed_session, load_snapshot, now_utc, save_json, wide
from .experiment import connect_db, event, source_archive, validate_config


def forward_tables(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS policies (
          version TEXT PRIMARY KEY, created_at TEXT NOT NULL, policy_json TEXT NOT NULL, reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS forecast_batches (
          id TEXT PRIMARY KEY, version TEXT NOT NULL, decision_date TEXT NOT NULL,
          issued_at TEXT NOT NULL, entry_at TEXT NOT NULL, exit_at TEXT NOT NULL,
          snapshot TEXT NOT NULL, path TEXT NOT NULL, UNIQUE(version,decision_date));
        CREATE TABLE IF NOT EXISTS predictions (
          batch_id TEXT NOT NULL, model TEXT NOT NULL, symbol TEXT NOT NULL, score REAL,
          PRIMARY KEY(batch_id,model,symbol));
        CREATE TABLE IF NOT EXISTS forecast_integrity (
          batch_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outcome_integrity (
          batch_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS position_intents (
          batch_id TEXT NOT NULL, model TEXT NOT NULL, symbol TEXT NOT NULL,
          weight REAL NOT NULL, dollars REAL NOT NULL, role TEXT NOT NULL,
          PRIMARY KEY(batch_id,model,symbol));
        CREATE TABLE IF NOT EXISTS submitted_orders (
          broker_order_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, account TEXT NOT NULL,
          submitted_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fills (
          execution_id TEXT PRIMARY KEY, broker_order_id TEXT NOT NULL,
          observed_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observed_positions (
          observation_id TEXT PRIMARY KEY, account TEXT NOT NULL,
          observed_at TEXT NOT NULL, payload TEXT NOT NULL);
    """)
    db.commit()


def forecast_times(asof: str, issued_at: str) -> dict:
    """Only fresh, genuinely prospective batches can enter the forward ledger."""
    now = pd.Timestamp(issued_at)
    date = pd.Timestamp(asof)
    if now.tzinfo is None or date != latest_completed_session(now):
        raise ValueError("Forward prediction requires the latest completed session and a UTC clock")
    cal = calendar((date - pd.Timedelta(days=10)).date().isoformat(),
                   (date + pd.Timedelta(days=20)).date().isoformat())
    entry_date = cal.next_session(date)
    exit_date = cal.next_session(entry_date)
    available = cal.session_close(date) + pd.Timedelta(hours=1)
    entry_at, exit_at = cal.session_open(entry_date), cal.session_open(exit_date)
    if not available <= now < entry_at - pd.Timedelta(minutes=2):
        raise ValueError("Prediction is late or bars incomplete; it cannot be labelled forward")
    return {"decision_date": str(date.date()), "feature_available_at": available.isoformat(),
            "entry_date": str(entry_date.date()), "exit_date": str(exit_date.date()),
            "entry_at": entry_at.isoformat(), "exit_at": exit_at.isoformat()}


def predict(policy_path: Path, snapshot: str | None = None) -> Path:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    cfg_path = (ROOT / policy["research_config"]).resolve()
    if not cfg_path.is_relative_to(ROOT):
        raise ValueError("Research config must be inside the repository")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    validate_config(cfg)
    if cfg.get("rebalance_every", 1) != 1:
        raise ValueError("Only daily forward policies are supported; cadence studies remain historical until a schedule is frozen")
    if policy["active_model"] not in cfg["models"] or not policy.get("reason", "").strip():
        raise ValueError("A policy needs a known active model and a reason")
    if policy.get("execution_enabled") is not False or policy["gross_exposure"] != 1:
        raise ValueError("This milestone has no transmission and only 1x long-only exposure")
    capital = float(policy["notional_capital"])
    if not np.isfinite(capital) or capital <= 0:
        raise ValueError("Invalid notional capital")
    bars, manifest, _ = load_snapshot(snapshot)
    issued_at = now_utc()
    asof = bars.date.max()
    times = forecast_times(str(asof.date()), issued_at)
    if pd.Timestamp(manifest["created_at_utc"]) > pd.Timestamp(issued_at):
        raise ValueError("Cannot use data acquired after issuance")
    if any(pd.Timestamp(s["fetched_at_utc"]) < pd.Timestamp(times["feature_available_at"]) for s in manifest["raw_sources"]):
        raise ValueError("Snapshot was fetched before all final bars were available")
    protocol = json.loads((ROOT / "config/protocol.json").read_text(encoding="utf-8"))
    if not protocol["competition_first_session"] <= times["entry_date"] <= protocol["competition_end_assumption"]:
        raise ValueError("Prediction lies outside configured competition dates; explicitly revise the protocol")
    close = wide(bars, "adj_close", cfg["symbols"])
    signals = make_signals(close, cfg)
    if any(asof not in score.index for score in signals.values()):
        raise ValueError("Latest features are not complete")
    raw_close = wide(bars, "close", cfg["symbols"] + [cfg["benchmark"]]).loc[asof]
    # Policy ID incorporates research configuration and source: a code/rule change
    # becomes an explicit new version, never a rewrite of previous predictions.
    staged = ROOT / "state/forward" / ("batch_" + uuid4().hex[:16])
    staged.mkdir(parents=True)
    source_hash = source_archive(staged / "source")
    model_sources = {name: digest((Path(__file__).parent / name).read_bytes())
                     for name in ["core.py", "data.py", "forward.py"]}
    frozen = {"policy": policy, "research_config": cfg,
              "model_source_sha256": digest(json.dumps(model_sources, sort_keys=True).encode())}
    version = digest(json.dumps(frozen, sort_keys=True).encode())[:20]
    rows = []
    for model in cfg["models"]:
        if model == "spy_buy_hold":
            scores, weights = {cfg["benchmark"]: None}, {cfg["benchmark"]: 1.0}
        else:
            score = signals[model].loc[[asof]]
            scores = score.iloc[0].to_dict()
            weights = score_weights(score, cfg["tilt"], cfg["max_weight"]).iloc[0].to_dict()
        for symbol, weight in weights.items():
            dollars = float(weight * capital)
            rows.append({"model": model, "symbol": symbol, "score": scores[symbol],
                         "target_weight": float(weight), "target_dollars": dollars,
                         "reference_close": float(raw_close[symbol]),
                         "indicative_whole_shares": int(dollars // raw_close[symbol]),
                         "role": "active" if model == policy["active_model"] else "shadow",
                         "rebalance": "initial_only" if model.endswith("buy_hold") else "daily"})
    risk = {"gross_exposure": 1.0, "net_exposure": 1.0,
            "method": "Trailing 60-session sample covariance; prior 20-session mean dollar volume. Reference positions, not submitted orders."}
    trailing = close.pct_change(fill_method=None).tail(60)
    correlations = trailing.corr().to_numpy()
    risk["mean_sector_pair_correlation"] = float(correlations[np.triu_indices(len(correlations), 1)].mean())
    selected = [r for r in rows if r["role"] == "active"]
    active_symbols = [r["symbol"] for r in selected]
    w = np.array([r["target_weight"] for r in selected])
    active_returns = wide(bars, "adj_close", active_symbols).pct_change(fill_method=None).tail(60)
    risk["active_annualized_volatility_estimate"] = float(np.sqrt(w @ (active_returns.cov().to_numpy() * 252) @ w))
    risk["active_max_target_weight"] = float(w.max())
    if "volume" in bars:
        dv = wide(bars.assign(dollar_volume=bars.close * bars.volume), "dollar_volume", active_symbols).tail(20).mean()
        risk["max_initial_notional_to_daily_dollar_volume"] = float(np.max(capital * w / dv.to_numpy()))
        risk["minimum_daily_dollar_volume"] = float(dv.min())
    else:
        risk["liquidity_status"] = "Unavailable; no liquidity claim can be made"
    # Timestamp after feature construction and code archiving, not before.
    issued_at = now_utc()
    times = forecast_times(str(asof.date()), issued_at)
    batch = {"id": staged.name, "version": version, "issued_at_utc": issued_at, **times,
             "snapshot": manifest["id"], "active_model": policy["active_model"],
             "source_sha256": source_hash, "capital": capital,
             "risk": risk,
             "execution_enabled": False, "price_note": "Latest raw close is a reference only; shares are not executable orders and ignore opening gap/fees.",
             "policy": frozen, "predictions": [{k: r[k] for k in ["model", "symbol", "score"]} for r in rows],
             "position_intents": [{k: v for k, v in r.items() if k != "score"} for r in rows]}
    db = connect_db()
    forward_tables(db)
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM forecast_batches WHERE version=? AND decision_date=?", (version, times["decision_date"])).fetchone():
            raise ValueError("This policy/session is already frozen; existing predictions cannot be replaced")
        # Do not silently change the active strategy during the contest. New
        # versions need a distinct policy name and an explicit rationale.
        old = db.execute("SELECT policy_json FROM policies ORDER BY created_at DESC LIMIT 1").fetchone()
        if old:
            previous = json.loads(old[0])
            previous_id = digest(json.dumps(previous, sort_keys=True).encode())[:20]
            if version != previous_id and previous["policy"]["name"] == policy["name"]:
                raise ValueError("Policy/code changed: use a new policy name and explain why before freezing predictions")
        db.execute("INSERT OR IGNORE INTO policies VALUES (?,?,?,?)", (version, issued_at, json.dumps(frozen, sort_keys=True), policy["reason"]))
        db.execute("INSERT INTO forecast_batches VALUES (?,?,?,?,?,?,?,?)", (batch["id"], version, times["decision_date"], issued_at, times["entry_at"], times["exit_at"], manifest["id"], str(staged.relative_to(ROOT))))
        for row in rows:
            db.execute("INSERT INTO predictions VALUES (?,?,?,?)", (batch["id"], row["model"], row["symbol"], row["score"]))
            db.execute("INSERT INTO position_intents VALUES (?,?,?,?,?,?)", (batch["id"], row["model"], row["symbol"], row["target_weight"], row["target_dollars"], row["role"]))
        save_json(staged / "forecast.json", batch)
        db.execute("INSERT INTO forecast_integrity VALUES (?,?)",
                   (batch["id"], digest((staged / "forecast.json").read_bytes())))
        pd.DataFrame(rows).to_csv(staged / "position_intents.csv", index=False)
        forecast_times(str(asof.date()), now_utc())  # Fail if serialization/locking crossed cutoff.
        db.commit()
        event(db, "forecast_frozen", {"batch": batch["id"], "version": version, "entry_at": times["entry_at"], "active": policy["active_model"]})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return staged


def evaluate(snapshot: str | None = None) -> Path:
    """Freeze first-observed outcomes; mark shadow performance, never broker P&L."""
    with closing(connect_db()) as db:
        forward_tables(db)
        return _evaluate(db, snapshot)


def _evaluate(db, snapshot: str | None) -> Path:
    bars, manifest, _ = load_snapshot(snapshot)
    output = ROOT / "state/forward_evaluations" / (pd.Timestamp(now_utc()).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:6])
    output.mkdir(parents=True)
    batches = db.execute("SELECT id,path FROM forecast_batches ORDER BY entry_at,issued_at").fetchall()
    groups = {}
    pending = 0
    for batch_id, path in batches:
        payload = (ROOT / path / "forecast.json").read_bytes()
        integrity = db.execute("SELECT sha256 FROM forecast_integrity WHERE batch_id=?", (batch_id,)).fetchone()
        if not integrity or digest(payload) != integrity[0]:
            raise ValueError("Frozen forecast integrity mismatch; evaluation refused")
        batch = json.loads(payload)
        if pd.Timestamp(batch["issued_at_utc"]) >= pd.Timestamp(batch["entry_at"]):
            raise ValueError("Found a retrospective prediction in the prospective ledger")
        if pd.Timestamp(batch["exit_date"]) > bars.date.max() or pd.Timestamp(now_utc()) < pd.Timestamp(batch["exit_at"]):
            pending += 1
            continue
        outcome_path = ROOT / path / "outcome.json"
        if outcome_path.exists():
            integrity = db.execute("SELECT sha256 FROM outcome_integrity WHERE batch_id=?", (batch_id,)).fetchone()
            if not integrity or digest(outcome_path.read_bytes()) != integrity[0]:
                raise ValueError("Frozen outcome integrity mismatch; evaluation refused")
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        else:
            symbols = sorted({r["symbol"] for r in batch["position_intents"]})
            prices = wide(bars, "adj_open", symbols)
            ret = prices.loc[batch["exit_date"]] / prices.loc[batch["entry_date"]] - 1
            outcome = {"observed_at_utc": now_utc(), "snapshot": manifest["id"], "batch": batch["id"], "returns": ret.to_dict(), "valuation": "adjusted-open total-return proxy, not broker fills"}
            save_json(outcome_path, outcome)
            db.execute("INSERT INTO outcome_integrity VALUES (?,?)", (batch_id, digest(outcome_path.read_bytes())))
            event(db, "forward_outcome_frozen", {"batch": batch["id"], "snapshot": manifest["id"]})
        for model in batch["policy"]["research_config"]["models"]:
            intents = [r for r in batch["position_intents"] if r["model"] == model]
            groups.setdefault((batch["version"], model), []).append((batch, intents, outcome))
    summary = []
    for (version, model), observations in groups.items():
        idx = pd.DatetimeIndex([b["decision_date"] for b, _, _ in observations], name="decision_date")
        weights = pd.DataFrame([{r["symbol"]: r["target_weight"] for r in intents} for _, intents, _ in observations], index=idx)
        targets = pd.DataFrame([{s: outcome["returns"][s] for s in weights.columns} for _, _, outcome in observations], index=idx)
        timing = pd.DataFrame({"entry_date": pd.to_datetime([b["entry_date"] for b, _, _ in observations]), "exit_date": pd.to_datetime([b["exit_date"] for b, _, _ in observations])}, index=idx)
        cfg = observations[0][0]["policy"]["research_config"]
        sim = simulate(weights, targets, timing, cfg["cost_bps"], buy_hold=model.endswith("buy_hold"), liquidate=False)
        sim["daily"].to_csv(output / f"{version}_{model}_daily.csv")
        summary.append({"version": version, "model": model, "observed_sessions": len(idx), "net_shadow_return": float(sim["daily"].nav.iloc[-1] - 1)})
    pd.DataFrame(summary, columns=["version", "model", "observed_sessions", "net_shadow_return"]).to_csv(output / "summary.csv", index=False)
    save_json(output / "status.json", {"at_utc": now_utc(), "pending_batches": pending, "observed_model_series": len(summary), "submitted_orders": db.execute("SELECT count(*) FROM submitted_orders").fetchone()[0], "fills": db.execute("SELECT count(*) FROM fills").fetchone()[0], "note": "Separate shadow curves per frozen version, cash reset on a version change. Missing consecutive forecasts cause an error; no silent gap bridging. No terminal liquidation until the experiment ends."})
    return output
