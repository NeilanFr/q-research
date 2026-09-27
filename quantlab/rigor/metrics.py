"""Net metrics and explicit P&L denominators; zero denominators remain undefined."""
from __future__ import annotations

import numpy as np
import pandas as pd


def finite(value):
    return float(value) if np.isfinite(value) else None


def ratio(a, b):
    return float(a/b) if b > 1e-15 else None


def metrics(sim, benchmark, sectors=None):
    d, trades = sim["daily"], sim["roundtrips"]
    r = d.net_return
    nav = (1+r).cumprod()
    drawdown = nav/nav.cummax().clip(lower=1)-1
    cagr = nav.iloc[-1]**(252/len(r))-1
    downside = np.sqrt(np.mean(np.minimum(r, 0)**2))*np.sqrt(252)
    monthly = (1+r).resample("ME").prod()-1
    yearly = (1+r).resample("YE").prod()-1
    result = {"total_return": nav.iloc[-1]-1, "cagr": cagr, "annualized_volatility": r.std()*np.sqrt(252),
              "sharpe": ratio(r.mean()*np.sqrt(252), r.std()), "sortino": ratio(r.mean()*252, downside),
              "max_drawdown": drawdown.min(), "calmar": ratio(cagr, -drawdown.min()),
              "annual_turnover": d.turnover.mean()*252, "exposure": d.gross_exposure.mean(),
              "beta": ratio(r.cov(benchmark.reindex(r.index)), benchmark.reindex(r.index).var()),
              "tail_loss_5pct": r[r <= r.quantile(.05)].mean(), "monthly_consistency": (monthly > 0).mean(),
              "annual_consistency": (yearly > 0).mean(), "fill_count": len(sim["trades"]),
              "trade_count": len(trades), "average_holding_period": None, "hit_rate": None,
              "average_winner": None, "average_loser": None, "profit_factor": None,
              "sector_concentration": None, "sector_classification_status": "no_point_in_time_sector_history"}
    if len(trades):
        winners, losers = trades[trades.pnl > 0], trades[trades.pnl < 0]
        result.update(average_holding_period=float(np.average(trades.holding_sessions, weights=trades.units)),
                      hit_rate=float((trades.pnl > 0).mean()), average_winner=finite(winners["return"].mean()),
                      average_loser=finite(losers["return"].mean()), profit_factor=ratio(winners.pnl.sum(), -losers.pnl.sum()))
    if sectors:
        sector_w = sim["weights"].T.groupby(pd.Series(sectors)).sum().T
        result["sector_concentration"] = float(sector_w.max(axis=1).max())
        result["sector_classification_status"] = "static_analytical_groups_NOT_point_in_time"
    return {k: finite(v) if isinstance(v, (float, np.floating)) else v for k, v in result.items()}


def concentration(sim, regimes, sectors=None):
    con, trades = sim["contributions"], sim["roundtrips"]
    total = float(con.to_numpy().sum())
    result = {"net_pnl": total, "denominator": "cumulative net dollar P&L / initial NAV; null if nonpositive",
              "top_trades": {}, "ticker": con.sum().to_dict(),
              "year": {str(k): float(v) for k, v in con.sum(axis=1).groupby(con.index.year).sum().items()},
              "month": {str(k): float(v) for k, v in con.sum(axis=1).groupby(con.index.to_period('M')).sum().items()},
              "regime": {str(k): float(con.loc[regimes.reindex(con.index) == k].to_numpy().sum()) for k in regimes.unique()}}
    if len(trades):
        wins = trades.pnl.clip(lower=0).sort_values(ascending=False)
        for label, n in [("top_1", 1), ("top_3", 3), ("top_1pct", int(np.ceil(len(trades)*.01))),
                         ("top_5pct", int(np.ceil(len(trades)*.05))), ("top_10pct", int(np.ceil(len(trades)*.1)))]:
            result["top_trades"][label] = {"pnl": float(wins.iloc[:n].sum()), "fraction_net": ratio(wins.iloc[:n].sum(), total),
                                         "fraction_positive": ratio(wins.iloc[:n].sum(), wins.sum()), "count": n}
    result["sector"] = con.sum().groupby(pd.Series(sectors)).sum().to_dict() if sectors else None
    result["moonshot_flag"] = bool(result["top_trades"].get("top_3", {}).get("fraction_net") is not None and result["top_trades"]["top_3"]["fraction_net"] > .5)
    return result
