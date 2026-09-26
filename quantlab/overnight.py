"""Sealed-history overnight experiments; conditional MOC decisions lag EOD by one.

Rows are attributed to exit session d. An overnight trade enters close d-1,
using features through close d-2, and exits open d; cash earns zero thereafter.
Adjusted OHLC are total-return proxies, not executable auction prices.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .core import trading_cost
from .report import _block_ci, _metrics

HOLDOUT = pd.Timestamp("2023-01-01")


def decompose(bars: pd.DataFrame) -> pd.DataFrame:
    """Raw and adjusted identities, indexed by exit date and symbol as columns.

    The first observation for each symbol has no overnight/close-close label.
    Calendar completeness must have been checked by the upstream data loader.
    """
    required = ["date", "symbol", "open", "high", "low", "close", "adj_close", "volume", "adj_open"]
    b = bars[required].copy()
    b["date"] = pd.to_datetime(b.date)
    if b.empty or b.date.isna().any() or b.date.dt.tz is not None:
        raise ValueError("Require nonempty, timezone-naive session dates")
    if (b.date >= HOLDOUT).any():
        raise ValueError("Historical holdout is sealed: reject input bars from 2023 onward")
    if not b.date.equals(b.date.dt.normalize()) or b.duplicated(["date", "symbol"]).any():
        raise ValueError("Require unique date/symbol observations at normalized session dates")
    if b.symbol.isna().any() or (b.symbol == "").any():
        raise ValueError("Missing symbols")
    values = b[required[2:]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Require positive finite OHLCV and adjustment data")
    if ((b.high < b[["open", "close", "low"]].max(axis=1))
            | (b.low > b[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Inconsistent OHLC geometry")
    b = b.sort_values(["symbol", "date"]).reset_index(drop=True)
    b["adjustment_factor"] = b.adj_close / b.close
    if not np.allclose(b.adj_open, b.open * b.adjustment_factor, rtol=1e-10, atol=1e-10):
        raise ValueError("Adjusted open disagrees with the same-session close adjustment factor")
    prior = b.groupby("symbol", sort=False)[["date", "close", "adj_close", "adjustment_factor"]].shift(1)
    b["entry_close_date"] = prior.date
    factor_ratio = b.adjustment_factor / prior.adjustment_factor
    for prefix, op, cl, prev in [("raw", b.open, b.close, prior.close),
                                  ("adjusted", b.adj_open, b.adj_close, prior.adj_close)]:
        b[f"{prefix}_overnight"] = op / prev - 1
        b[f"{prefix}_intraday"] = cl / op - 1
        b[f"{prefix}_close_close"] = cl / prev - 1
        b[f"{prefix}_identity_residual"] = (1 + b[f"{prefix}_overnight"]) * (1 + b[f"{prefix}_intraday"]) - (1 + b[f"{prefix}_close_close"])
    b["factor_overnight_residual"] = (1 + b.adjusted_overnight) - (1 + b.raw_overnight) * factor_ratio
    b["factor_close_close_residual"] = (1 + b.adjusted_close_close) - (1 + b.raw_close_close) * factor_ratio
    b["factor_intraday_residual"] = b.adjusted_intraday - b.raw_intraday
    b["adjustment_log_effect"] = np.log(factor_ratio)
    residuals = b.filter(regex="residual$")
    if residuals.abs().max().max() > 1e-9:
        raise ValueError("Price/factor decomposition failed its accounting identity")
    return b.sort_values(["date", "symbol"]).reset_index(drop=True)


def _account(asset_return: np.ndarray, exposure: np.ndarray, bps: float, buy_hold=False) -> dict:
    """Self-financing cash/one-asset accounting; fees per dollar on EACH leg."""
    r, w = np.asarray(asset_return, float), np.asarray(exposure, float)
    if r.shape != w.shape or r.ndim != 1 or not len(r) or not np.isfinite(r).all() or (r <= -1).any():
        raise ValueError("Invalid or misaligned return/exposure arrays")
    if not np.isfinite(w).all() or ((w < 0) | (w > 1)).any() or not 0 <= bps < 1000:
        raise ValueError("Require unlevered finite exposure and cost in [0,1000)bps")
    if buy_hold and not (w == 1).all():
        raise ValueError("Full buy-and-hold must remain fully invested")
    rate = bps / 10000
    fees = {x: trading_cost(np.zeros(1), np.array([x]), rate)[0] for x in np.unique(w)}
    entry_cost = np.array([fees[x] for x in w])
    if buy_hold:
        entry_cost[1:] = 0
    gross = w * r
    buys = (1 - entry_cost) * w
    sells = (1 - entry_cost) * w * (1 + r)
    if buy_hold:
        buys[1:], sells[:-1] = 0, 0
    costs = entry_cost + rate * sells
    factors = (1 - entry_cost) * (1 + gross) - rate * sells
    nav = np.cumprod(factors)
    before = np.r_[1.0, nav[:-1]]
    return {"gross_return": gross, "net_return": factors - 1, "cost_fraction": costs,
            "turnover": buys + sells, "nav_before": before, "nav": nav,
            "cost_per_initial_dollar": before * costs,
            "gross_pnl_per_initial_dollar": before * (1 - entry_cost) * gross}


def _break_even(r, w, buy_hold) -> tuple[float, bool]:
    def log_growth(cost):
        return np.log1p(_account(r, w, cost, buy_hold)["net_return"]).sum()
    if log_growth(0) <= 0:
        return 0.0, False
    if log_growth(999) > 0:
        return 999.0, True
    lo, hi = 0.0, 999.0
    for _ in range(35):
        mid = (lo + hi) / 2
        if log_growth(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2, False


def _stats(r: pd.Series) -> dict:
    p05 = r.quantile(0.05)
    return {**_metrics(r), "skew": float(r.skew()) if len(r) >= 3 else float("nan"),
            "p01": float(r.quantile(0.01)), "es_5pct": float(r[r <= p05].mean()),
            "worst_session": float(r.min())}


def run_overnight(bars: pd.DataFrame, cfg: dict, out_dir: Path, parts=("discovery", "validation")) -> dict:
    """Run every declared alternative; return exit-date-indexed model streams.

    Partitions reset to cash and liquidate independently. All families share
    dates: both entry and exit must be inside a partition, including intraday.
    Controls calibrate exposure using EARLIER feature observations, never
    validation occupancy. They approximate exposure matching; report mismatch.
    """
    partitions = {p: (pd.Timestamp(cfg[p + "_start"]), pd.Timestamp(cfg[p + "_end"]))
                  for p in ("discovery", "validation")}
    if any(pd.isna(x) or x.tzinfo is not None or x >= HOLDOUT for pair in partitions.values() for x in pair):
        raise ValueError("Historical holdout is sealed; partition dates must precede 2023")
    ds, de = partitions["discovery"]
    vs, ve = partitions["validation"]
    if not ds <= de < vs <= ve:
        raise ValueError("Require independent chronological discovery and validation partitions")
    symbols = list(cfg["overnight_symbols"])
    if len(set(symbols)) != len(symbols) or not {"SPY", "QQQ"}.issubset(symbols):
        raise ValueError("Unique overnight universe must include SPY and QQQ")
    costs = sorted(set([*cfg.get("cost_grid_bps", [0, 1, 2.5, 5, 10]), cfg["cost_bps"], cfg["stress_cost_bps"]]))
    if any(not np.isfinite(c) or not 0 <= c < 1000 for c in costs):
        raise ValueError("Invalid per-leg cost grid")
    if cfg["cost_bps"] > cfg["stress_cost_bps"]:
        raise ValueError("Stress costs must be at least base costs")
    if (pd.to_datetime(bars.date) >= HOLDOUT).any():
        raise ValueError("Historical holdout is sealed: reject input bars from 2023 onward")
    dec = decompose(bars[bars.symbol.isin(symbols)])
    close = dec.pivot(index="date", columns="symbol", values="adj_close").reindex(columns=symbols)
    if close.isna().any().any():
        raise ValueError("Incomplete universe: no silent filling or survivor filtering")
    dates = pd.Series(close.index, index=close.index)
    prior, feature_date = dates.shift(1), dates.shift(2)
    sma = close.rolling(200, min_periods=200).mean()
    risk = (close > sma).astype(float).where(sma.notna()).shift(2)
    reversal = (close.pct_change(3, fill_method=None) < 0).astype(float).where(close.shift(3).notna()).shift(2)
    conditions = {"risk_on": risk, "pullback": reversal}
    regime = pd.Series(np.where(risk.SPY == 1, "risk_on", "risk_off"), index=close.index)
    masks = {p: (prior >= start) & (dates >= start) & (dates <= end)
             for p, (start, end) in partitions.items()}
    for mask in masks.values():
        if not mask.any() or risk.loc[mask, ["SPY", "QQQ"]].isna().any().any():
            raise ValueError("Empty partition or insufficient 200-session lagged feature history")
    specs = [(f"{s}_{kind}", s, kind, None) for s in symbols
             for kind in ("overnight", "intraday", "close_buy_hold")]
    specs += [(f"{s}_{rule}_{suffix}", s, "overnight", (rule, control))
              for s in ("SPY", "QQQ") for rule in conditions
              for control, suffix in ((False, "overnight"), (True, "exposure_control"))]
    results, summary, sensitivity, yearly, regimes, decomposition = {}, [], [], [], [], []
    out_dir = Path(out_dir)
    (out_dir / "daily").mkdir(parents=True, exist_ok=True)
    (out_dir / "weights").mkdir(exist_ok=True)
    dec.to_csv(out_dir / "decomposition.csv", index=False)
    for name, symbol, kind, conditional in specs:
        symbol_dec = dec[dec.symbol == symbol].set_index("date")
        pieces = []
        for part, mask in masks.items():
            if part not in parts:
                continue
            idx = close.index[mask]
            fields = symbol_dec.loc[idx]
            leg = "close_close" if kind == "close_buy_hold" else kind
            asset_return = fields["adjusted_" + leg].to_numpy()
            exposure = np.ones(len(idx))
            calibration = ""
            if conditional:
                rule, control = conditional
                state = conditions[rule][symbol]
                exposure = state.loc[idx].to_numpy()
                if control:
                    earlier = (dates < ds) & state.notna() if part == "discovery" else masks["discovery"]
                    if not earlier.any():
                        raise ValueError("No prior observations to calibrate exposure control")
                    exposure = np.full(len(idx), float(state.loc[earlier].mean()))
                    calibration = "pre_discovery_features" if part == "discovery" else "discovery_features"
            by_cost = {c: _account(asset_return, exposure, c, kind == "close_buy_hold") for c in costs}
            daily = pd.DataFrame(by_cost[cfg["cost_bps"]], index=idx)
            daily.index.name = "exit_date"
            daily["net_return_stress"] = by_cost[cfg["stress_cost_bps"]]["net_return"]
            daily["partition"], daily["symbol"], daily["kind"] = part, symbol, kind
            daily["entry_date"] = dates.loc[idx] if kind == "intraday" else prior.loc[idx]
            daily["feature_date"], daily["market_regime"] = feature_date.loc[idx], regime.loc[idx]
            daily["exposure"], daily["cash_weight"] = exposure, 1 - exposure
            daily["raw_asset_return"] = fields["raw_" + leg]
            daily["asset_overnight_return"] = fields.adjusted_overnight
            pieces.append(daily)
            breakeven, capped = _break_even(asset_return, exposure, kind == "close_buy_hold")
            row = {"model": name, "symbol": symbol, "kind": kind, "partition": part,
                   "role": "baseline" if conditional is None else "exposure_control" if conditional[1] else "exploratory_candidate",
                   "control_calibration": calibration, **_stats(daily.net_return),
                   "mean_exposure": float(exposure.mean()), "active_fraction": float((exposure > 0).mean()),
                   "annual_two_sided_turnover": float(daily.turnover.mean() * 252),
                   "total_cost_per_initial_dollar": float(daily.cost_per_initial_dollar.sum()),
                   "worst_asset_gap": float(fields.adjusted_overnight.min()),
                   "worst_held_gap": float((exposure * fields.adjusted_overnight).min()) if kind != "intraday" else 0.0,
                   "stress_cagr": _metrics(daily.net_return_stress)["cagr"],
                   "break_even_cost_bps_per_leg": breakeven, "break_even_above_999bps": capped}
            summary.append(row)
            for cost, account in by_cost.items():
                sensitivity.append({"model": name, "partition": part, "cost_bps_per_leg": cost,
                                    **_stats(pd.Series(account["net_return"]))})
            for group, rows in [(daily.index.year, yearly), (daily.market_regime, regimes)]:
                for value, subset in daily.groupby(group):
                    rows.append({"model": name, "partition": part, "group": value, **_stats(subset.net_return),
                                 "mean_exposure": float(subset.exposure.mean()), "turnover": float(subset.turnover.sum())})
        combined = pd.concat(pieces)
        weights = combined[["exposure"]].rename(columns={"exposure": symbol})
        results[name] = {"daily": combined, "weights": weights}
        combined.to_csv(out_dir / "daily" / f"{name}.csv")
        weights.to_csv(out_dir / "weights" / f"{name}.csv")
    for part, mask in masks.items():
        if part not in parts:
            continue
        for symbol in symbols:
            sub = dec[(dec.symbol == symbol) & dec.date.isin(close.index[mask])]
            row = {"partition": part, "symbol": symbol, "max_identity_factor_residual": float(sub.filter(regex="residual$").abs().max().max()),
                   "adjustment_log_effect": float(sub.adjustment_log_effect.sum())}
            for prefix in ("raw", "adjusted"):
                on, intr, total = [float(np.log1p(sub[f"{prefix}_{leg}"]).sum()) for leg in ("overnight", "intraday", "close_close")]
                row.update({f"{prefix}_overnight_log_return": on, f"{prefix}_intraday_log_return": intr,
                            f"{prefix}_close_close_log_return": total, f"{prefix}_overnight_log_share": on / total if abs(total) > 1e-12 else np.nan})
            decomposition.append(row)
    paired = []
    boot = {"bootstrap_samples": 1000, "bootstrap_block": 20, "seed": 20260920, **cfg}
    for symbol in ("SPY", "QQQ"):
        for rule in conditions:
            candidate = results[f"{symbol}_{rule}_overnight"]["daily"]
            control = results[f"{symbol}_{rule}_exposure_control"]["daily"]
            for part in masks:
                if part not in parts:
                    continue
                left, right = [x[x.partition == part] for x in (candidate, control)]
                difference = left.net_return - right.net_return
                lo, hi = _block_ci(difference, boot, 252)
                paired.append({"symbol": symbol, "rule": rule, "partition": part,
                               "annual_mean_difference": float(difference.mean() * 252), "ci_low": lo, "ci_high": hi,
                               "realized_exposure_difference": float((left.exposure - right.exposure).mean())})
    for filename, rows in [("summary", summary), ("cost_sensitivity", sensitivity), ("yearly", yearly),
                            ("regimes", regimes), ("decomposition_diagnostics", decomposition), ("paired_controls", paired)]:
        pd.DataFrame(rows).to_csv(out_dir / f"{filename}.csv", index=False)
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(
        "# Overnight discovery screen\n\n"
        "Every declared alternative is reported; none is selected or promoted. All input rows and evaluated outcomes precede 2023.\n\n"
        "Overnight: enter close t, exit open t+1. Conditional rules use EOD t-1 (two sessions before the exit row): own close above its 200-session SMA, or negative own 3-session return. No final-close feature decides that same close auction. Intraday enters the open and exits that session's close. Buy-and-hold owns close-to-close returns.\n\n"
        "Exit dates align streams to a close mark with overnight positions already in cash. Each holding interval bears its own entry/exit fees. Partition accounting starts in cash and liquidates at the end; all families share dates whose previous close and exit are inside the partition. Warmup features may precede a partition.\n\n"
        "Exposure controls use constant allocations calibrated on pre-discovery features for discovery and discovery features for validation. Realized exposure differences are disclosed: these are chronological approximations to matching, not hindsight-exact controls. Cash earns zero.\n\n"
        "Costs are basis points on each traded leg, including self-financing entry fees and exit notional after price movement. The 0/1/2.5/5/10bps grid is sensitivity, not measured auction execution. Break-even means zero compounded return and is capped at 999bps; zero means no positive zero-cost edge or no exposure.\n\n"
        "Adjusted opens use the close adjustment factor; adjusted returns are dividend/split proxies, not executable prices. Raw and adjusted price/log identities and adjustment-factor residuals are saved. Log shares can exceed 100% or be negative and are undefined near zero total log return.\n\n"
        "Sharpe uses zero cash yield. ES5 is the mean lower-5% session return. Regime groups use lagged SPY versus its 200-session SMA; their annualized metrics describe selected observations, not separate investable portfolios. Paired block intervals are descriptive after multiple research attempts. Auction availability, fills, spreads, cash yield, and realized execution remain unverified.\n",
        encoding="utf-8")
    lines = ["\n## Every alternative in declared order\n",
             "| Partition | Model | Net CAGR | Annual vol | Sharpe | Max drawdown | Mean exposure | Break-even bps/leg |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in summary:
        lines.append(f"| {row['partition']} | {row['model']} | {row['cagr']:.2%} | {row['annual_vol']:.2%} | {row['sharpe_zero_rf']:.2f} | {row['max_drawdown']:.2%} | {row['mean_exposure']:.2%} | {row['break_even_cost_bps_per_leg']:.2f}{'+' if row['break_even_above_999bps'] else ''} |")
    with (out_dir / "report.md").open("a", encoding="utf-8") as report:
        report.write("\n".join(lines) + "\n")
    return results
