"""Broader historical-roster data, sharing phase-one immutable raw storage."""
from __future__ import annotations
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
import requests
from .data import ROOT, calendar, digest, latest_completed_session, now_utc, save_json


def normalize(payload: dict, symbol: str, start: str, end: str) -> pd.DataFrame:
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"Unavailable chart: {chart.get('error')}")
    result = chart["result"][0]
    meta = result["meta"]
    if meta.get("symbol") != symbol or meta.get("currency") != "USD" or meta.get("instrumentType") not in {"ETF", "EQUITY"}:
        raise ValueError("Unexpected symbol/currency/security type")
    dates = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert("America/New_York").tz_localize(None).normalize()
    bars = pd.DataFrame(result["indicators"]["quote"][0], index=dates)
    bars["adj_close"] = result["indicators"]["adjclose"][0]["adjclose"]
    bars = bars.loc[start:end].copy()
    cols = ["open", "high", "low", "close", "volume", "adj_close"]
    if bars.empty or bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError("Empty, duplicate or unsorted bars")
    if not np.isfinite(bars[cols].to_numpy()).all() or (bars[cols] <= 0).any().any():
        raise ValueError("Nonfinite/nonpositive bars; no fill permitted")
    if ((bars.high * 1.00001 < bars[["open", "close", "low"]].max(axis=1)) | (bars.low * .99999 > bars[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Inconsistent OHLC")
    expected = calendar(start, end).sessions_in_range(bars.index.min(), bars.index.max())
    if not bars.index.equals(expected):
        raise ValueError("Internal session gap; no survivor-filtered inner join")
    bars["adj_open"] = bars.open * bars.adj_close / bars.close
    bars["symbol"] = symbol
    bars.index.name = "date"
    return bars.reset_index()[["date", "symbol", *cols, "adj_open"]]


def fetch_phase2(cfg: dict, end: str | None = None, *, etfs_only: bool = False) -> Path:
    end = end or str(latest_completed_session().date())
    if pd.Timestamp(end) > latest_completed_session():
        raise ValueError("Incomplete daily data")
    universe_path = ROOT / "config/phase2_universe.json"
    universe = json.loads(universe_path.read_text()) if not etfs_only else {"records": []}
    symbols = sorted(set(cfg["overnight_symbols"] + [r["symbol"] for r in universe["records"] if r.get("symbol")]))
    cache = ROOT / "data/phase2_downloads" / f"{cfg['data_start']}_{end}"
    cache.mkdir(parents=True, exist_ok=True)
    sources, frames, failures = [], [], []
    for symbol in symbols:
        meta_path = cache / f"{symbol}.json"
        try:
            if meta_path.exists():
                source = json.loads(meta_path.read_text())
                raw = (ROOT / source["file"]).read_bytes()
                if digest(raw) != source["sha256"]:
                    raise ValueError("Cached raw hash mismatch")
                bars = normalize(json.loads(raw), symbol, cfg["data_start"], end)
            else:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                params = {"period1": int(pd.Timestamp(cfg["data_start"], tz="UTC").timestamp()), "period2": int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp()), "interval": "1d", "events": "div,splits", "includeAdjustedClose": "true"}
                for attempt in range(3):
                    response = requests.get(url, params=params, timeout=25, headers={"User-Agent": "quantlab-personal-research/0.2"})
                    if response.status_code not in {429, 500, 502, 503}:
                        break
                    time.sleep(2 ** attempt)
                raw = response.content
                raw_hash = digest(raw)
                raw_path = ROOT / "data/raw" / f"{raw_hash}.json"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                if not raw_path.exists(): raw_path.write_bytes(raw)
                response.raise_for_status()
                bars = normalize(json.loads(raw), symbol, cfg["data_start"], end)
                source = {"symbol": symbol, "url": response.url, "sha256": raw_hash, "fetched_at_utc": now_utc(), "file": str(raw_path.relative_to(ROOT)).replace("\\", "/")}
                save_json(meta_path, source)
            frames.append(bars)
            sources.append(source)
            print(f"{symbol}: {len(bars)} bars", flush=True)
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            failures.append({"symbol": symbol, "error": str(exc)[:250], "attempted_at": now_utc()})
            print(f"UNAVAILABLE {symbol}: {str(exc)[:80]}", flush=True)
    missing_etfs = set(cfg["overnight_symbols"]) - {s["symbol"] for s in sources}
    save_json(cache / "failures.json", {"failures": failures})
    if missing_etfs: raise ValueError(f"Missing required ETF histories: {missing_etfs}")
    bars = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])
    content = bars.to_csv(index=False, float_format="%.12g").encode()
    sid = digest(content)[:20]
    path = ROOT / "data/snapshots" / sid
    if not path.exists():
        path.mkdir(parents=True)
        (path / "bars.csv").write_bytes(content)
        save_json(path / "manifest.json", {"id": sid, "created_at_utc": now_utc(), "start": cfg["data_start"], "end": end, "symbols": sorted(bars.symbol.unique()), "rows": len(bars), "bars_sha256": digest(content), "raw_sources": sources, "unavailable": failures, "universe": universe, "universe_sha256": digest(universe_path.read_bytes()) if not etfs_only else None, "source": "Yahoo chart, latest revised vintage; archived historical roster with availability/survivor bias"})
    pointer = "PHASE2_ETFS" if etfs_only else "PHASE2_LATEST"
    (ROOT / "data" / pointer).write_text(sid)
    return path
