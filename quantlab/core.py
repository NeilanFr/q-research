"""Temporal alignment, prespecified signals, and self-financing accounting."""
from __future__ import annotations

import numpy as np
import pandas as pd


def centered_rank(frame: pd.DataFrame) -> pd.DataFrame:
    """Ties share ranks; no ticker-order tiebreaks. Rows sum to zero."""
    n = frame.shape[1]
    if n < 2:
        raise ValueError("Cross-sectional signals need at least two instruments")
    return (frame.rank(axis=1, method="average") - (n + 1) / 2) / ((n - 1) / 2)


def make_signals(close: pd.DataFrame, cfg: dict) -> dict[str, pd.DataFrame]:
    """At row t, every input has timestamp <= close[t]; no targets enter here."""
    for name, minimum in [("momentum_sessions", 1), ("reversal_sessions", 1), ("volatility_sessions", 2)]:
        if type(cfg[name]) is not int or cfg[name] < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if close.columns.has_duplicates:
        raise ValueError("Duplicate symbols")
    if not close.index.is_monotonic_increasing or close.index.has_duplicates:
        raise ValueError("Prices must have unique chronological sessions")
    if not np.isfinite(close.to_numpy()).all() or (close <= 0).any().any():
        raise ValueError("Missing/nonpositive prices are not permitted")
    returns = close.pct_change(fill_method=None)
    momentum = close / close.shift(cfg["momentum_sessions"]) - 1
    reversal = -(close / close.shift(cfg["reversal_sessions"]) - 1)
    vol = returns.rolling(cfg["volatility_sessions"]).std(ddof=1)
    residual = returns.sub(returns.mean(axis=1), axis=0)
    residual_vol = residual.rolling(cfg["volatility_sessions"]).std(ddof=1)
    m, r = centered_rank(momentum), centered_rank(reversal)
    ready = momentum.notna().all(axis=1) & reversal.notna().all(axis=1) & residual_vol.gt(0).all(axis=1)
    signals = {
        "equal_weight": close * 0,
        "equal_buy_hold": close * 0,
        "momentum": momentum,
        "reversal": reversal,
        "low_vol": -vol,
        "mom_rev_blend": (m + r) / 2,
        # Interaction: only established relative winners with recent pullbacks.
        # Compare to the additive blend; no thresholds were optimized.
        "trend_pullback": m.clip(lower=0) * r.clip(lower=0),
        "vol_scaled_reversal": reversal.sub(reversal.mean(axis=1), axis=0)
        / (residual_vol * np.sqrt(cfg["reversal_sessions"])),
    }
    return {name: frame.loc[ready] for name, frame in signals.items()}


def score_weights(scores: pd.DataFrame, tilt: float, cap: float) -> pd.DataFrame:
    if not 0 <= tilt <= 1 or not 0 < cap <= 1:
        raise ValueError("Invalid long-only tilt or weight cap")
    if not np.isfinite(scores.to_numpy()).all():
        raise ValueError("Cannot allocate from missing/nonfinite scores")
    w = (1 + tilt * centered_rank(scores)) / scores.shape[1]
    if (w > cap + 1e-12).any().any():
        raise ValueError("Requested tilt exceeds the cap; reduce tilt or expand universe")
    return w


def make_targets(opens: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Decision close t -> entry open t+1 -> exit open t+2 (one session)."""
    if opens.columns.has_duplicates or opens.index.has_duplicates or not opens.index.is_monotonic_increasing:
        raise ValueError("Opens must be unique and chronological")
    if not np.isfinite(opens.to_numpy()).all() or (opens <= 0).any().any():
        raise ValueError("Missing/nonpositive opens")
    y = opens.shift(-2) / opens.shift(-1) - 1
    dates = pd.Series(opens.index, index=opens.index)
    timing = pd.DataFrame({"entry_date": dates.shift(-1), "exit_date": dates.shift(-2)})
    return y, timing


def partition_dates(timing: pd.DataFrame, start: str, end: str) -> pd.Index:
    """Purge incomplete outcomes and outcomes crossing a partition boundary."""
    return timing.index[(timing.index >= pd.Timestamp(start))
                        & (timing.index <= pd.Timestamp(end))
                        & timing.exit_date.notna()
                        & (timing.exit_date <= pd.Timestamp(end))]


def trading_cost(previous: np.ndarray, target: np.ndarray, rate: float) -> tuple[float, np.ndarray]:
    """Solve cost = rate * sum(abs((1-cost)*target - previous)).

    Weights are relative to pretrade NAV for previous and postcost NAV for target.
    This avoids spending 100% of capital on assets and borrowing to pay fees.
    """
    if not 0 <= rate < 0.1:
        raise ValueError("Cost rate must be in [0, 0.1)")
    cost = 0.0
    for _ in range(100):
        new = rate * np.abs((1 - cost) * target - previous).sum()
        if abs(new - cost) < 1e-14:
            cost = new
            break
        cost = new
    else:
        raise ArithmeticError("Cost solver did not converge")
    return float(cost), (1 - cost) * target - previous


def simulate(weights: pd.DataFrame, returns: pd.DataFrame, timing: pd.DataFrame,
             cost_bps: float, *, buy_hold: bool = False, liquidate: bool = True,
             rebalance_every: int = 1) -> dict:
    """Fractional total-return units, daily opening execution, no leverage.

    Orders/attribution here are backtest records, never actual broker fills.
    Terminal liquidation is charged for every historical partition.
    """
    if not weights.index.equals(returns.index) or not weights.columns.equals(returns.columns):
        raise ValueError("Weight/return alignment must be exact")
    if weights.columns.has_duplicates or weights.index.has_duplicates or not weights.index.is_monotonic_increasing:
        raise ValueError("Unique symbols and chronological decisions are required")
    if len(weights) == 0 or not weights.index.equals(timing.index):
        raise ValueError("Empty or misaligned timing")
    arrays = [weights.to_numpy(), returns.to_numpy()]
    if any(not np.isfinite(a).all() for a in arrays):
        raise ValueError("Nonfinite weights/returns")
    if (arrays[0] < -1e-12).any() or not np.allclose(arrays[0].sum(axis=1), 1):
        raise ValueError("This engine requires unlevered fully invested long-only targets")
    if (arrays[1] <= -1).any():
        raise ValueError("Returns <= -100% are invalid")
    if type(rebalance_every) is not int or rebalance_every < 1:
        raise ValueError("Rebalance cadence must be a positive integer")
    if not ((timing.index < timing.entry_date) & (timing.entry_date < timing.exit_date)).all():
        raise ValueError("Decision must precede entry, which must precede exit")
    if len(timing) > 1 and not np.array_equal(timing.exit_date.iloc[:-1].values,
                                              timing.entry_date.iloc[1:].values):
        raise ValueError("Holding periods must be contiguous; missing sessions are not free cash")
    rate, nav = cost_bps / 10000, 1.0
    previous = np.zeros(weights.shape[1])
    daily, trades, contributions, actual = [], [], [], []
    for i, (date, row) in enumerate(weights.iterrows()):
        hold = i > 0 and (buy_hold or i % rebalance_every != 0)
        target = previous.copy() if hold else row.to_numpy().copy()
        rr = returns.loc[date].to_numpy()
        cost, delta = trading_cost(previous, target, rate)
        gross = float(target @ rr)
        factor = (1 - cost) * (1 + gross)
        drifted = target * (1 + rr) / (1 + gross)
        total_cost, turnover = cost, float(np.abs(delta).sum())
        entry, exit_ = timing.loc[date, ["entry_date", "exit_date"]]
        for j, symbol in enumerate(weights.columns):
            trades.append((date, entry, symbol, delta[j] * nav, abs(delta[j]) * nav * rate, "rebalance"))
            contributions.append((date, symbol, nav * (1 - cost) * target[j] * rr[j]))
        if liquidate and i == len(weights) - 1:
            for j, symbol in enumerate(weights.columns):
                dollars = -nav * factor * drifted[j]
                trades.append((date, exit_, symbol, dollars, abs(dollars) * rate, "liquidation"))
            total_cost += factor * rate
            turnover += factor
            factor *= 1 - rate
        daily.append((date, entry, exit_, gross, factor - 1, total_cost,
                      turnover, nav, nav * factor, float(target.max()),
                      float(1 / (target @ target))))
        actual.append(target)
        previous, nav = drifted, nav * factor
    d = pd.DataFrame(daily, columns=["decision_date", "entry_date", "exit_date", "gross_return",
        "net_return", "cost_fraction", "turnover", "nav_before", "nav", "max_weight", "effective_n"]).set_index("decision_date")
    return {
        "daily": d,
        "trades": pd.DataFrame(trades, columns=["decision_date", "execution_date", "symbol", "dollars_per_initial_dollar", "cost_per_initial_dollar", "kind"]),
        "contributions": pd.DataFrame(contributions, columns=["decision_date", "symbol", "pnl_per_initial_dollar"]),
        "weights": pd.DataFrame(actual, index=weights.index, columns=weights.columns),
    }
