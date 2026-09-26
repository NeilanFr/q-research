"""Small immutable Yahoo snapshot cache; no credentials or broker needed."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def calendar(start="2005-01-01", end="2027-12-31"):
    return xcals.get_calendar("XNYS", start=pd.Timestamp(start) - pd.Timedelta(days=10),
                             end=pd.Timestamp(end) + pd.Timedelta(days=10))


def latest_completed_session(now=None) -> pd.Timestamp:
    now = pd.Timestamp(now or now_utc())
    if now.tzinfo is None:
        raise ValueError("Clock must be timezone aware")
    cal = calendar((now - pd.Timedelta(days=30)).date().isoformat(),
                   (now + pd.Timedelta(days=30)).date().isoformat())
    # Conservative one-hour publication lag, also accommodates early closes.
    valid = cal.schedule[cal.schedule["close"] + pd.Timedelta(hours=1) <= now]
    return valid.index[-1]


def parse_chart(payload: dict, symbol: str, start: str, end: str) -> pd.DataFrame:
    chart = payload.get("chart", {})
    result = chart.get("result")
    if chart.get("error") or not result or len(result) != 1:
        raise ValueError(f"{symbol}: malformed/error Yahoo chart")
    item = result[0]
    meta = item["meta"]
    if meta.get("symbol") != symbol or meta.get("currency") != "USD" or meta.get("instrumentType") != "ETF":
        raise ValueError(f"Unexpected instrument metadata for {symbol}")
    idx = pd.to_datetime(item["timestamp"], unit="s", utc=True).tz_convert("America/New_York").tz_localize(None).normalize()
    frame = pd.DataFrame(item["indicators"]["quote"][0], index=idx)
    frame["adj_close"] = item["indicators"]["adjclose"][0]["adjclose"]
    frame = frame.loc[start:end].copy()
    required = ["open", "high", "low", "close", "adj_close", "volume"]
    if frame.empty or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f"{symbol}: empty, duplicate or unordered sessions")
    if not np.isfinite(frame[required].to_numpy()).all() or (frame[required] <= 0).any().any():
        raise ValueError(f"{symbol}: missing, zero or invalid OHLCV; no silent fill")
    tolerance = frame.close * 1e-5
    if ((frame.high + tolerance < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low - tolerance > frame[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError(f"{symbol}: inconsistent OHLC")
    expected = calendar(start, end).sessions_in_range(start, end)
    if not frame.index.equals(expected):
        raise ValueError(f"{symbol}: session gaps/extras: missing={list(expected.difference(frame.index)[:5])}, extra={list(frame.index.difference(expected)[:5])}")
    frame["adj_open"] = frame.open * frame.adj_close / frame.close
    frame["symbol"] = symbol
    frame.index.name = "date"
    return frame.reset_index()[["date", "symbol", *required, "adj_open"]]


def fetch(cfg: dict, end: str | None = None) -> Path:
    end = end or latest_completed_session().date().isoformat()
    if pd.Timestamp(end) > latest_completed_session():
        raise ValueError("Cannot fetch incomplete/future daily bars")
    start, symbols = cfg["data_start"], sorted(set(cfg["symbols"] + [cfg["benchmark"]]))
    period1 = int(pd.Timestamp(start, tz="UTC").timestamp())
    period2 = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp())
    frames, sources = [], []
    raw_dir = ROOT / "data/raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "quantlab-personal-research/0.1"
    for symbol in symbols:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"period1": period1, "period2": period2, "interval": "1d", "events": "div,splits", "includeAdjustedClose": "true"}
        for attempt in range(3):
            response = session.get(url, params=params, timeout=30)
            if response.status_code not in (429, 500, 502, 503):
                break
            time.sleep(2 ** attempt)
        response.raise_for_status()
        payload = response.json()  # HTML 200 must fail.
        frame = parse_chart(payload, symbol, start, end)
        raw_hash = digest(response.content)
        raw_path = raw_dir / f"{raw_hash}.json"
        if not raw_path.exists():
            raw_path.write_bytes(response.content)
        sources.append({"symbol": symbol, "url": response.url, "sha256": raw_hash,
                        "fetched_at_utc": now_utc(), "file": str(raw_path.relative_to(ROOT)).replace("\\", "/")})
        frames.append(frame)
        print(f"Fetched {symbol}: {len(frame)} complete sessions", flush=True)
    bars = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])
    csv = bars.to_csv(index=False, float_format="%.12g").encode()
    snapshot_id = digest(csv)[:20]
    destination = ROOT / "data/snapshots" / snapshot_id
    if not destination.exists():
        destination.mkdir(parents=True)
        (destination / "bars.csv").write_bytes(csv)
        save_json(destination / "manifest.json", {"id": snapshot_id, "created_at_utc": now_utc(),
            "source": "Yahoo unofficial chart endpoint", "start": start, "end": end,
            "symbols": symbols, "rows": len(bars), "bars_sha256": digest(csv), "raw_sources": sources,
            "price_convention": "adj_open = raw_open * adj_close / raw_close; total-return proxy, not executable price",
            "historical_vintage": "latest available revisions; not point-in-time"})
    (ROOT / "data/LATEST").write_text(snapshot_id, encoding="utf-8")
    return destination


def load_snapshot(snapshot: str | None = None) -> tuple[pd.DataFrame, dict, Path]:
    snapshot = snapshot or (ROOT / "data/LATEST").read_text(encoding="utf-8").strip()
    if not snapshot.isalnum():
        raise ValueError("Snapshot must be its content ID")
    path = ROOT / "data/snapshots" / snapshot
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    raw = (path / "bars.csv").read_bytes()
    if digest(raw) != manifest["bars_sha256"]:
        raise ValueError("Snapshot integrity check failed")
    for source in manifest["raw_sources"]:
        if digest((ROOT / source["file"]).read_bytes()) != source["sha256"]:
            raise ValueError("Raw source integrity check failed")
    return pd.read_csv(path / "bars.csv", parse_dates=["date"]), manifest, path


def wide(bars: pd.DataFrame, field: str, symbols: list[str]) -> pd.DataFrame:
    out = bars.pivot(index="date", columns="symbol", values=field).reindex(columns=symbols)
    if out.isna().any().any():
        raise ValueError("Incomplete panel; no forward filling or survivor filtering")
    return out
