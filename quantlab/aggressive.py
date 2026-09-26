"""Historical-roster stock research; deterministic rules and one ranked ridge fit."""
from __future__ import annotations
import numpy as np
import pandas as pd
from .core import trading_cost
from .data import wide


def feature_panel(bars: pd.DataFrame, symbols: list[str], cfg: dict) -> tuple[dict, pd.DataFrame]:
    c, o = [wide(bars, f, symbols) for f in ["adj_close", "adj_open"]]
    raw, volume = [wide(bars, f, symbols) for f in ["close", "volume"]]
    high = wide(bars, "high", symbols) * c / raw
    low = wide(bars, "low", symbols) * c / raw
    r = c.pct_change(fill_method=None)
    spy = wide(bars, "adj_close", ["SPY"]).SPY.pct_change(fill_method=None)
    vol = r.rolling(60).std()
    beta = r.rolling(60).cov(spy).div(spy.rolling(60).var(), axis=0)
    residual = r.sub(beta.mul(spy, axis=0))
    mom = c.shift(5) / c.shift(63) - 1
    r21 = c / c.shift(21) - 1
    r63 = c / c.shift(63) - 1
    anomaly = (volume.rolling(5).mean() / volume.rolling(60).mean()).clip(.1, 10)
    gap = o / c.shift(1) - 1
    location = (c - low) / (high - low).replace(0, np.nan)
    features = {"momentum": mom, "acceleration": r21 - r63 / 3,
        "breakout": c / high.rolling(63).max() - 1,
        "volume_momentum": r21 * np.log(anomaly),
        "reversal": -(c / c.shift(5) - 1),
        "gap_continuation": gap * (2 * location - 1),
        "residual_momentum": residual.rolling(63).sum(),
        "vol_contraction": -(r.rolling(10).std() / vol).where(r63 > 0),
        "volatility": vol, "volume_anomaly": np.log(anomaly)}
    adv = (raw * volume).rolling(20).mean()
    eligible = (adv >= cfg["min_dollar_volume"]) & c.notna() & c.shift(200).notna()
    # Market-risk proxy is information available through this close only.
    features["market_trend"] = pd.DataFrame(np.tile((spy.fillna(0).add(1).cumprod() / spy.fillna(0).add(1).cumprod().rolling(200).mean() - 1).to_numpy()[:, None], (1, len(symbols))), index=c.index, columns=symbols)
    return features, eligible


def ranked(frame: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    f = frame.where(eligible)
    return (f.rank(axis=1, pct=True) - .5) * 2


def top_weights(scores: pd.DataFrame, eligible: pd.DataFrame, k: int | None) -> pd.DataFrame:
    """Equal top-k, with cutoff ties sharing the remaining slots fairly."""
    if k is not None and (type(k) is not int or k < 1): raise ValueError("Invalid top-k")
    if not scores.index.equals(eligible.index) or not scores.columns.equals(eligible.columns): raise ValueError("Eligibility alignment")
    out = np.zeros(scores.shape)
    values, valid = scores.to_numpy(), eligible.to_numpy()
    for i in range(len(out)):
        mask = valid[i] & np.isfinite(values[i])
        n = int(mask.sum())
        if n == 0: continue
        count = min(k or n, n)
        threshold = np.sort(values[i, mask])[-count]
        above, tie = mask & (values[i] > threshold), mask & (values[i] == threshold)
        out[i, above] = 1 / count
        out[i, tie] = (count - above.sum()) / count / tie.sum()
    return pd.DataFrame(out, index=scores.index, columns=scores.columns)


def ridge_score(features: dict, eligible: pd.DataFrame, opens: pd.DataFrame,
                cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    names = ["momentum", "acceleration", "breakout", "volume_momentum", "reversal", "gap_continuation", "residual_momentum", "volatility"]
    x = np.stack([ranked(features[n], eligible).to_numpy() for n in names], axis=2)
    y = opens.shift(-6) / opens.shift(-1) - 1
    y = y.sub(y.where(eligible).mean(axis=1), axis=0).to_numpy()
    # Each five-session target exits six sessions after its feature date.
    exit_dates = pd.Series(opens.index, index=opens.index).shift(-6)
    start, end = pd.Timestamp(cfg["discovery_start"]), pd.Timestamp(cfg["discovery_end"])
    dates = (opens.index >= start) & (exit_dates <= end).to_numpy()
    valid = np.isfinite(x).all(axis=2) & np.isfinite(y) & eligible.to_numpy()
    def fit(mask):
        good = valid & mask[:, None]
        xx, yy = x[good], y[good]
        if len(yy) < 500: raise ValueError("Insufficient chronologically available training labels")
        penalty = .01
        return np.linalg.solve(xx.T @ xx / len(yy) + penalty * np.eye(len(names)), xx.T @ yy / len(yy)), int(len(yy))
    coef, n = fit(dates)
    coefficients = [{"fit": "all_discovery", "feature": name, "coefficient": float(v)} for name, v in zip(names, coef)]
    midpoint = start + (end - start) / 2
    for label, mask in [("early_discovery", dates & (exit_dates < midpoint).to_numpy()), ("late_discovery", dates & (opens.index >= midpoint))]:
        subset, _ = fit(mask)
        coefficients.extend({"fit": label, "feature": name, "coefficient": float(v)} for name, v in zip(names, subset))
    pred = np.einsum("dnp,p->dn", x, coef)
    pred[~np.isfinite(x).all(axis=2)] = np.nan
    model = {"features": names, "coefficients": coef.tolist(), "penalty": .01, "training_labels": n,
        "train_start": cfg["discovery_start"], "last_allowed_label_exit": cfg["discovery_end"],
        "target": "next open to open six sessions after feature date; five sessions, cross-section demeaned",
        "discovery_status": "in_sample_not_evidence", "validation_status": "frozen_discovery_fit; no refit or tuning"}
    return pd.DataFrame(pred, index=opens.index, columns=opens.columns), pd.DataFrame(coefficients), model


def simulate_close(opens: pd.DataFrame, closes: pd.DataFrame, targets: pd.DataFrame,
                   start: str, end: str, cost_bps: float, cadence=5, intraday=False,
                   buy_hold=False, adv: pd.DataFrame | None = None, capital=500000.,
                   max_adv=.01) -> dict:
    """Close-marked self-financing holdings; previous-close predictions trade next open.

    A holding carries its real gap before any opening rebalance. Cash allowed.
    No rounding, leverage or shorting. No use of an observed gap to obtain its open.
    """
    if not opens.index.equals(closes.index) or not opens.columns.equals(closes.columns): raise ValueError("Price alignment")
    if not targets.index.equals(opens.index) or not targets.columns.equals(opens.columns): raise ValueError("Target alignment")
    if opens.columns.has_duplicates or opens.index.has_duplicates or not opens.index.is_monotonic_increasing: raise ValueError("Unique chronological panel required")
    if not np.isfinite(opens.to_numpy()).all() or not np.isfinite(closes.to_numpy()).all() or (opens <= 0).any().any() or (closes <= 0).any().any(): raise ValueError("Missing/invalid prices")
    if type(cadence) is not int or cadence < 1 or not 0 <= cost_bps < 1000: raise ValueError("Invalid execution settings")
    if not np.isfinite(capital) or capital <= 0 or not 0 < max_adv <= 1: raise ValueError("Invalid capital/liquidity settings")
    if adv is not None and (not adv.index.equals(opens.index) or not adv.columns.equals(opens.columns)):
        raise ValueError("ADV alignment")
    w = targets.to_numpy()
    if not np.isfinite(w).all() or (w < -1e-12).any() or (w.sum(axis=1) > 1 + 1e-10).any(): raise ValueError("Only finite unlevered long-only targets")
    selected = np.flatnonzero((opens.index >= pd.Timestamp(start)) & (opens.index <= pd.Timestamp(end)))
    if not len(selected) or selected[0] == 0: raise ValueError("Warmup required")
    if pd.Timestamp(end) >= pd.Timestamp("2023-01-01") or (opens.index >= pd.Timestamp("2023-01-01")).any(): raise ValueError("Historical holdout sealed")
    op, cl = opens.to_numpy(), closes.to_numpy()
    rate, nav = cost_bps / 10000, 1.
    previous = np.zeros(len(opens.columns))
    rows, holdings, opening_holdings, closing_holdings, trades, contributions = [], [], [], [], [], []
    for j, i in enumerate(selected):
        date, feature_date = opens.index[i], opens.index[i - 1]
        gap = op[i] / cl[i - 1] - 1
        gap_pnl = previous * gap
        gap_factor = 1 + gap_pnl.sum()
        preopen = previous * (1 + gap) / gap_factor
        if j == 0 or (not buy_hold and j % cadence == 0):
            scheduled = w[i - 1].copy()
            decision_date = feature_date
        target = scheduled.copy() if intraday or j == 0 or (not buy_hold and j % cadence == 0) else preopen.copy()
        fee, delta = trading_cost(preopen, target, rate)
        trade_adv = 0.
        if adv is not None:
            liquidity = adv.iloc[i - 1].to_numpy()
            if not np.isfinite(liquidity).all() or (liquidity <= 0).any(): raise ValueError("Invalid ADV")
            trade_adv = float((np.abs(delta) * nav * capital * gap_factor / liquidity).max())
            if trade_adv > max_adv + 1e-10:
                offender=opens.columns[int(np.argmax(np.abs(delta) * nav * capital * gap_factor / liquidity))]
                raise ValueError(f"Liquidity limit exceeded on {date.date()} for {offender}: {trade_adv:.4%} of prior ADV > {max_adv:.2%}; no silent fill/cap")
        day = cl[i] / op[i] - 1
        day_pnl = gap_factor * (1 - fee) * target * day
        day_factor = 1 + target @ day
        factor = gap_factor * (1 - fee) * day_factor
        end_weights = target * (1 + day) / day_factor
        peak_weight = float(max(target.max(), preopen.max(), end_weights.max()))
        dollar_fees = gap_factor * fee
        turnover = gap_factor * np.abs(delta).sum()
        for s, symbol in enumerate(opens.columns):
            if abs(delta[s]) > 1e-12:
                trades.append((date, decision_date, symbol, "open", float(nav * gap_factor * delta[s]), float(nav * gap_factor * abs(delta[s]) * rate)))
            contributions.append((date, symbol, float(nav * gap_pnl[s]), float(nav * day_pnl[s])))
        closing_fee = 0.
        if intraday or j == len(selected) - 1:
            if adv is not None:
                close_adv = float((nav * factor * end_weights * capital / liquidity).max())
                trade_adv = max(trade_adv, close_adv)
                if close_adv > max_adv + 1e-10: raise ValueError(f"Closing liquidity limit exceeded on {date.date()}")
            closing_fee = factor * end_weights.sum() * rate
            turnover += factor * end_weights.sum()
            for s, symbol in enumerate(opens.columns):
                if end_weights[s] > 1e-12:
                    dollars = nav * factor * end_weights[s]
                    trades.append((date, decision_date, symbol, "close", -float(dollars), float(dollars * rate)))
            factor -= closing_fee
            end_weights[:] = 0
        dollar_fees += closing_fee
        rows.append((date, feature_date, nav, nav * factor, factor - 1,
                     float(gap_pnl.sum()), float(day_pnl.sum()), float(dollar_fees),
                     float(turnover), float(target.sum()), peak_weight,
                     float(1 / (target @ target)) if target @ target > 0 else 0,
                     float(preopen.sum()), float(end_weights.sum()), trade_adv))
        holdings.append(target)
        opening_holdings.append(preopen)
        closing_holdings.append(end_weights.copy())
        previous, nav = end_weights, nav * factor
    daily = pd.DataFrame(rows, columns=["date", "feature_date", "nav_before", "nav", "net_return", "overnight_pnl_fraction", "intraday_pnl_fraction", "cost_fraction", "turnover", "gross_exposure", "max_weight", "effective_n", "overnight_exposure", "closing_exposure", "max_trade_adv_fraction"]).set_index("date")
    daily["net_exposure"] = daily.gross_exposure
    daily["gross_return"] = daily.overnight_pnl_fraction + daily.intraday_pnl_fraction
    daily["pnl_per_initial_dollar"] = daily.nav - daily.nav_before
    con = pd.DataFrame(contributions, columns=["date", "symbol", "overnight_pnl", "intraday_pnl"])
    tr = pd.DataFrame(trades, columns=["date", "feature_date", "symbol", "session", "dollars_per_initial_dollar", "fees_per_initial_dollar"])
    if not np.isclose(con.overnight_pnl.sum() + con.intraday_pnl.sum() - tr.fees_per_initial_dollar.sum(), nav - 1, atol=1e-9): raise ArithmeticError("Sleeve accounting does not reconcile")
    return {"daily": daily, "weights": pd.DataFrame(holdings, index=daily.index, columns=opens.columns),
            "opening_weights": pd.DataFrame(opening_holdings, index=daily.index, columns=opens.columns),
            "closing_weights": pd.DataFrame(closing_holdings, index=daily.index, columns=opens.columns),
            "trades": tr, "contributions": con}
