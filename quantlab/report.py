"""Auditable descriptive reports; historical screening is not proof of alpha."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd


def _block_ci(values, cfg: dict, scale: float = 1.0) -> tuple[float, float]:
    """Percentile CI for a mean using circular blocks of chronological dates.

    Missing ICs retain their dates. Paired return differences are formed before
    resampling, and the fixed seed uses the same blocks for matched series.
    """
    x = np.asarray(values, dtype=float)
    if not len(x) or not np.isfinite(x).any():
        return float("nan"), float("nan")
    if np.isinf(x).any():
        raise ValueError("Infinite bootstrap observation")
    n, samples = len(x), int(cfg["bootstrap_samples"])
    block = min(int(cfg["bootstrap_block"]), n)
    if block < 1 or samples < 1:
        raise ValueError("Positive bootstrap block and sample counts required")
    lengths = np.full((n + block - 1) // block, block)
    lengths[-1] = n - block * (len(lengths) - 1)
    starts = np.random.default_rng(cfg["seed"]).integers(0, n, (samples, len(lengths)))
    sums = []
    for a in (np.nan_to_num(x, nan=0.0), np.isfinite(x).astype(float)):
        prefix = np.r_[0.0, np.cumsum(np.r_[a, a[:block - 1]])]
        sums.append((prefix[starts + lengths] - prefix[starts]).sum(axis=1))
    means = np.divide(sums[0], sums[1], out=np.full(samples, np.nan), where=sums[1] > 0)
    return tuple(float(v) for v in np.nanquantile(means * scale, [0.025, 0.975]))


def _metrics(returns: pd.Series) -> dict:
    r = returns.to_numpy(dtype=float)
    if not len(r) or not np.isfinite(r).all() or (r <= -1).any():
        raise ValueError("Metrics require finite, nonempty returns above -100%")
    wealth = np.r_[1.0, np.cumprod(1 + r)]
    vol = float(np.std(r, ddof=1) * np.sqrt(252)) if len(r) > 1 else float("nan")
    return {"n_sessions": len(r), "total_return": wealth[-1] - 1,
            "cagr": float(np.expm1(np.log1p(r).sum() * 252 / len(r))),
            "annual_vol": vol, "sharpe_zero_rf": float(r.mean() * 252 / vol) if vol > 0 else float("nan"),
            "max_drawdown": float((wealth / np.maximum.accumulate(wealth) - 1).min())}


def _matched(daily: pd.DataFrame, other: pd.DataFrame) -> None:
    if not daily.index.equals(other.index):
        raise ValueError("Comparisons require exactly matched chronological decision dates")
    if not daily[["entry_date", "exit_date"]].equals(other[["entry_date", "exit_date"]]):
        raise ValueError("Comparisons require identical execution and outcome dates")


def _paired(row: dict, label: str, left: pd.Series, right: pd.Series, cfg: dict) -> None:
    difference = left - right
    row[f"{label}_annual"] = float(difference.mean() * 252)
    row[f"{label}_ci_low"], row[f"{label}_ci_high"] = _block_ci(difference, cfg, 252)


def _fmt(value: float, percent: bool = False) -> str:
    return "n/a" if not np.isfinite(value) else (f"{value:.2%}" if percent else f"{value:.2f}")


def build_report(run_dir: Path, manifest: dict, results: dict, cfg: dict) -> pd.DataFrame:
    """Save summaries, diagnostics and an equity plot, returning one row/model/split."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    rows, years, sectors, failures = [], [], [], []
    holdout_used = False
    for partition, models in results.items():
        equal = models["equal_weight"]["daily"]
        spy = models["spy_buy_hold"]["daily"]
        for name, simulation in models.items():
            d, stress = simulation["daily"], simulation["stress"]["daily"]
            for other in (equal, spy, stress):
                _matched(d, other)
            if d.index.has_duplicates or not d.index.is_monotonic_increasing:
                raise ValueError("Report observations must be unique and chronological")
            holdout_used |= bool((pd.to_datetime(d.exit_date) >= pd.Timestamp(cfg["holdout_start"])).any())
            ic = simulation["ic"]
            if len(ic) and not ic.index.equals(d.index):
                raise ValueError("IC dates must match portfolio dates exactly")
            row = {"partition": partition, "model": name, "origin": cfg["origins"].get(name, "unspecified"),
                   "first_decision": str(d.index[0].date()), "last_exit": str(pd.Timestamp(d.exit_date.iloc[-1]).date()),
                   **_metrics(d.net_return)}
            _paired(row, "active_equal_weight", d.net_return, equal.net_return, cfg)
            row["stress_cagr"] = _metrics(stress.net_return)["cagr"]
            row["stress_active_equal_weight_annual"] = float(
                (stress.net_return - models["equal_weight"]["stress"]["daily"].net_return).mean() * 252)
            row["mean_ic"] = float(ic.mean()) if len(ic) else float("nan")
            row["ic_dates"] = int(ic.notna().sum())
            row["ic_ci_low"], row["ic_ci_high"] = _block_ci(ic, cfg)
            variance = spy.gross_return.var(ddof=1)
            row["beta_spy"] = float(d.net_return.cov(spy.gross_return) / variance) if variance > 0 else float("nan")
            row["annual_two_sided_turnover"] = float(d.turnover.mean() * 252)
            row["annual_arithmetic_cost_drag"] = float((d.gross_return - d.net_return).mean() * 252)
            row["total_cost_per_initial_dollar"] = float(simulation["trades"].cost_per_initial_dollar.sum())
            row["peak_weight"] = float(d.max_weight.max())
            row["mean_effective_n"] = float(d.effective_n.mean())
            comparator = cfg.get("comparators", {}).get(name)
            row["comparator"] = comparator or ""
            if comparator:
                _matched(d, models[comparator]["daily"])
                _matched(stress, models[comparator]["stress"]["daily"])
                _paired(row, "active_comparator", d.net_return, models[comparator]["daily"].net_return, cfg)
                row["stress_active_comparator_annual"] = float(
                    (stress.net_return - models[comparator]["stress"]["daily"].net_return).mean() * 252)
            rows.append(row)
            for year, part in d.groupby(pd.to_datetime(d.exit_date).dt.year):
                years.append({"partition": partition, "model": name, "year": int(year),
                              **_metrics(part.net_return), "active_equal_weight_annual": float(
                                  (part.net_return - equal.loc[part.index, "net_return"]).mean() * 252),
                              "turnover": float(part.turnover.sum()),
                              "arithmetic_cost_drag": float((part.gross_return - part.net_return).sum())})
            contribution = simulation["contributions"].groupby("symbol").pnl_per_initial_dollar.sum()
            fees = simulation["trades"].groupby("symbol").cost_per_initial_dollar.sum()
            attribution = pd.concat([contribution.rename("gross_pnl_per_initial_dollar"),
                                     fees.rename("cost_per_initial_dollar")], axis=1).fillna(0)
            attribution["net_pnl_per_initial_dollar"] = attribution.iloc[:, 0] - attribution.iloc[:, 1]
            if not np.isclose(attribution.net_pnl_per_initial_dollar.sum(), row["total_return"], atol=1e-10):
                raise ValueError("Sector P&L and trade fees do not reconcile to total return")
            attribution["partition"], attribution["model"] = partition, name
            sectors.append(attribution.reset_index())
            worst = d.copy()
            worst["active_equal_weight"] = d.net_return - equal.net_return
            worst["partition"], worst["model"] = partition, name
            if comparator:
                worst["active_comparator"] = d.net_return - models[comparator]["daily"].net_return
            for criterion in ["net_return", "active_equal_weight"] + (["active_comparator"] if comparator else []):
                selected = worst.nsmallest(10, criterion).copy()
                selected["selection"] = "worst_" + criterion
                failures.append(selected.reset_index())
    summary = pd.DataFrame(rows)
    summary.to_csv(run_dir / "summary.csv", index=False)
    pd.DataFrame(years).to_csv(run_dir / "yearly.csv", index=False)
    pd.concat(sectors, ignore_index=True).to_csv(run_dir / "sector_contributions.csv", index=False)
    pd.concat(failures, ignore_index=True).to_csv(run_dir / "failures.csv", index=False)
    _plot(run_dir, results)
    lines = [f"# Research screen: {manifest['id']}", "",
             f"Created: {manifest.get('created_at_utc', 'unspecified')}. "
             f"Snapshot: {manifest.get('snapshot_id', manifest.get('snapshot', 'unspecified'))}.",
             f"Source SHA256: `{manifest.get('source_sha256', 'unspecified')}`.", "",
             f"Study: {cfg.get('description', cfg.get('study', 'unspecified'))}. "
             f"Rebalance every {cfg.get('rebalance_every', 1)} session(s), anchored at each partition's first entry; "
             "buy-and-hold baselines only enter once. Raw-signal IC still measures the daily prediction, "
             "not the drifted allocation between scheduled trades.",
             f"Earlier ledger records: {manifest.get('ledger_trials_before_run', 0)} model/partition trials. "
             "Reruns and adaptive follow-ups are not independent replications.", "",
             "## Interpretation and limits", "",
             f"Discovery: {cfg['discovery_start']} through {cfg['discovery_end']}; "
             f"validation: {cfg['validation_start']} through {cfg['validation_end']}. "
             + ("Holdout results ARE INCLUDED: this period is no longer an unopened test."
                if holdout_used else f"Holdout from {cfg['holdout_start']} is unopened and excluded from these results."),
             "These are retrospective screening results, not evidence that AI adds alpha. "
             "Chronological validation is not a randomized or independent replication. "
             "The nine sector ETFs are correlated; ETF-days are not independent observations. "
             "Historical patterns may also have informed model ideas before this study.",
             f"All {len(cfg['models'])} predeclared configurations, including baselines, are visible. "
             "Choosing a winner after viewing validation consumes that validation. "
             f"Intervals use {cfg['bootstrap_samples']} circular date-block draws of "
             f"{cfg['bootstrap_block']} sessions (seed {cfg['seed']}); they are exploratory 95% percentile "
             "intervals without multiplicity correction, and do not establish formal alpha.", "",
             "## Timing, accounting and comparisons", "",
             "A signal observed after close t enters at open t+1 and exits/marks at open t+2. "
             "Split boundaries purge outcomes that finish outside the partition. "
             "Each partition starts in cash and includes initial entry and terminal liquidation costs. "
             "Adjusted-open returns are a dividend-reinvestment total-return proxy using revised vendor "
             "history, not historical executable prices or an exact dividend cash ledger. "
             "Cached inputs make this run reproducible; vendor corrections can change later snapshots.",
             f"Base cost is {cfg['cost_bps']:g} bps per traded dollar; stress is {cfg['stress_cost_bps']:g} bps. "
             "Costs cover an assumed combined spread, fees and slippage, not measured opening fills. "
             "Fractional positions, opening liquidity, no market impact and immediate reinvestment are assumptions. "
             "Unlevered long-only sector tilts retain substantial equity-market exposure. "
             "The weight cap applies to target weights at rebalances; holdings can drift beyond it between trades. "
             "Tied scores can shrink active risk, so an interaction-versus-blend gain is not a risk-matched mechanism test.",
             "CAGR and volatility use 252 sessions/year; Sharpe assumes zero risk-free return. "
             "Drawdown includes initial wealth of 1. Active return means 252 times the paired daily "
             "arithmetic net-return difference, not a difference of CAGRs or regression alpha. "
             "SPY beta uses gross SPY returns. Turnover is two-sided traded notional/NAV. "
             "IC is daily cross-sectional rank correlation; its interval resamples dates. "
             "Peak weight and inverse Herfindahl effective holdings describe concentration.", "",
             "## Same-date performance", "",
             "| Split | Model | Net CAGR | Sharpe (RF=0) | Max DD | Active vs EW/year | Active 95% interval | Stress active/year | Turnover/year | IC |",
             "|---|---|---:|---:|---:|---:|---|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['partition']} | {row['model']} | {_fmt(row['cagr'], True)} | "
                     f"{_fmt(row['sharpe_zero_rf'])} | {_fmt(row['max_drawdown'], True)} | "
                     f"{_fmt(row['active_equal_weight_annual'], True)} | "
                     f"[{_fmt(row['active_equal_weight_ci_low'], True)}, {_fmt(row['active_equal_weight_ci_high'], True)}] | "
                     f"{_fmt(row['stress_active_equal_weight_annual'], True)} | "
                     f"{_fmt(row['annual_two_sided_turnover'])} | {_fmt(row['mean_ic'])} |")
    lines += ["", "## Candidate mechanism checks", "",
              "Trend pullback must improve on the additive momentum/reversal blend; volatility-scaled "
              "reversal must improve on ordinary reversal. Compare both with low volatility to detect "
              "a defensive exposure explanation. Positive market returns alone do not support either mechanism.", "",
              "| Split | Candidate | Comparator | Active/year | 95% interval | Stress active/year |",
              "|---|---|---|---:|---|---:|"]
    for row in rows:
        if row["comparator"]:
            lines.append(f"| {row['partition']} | {row['model']} | {row['comparator']} | "
                         f"{_fmt(row['active_comparator_annual'], True)} | "
                         f"[{_fmt(row['active_comparator_ci_low'], True)}, {_fmt(row['active_comparator_ci_high'], True)}] | "
                         f"{_fmt(row['stress_active_comparator_annual'], True)} |")
    lines += ["", "## Inspect the failure evidence", "",
              "`summary.csv` includes volatility, beta, cost drag, IC intervals and concentration. "
              "`yearly.csv` exposes subperiod dependence (partial years use 252-session annualization). "
              "`sector_contributions.csv` reconciles symbol P&L minus symbol trade fees to final profit, "
              "in dollars per initial dollar. `failures.csv` selects the ten worst absolute, equal-weight-relative "
              "and relevant comparator-relative days separately; selection groups can repeat dates. "
              "Per-model daily, weights and trades CSVs contain the detailed historical simulation records. "
              "The equity plot includes the entry value before any returns or fees.", "",
              "![Historical portfolio equity](equity.png)", "",
              "Next decision: inspect validation stability, cost sensitivity and matched ablations before "
              "allocating to a candidate. Explain failures through sector contribution, regime dependence "
              "or turnover before proposing another hypothesis. Register follow-ups as new experiments; "
              "keep the holdout sealed and save timestamped forward predictions before outcomes occur.", ""]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def _plot(run_dir: Path, results: dict) -> None:
    os.environ["MPLCONFIGDIR"] = str(Path(__file__).resolve().parents[1] / ".cache" / "matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(results), 1, figsize=(12, 4 * len(results)), squeeze=False)
    for ax, (partition, models) in zip(axes[:, 0], results.items()):
        for name, simulation in models.items():
            d = simulation["daily"]
            dates = pd.DatetimeIndex([d.entry_date.iloc[0]]).append(pd.DatetimeIndex(d.exit_date))
            ax.plot(dates, np.r_[1.0, d.nav], label=name,
                    linewidth=2 if name == "equal_weight" else 1,
                    color="black" if name == "equal_weight" else None, alpha=0.85)
        ax.set(title=partition, ylabel="Net wealth per initial $1 (log scale)", yscale="log")
        ax.grid(alpha=0.2)
        ax.legend(fontsize=8, ncol=3)
    fig.tight_layout()
    fig.savefig(run_dir / "equity.png", dpi=140)
    plt.close(fig)
