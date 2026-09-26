"""One reproducible experiment, with every planned trial in a local ledger."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import traceback
from uuid import uuid4

import numpy as np
import pandas as pd

from .core import make_signals, make_targets, partition_dates, score_weights, simulate
from .data import ROOT, digest, load_snapshot, now_utc, save_json, wide


def connect_db() -> sqlite3.Connection:
    (ROOT / "state").mkdir(exist_ok=True)
    db = sqlite3.connect(ROOT / "state/research.sqlite")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, config TEXT NOT NULL,
          snapshot TEXT, source_sha256 TEXT, status TEXT NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS trials (
          run_id TEXT NOT NULL REFERENCES runs(id), partition TEXT NOT NULL,
          model TEXT NOT NULL, origin TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
          PRIMARY KEY(run_id, partition, model));
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, at_utc TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
    """)
    return db


def event(db, kind: str, payload: dict) -> None:
    db.execute("INSERT INTO events(at_utc,kind,payload) VALUES (?,?,?)",
               (now_utc(), kind, json.dumps(payload, sort_keys=True, allow_nan=False)))
    db.commit()


def source_archive(destination: Path) -> str:
    """Archive the executable source, tests, config and locked environment."""
    files = sorted([*ROOT.glob("quantlab/*.py"), *ROOT.glob("tests/*.py"),
                    *ROOT.glob("config/*.json"), ROOT / "requirements.lock", ROOT / "pyproject.toml"])
    hashes = {}
    for path in files:
        if path.name.startswith("private"):
            continue
        name = path.relative_to(ROOT)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[str(name).replace("\\", "/")] = digest(path.read_bytes())
    save_json(destination / "hashes.json", hashes)
    return digest(json.dumps(hashes, sort_keys=True).encode())


def validate_config(cfg: dict) -> None:
    protocol = json.loads((ROOT / "config/protocol.json").read_text(encoding="utf-8"))
    holdout = pd.Timestamp(protocol["historical_holdout_start"])
    if pd.Timestamp(cfg["validation_end"]) >= holdout or pd.Timestamp(cfg["discovery_end"]) >= holdout:
        raise ValueError("Historical holdout is sealed; research cannot evaluate 2023 onward")
    keys = ["data_start", "discovery_start", "discovery_end", "validation_start", "validation_end", "holdout_start"]
    dates = [pd.Timestamp(cfg[k]) for k in keys]
    if any(d.tzinfo is not None for d in dates) or any(a >= b for a, b in zip(dates, dates[1:])):
        raise ValueError("Require distinct chronological date partitions")
    if cfg["horizon_sessions"] != 1:
        raise ValueError("Only the audited one-session target is implemented")
    if type(cfg.get("rebalance_every", 1)) is not int or cfg.get("rebalance_every", 1) < 1:
        raise ValueError("Rebalance cadence must be a positive integer")
    if len(cfg["symbols"]) < 2 or len(set(cfg["symbols"])) != len(cfg["symbols"]):
        raise ValueError("Duplicate or insufficient symbols")
    if cfg["benchmark"] in cfg["symbols"]:
        raise ValueError("Benchmark must be separate from sector universe")
    allowed = {"equal_weight", "equal_buy_hold", "spy_buy_hold", "momentum", "reversal", "low_vol", "mom_rev_blend", "trend_pullback", "vol_scaled_reversal"}
    models = cfg["models"]
    if not {"equal_weight", "spy_buy_hold"}.issubset(models) or len(set(models)) != len(models) or set(models) - allowed:
        raise ValueError("Require known unique models and equal_weight/spy_buy_hold benchmarks")
    if any(m not in cfg["origins"] for m in models):
        raise ValueError("Every serious model needs an origin label")
    if not 0 <= cfg["cost_bps"] <= cfg["stress_cost_bps"] < 1000:
        raise ValueError("Invalid cost assumptions")
    if cfg["bootstrap_samples"] < 100 or cfg["bootstrap_block"] < 1:
        raise ValueError("Invalid uncertainty settings")
    for candidate, comparator in cfg["comparators"].items():
        if candidate in models and comparator not in models:
            raise ValueError(f"Include the matched comparator {comparator} for {candidate}")


def rank_ic(scores: pd.DataFrame, targets: pd.DataFrame) -> pd.Series:
    a = scores.rank(axis=1, method="average")
    b = targets.rank(axis=1, method="average")
    a, b = a.sub(a.mean(axis=1), axis=0), b.sub(b.mean(axis=1), axis=0)
    denom = np.sqrt(a.pow(2).sum(axis=1) * b.pow(2).sum(axis=1)).replace(0, np.nan)
    return (a.mul(b).sum(axis=1) / denom).rename("rank_ic")


def run(cfg: dict, snapshot: str | None = None) -> Path:
    run_id = pd.Timestamp(now_utc()).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    destination = ROOT / "runs" / run_id
    destination.mkdir(parents=True)
    db = connect_db()
    db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
               (run_id, now_utc(), json.dumps(cfg, sort_keys=True), snapshot, None, "started", None))
    for part in ["discovery", "validation"]:
        for model in cfg.get("models", []):
            db.execute("INSERT OR IGNORE INTO trials VALUES (?,?,?,?,?,?)",
                       (run_id, part, model, cfg.get("origins", {}).get(model, "unlabelled"), "planned", None))
    db.commit()
    current = None
    try:
        validate_config(cfg)
        save_json(destination / "config.json", cfg)
        source_hash = source_archive(destination / "source")
        bars, data_manifest, _ = load_snapshot(snapshot)
        if set(cfg["symbols"] + [cfg["benchmark"]]) - set(data_manifest["symbols"]):
            raise ValueError("Snapshot lacks required symbols")
        if pd.Timestamp(data_manifest["end"]) < pd.Timestamp(cfg["validation_end"]) - pd.Timedelta(days=7):
            raise ValueError("Snapshot does not cover the validation period")
        # Enforce the historical boundary BEFORE feature, target or performance construction.
        bars = bars[(bars.date >= cfg["data_start"]) & (bars.date <= cfg["validation_end"])].copy()
        close = wide(bars, "adj_close", cfg["symbols"])
        opens = wide(bars, "adj_open", cfg["symbols"])
        signals = make_signals(close, cfg)
        targets, timing = make_targets(opens)
        spy_targets, _ = make_targets(wide(bars, "adj_open", [cfg["benchmark"]]))
        manifest = {"id": run_id, "created_at_utc": now_utc(), "snapshot": data_manifest["id"],
            "source_sha256": source_hash, "python": platform.python_version(),
            "packages": {p.metadata["Name"]: p.version for p in importlib.metadata.distributions()},
            "historical_cutoff": cfg["validation_end"], "holdout_opened": False,
            "serious_models_this_run": len(cfg["models"]),
            "ledger_trials_before_run": db.execute("SELECT count(*) FROM trials WHERE run_id != ?", (run_id,)).fetchone()[0]}
        save_json(destination / "manifest.json", manifest)
        save_json(destination / "data_manifest.json", data_manifest)
        db.execute("UPDATE runs SET snapshot=?, source_sha256=? WHERE id=?", (data_manifest["id"], source_hash, run_id))
        db.commit()
        results = {}
        for part in ["discovery", "validation"]:
            idx = partition_dates(timing, cfg[part + "_start"], cfg[part + "_end"])
            idx = idx.intersection(signals["equal_weight"].index)
            results[part] = {}
            for model in cfg["models"]:
                current = (run_id, part, model)
                db.execute("UPDATE trials SET status='running' WHERE run_id=? AND partition=? AND model=?", current)
                db.commit()
                if model == "spy_buy_hold":
                    y = spy_targets.loc[idx]
                    score = y * 0
                    weights = pd.DataFrame(1.0, index=idx, columns=y.columns)
                else:
                    y, score = targets.loc[idx], signals[model].loc[idx]
                    weights = score_weights(score, cfg["tilt"], cfg["max_weight"])
                sim = simulate(weights, y, timing.loc[idx], cfg["cost_bps"], buy_hold=model.endswith("buy_hold"), rebalance_every=cfg.get("rebalance_every", 1))
                sim["stress"] = simulate(weights, y, timing.loc[idx], cfg["stress_cost_bps"], buy_hold=model.endswith("buy_hold"), rebalance_every=cfg.get("rebalance_every", 1))
                sim["ic"] = rank_ic(score, y)
                path = destination / part / model
                path.mkdir(parents=True)
                for key in ["daily", "trades", "contributions", "weights"]:
                    sim[key].to_csv(path / f"{key}.csv", index=key in {"daily", "weights"})
                sim["ic"].to_csv(path / "ic.csv")
                score.rename_axis("decision_date").to_csv(path / "scores.csv")
                y.rename_axis("decision_date").to_csv(path / "targets.csv")
                results[part][model] = sim
                db.execute("UPDATE trials SET status='complete' WHERE run_id=? AND partition=? AND model=?", current)
                db.commit()
                print(f"{part}/{model}: {len(idx)} decisions; accounting complete", flush=True)
        from .report import build_report
        build_report(destination, manifest, results, cfg)
        db.execute("UPDATE runs SET status='complete' WHERE id=?", (run_id,))
        event(db, "research_completed", {"run": run_id, "models": cfg["models"], "holdout_opened": False})
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        (destination / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        db.execute("UPDATE runs SET status='failed', error=? WHERE id=?", (error, run_id))
        if current:
            db.execute("UPDATE trials SET status='failed', error=? WHERE run_id=? AND partition=? AND model=?", (error, *current))
        db.execute("UPDATE trials SET status='not_run', error=? WHERE run_id=? AND status='planned'", (error, run_id))
        event(db, "research_failed", {"run": run_id, "error": error})
        raise
    finally:
        db.close()
    return destination


def history() -> pd.DataFrame:
    with connect_db() as db:
        return pd.read_sql_query("SELECT r.id,r.created_at,r.status,r.snapshot,t.partition,t.model,t.origin,t.status AS trial_status,t.error FROM runs r LEFT JOIN trials t ON r.id=t.run_id ORDER BY r.created_at,t.partition,t.model", db)
