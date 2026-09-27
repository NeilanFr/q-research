"""A shares-and-cash research simulator, isolated from every broker adapter.

Adjusted units are total-return proxies, not broker shares. Order quantities are
fixed at prior close. Costs and affordability can reduce fills, never enlarge an
order after seeing its opening price. All output is explicitly simulated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantlab.data import calendar


def decision_times(sessions):
    cal = calendar(str(sessions[0].date()), str((sessions[-1]+pd.Timedelta(days=10)).date()))
    if not sessions.equals(cal.sessions_in_range(sessions[0], sessions[-1])):
        raise ValueError("Missing/extra exchange sessions; no implicit fills")
    schedule = cal.schedule
    next_sessions = [cal.next_session(d) for d in sessions]
    return pd.DataFrame({"source_timestamp": schedule.loc[sessions, "close"].to_numpy(),
                         "known_at": (schedule.loc[sessions, "close"]+pd.Timedelta(hours=1)).to_numpy(),
                         "decision_at": (schedule.loc[sessions, "close"]+pd.Timedelta(hours=1, seconds=1)).to_numpy(),
                         "earliest_execution_at": schedule.loc[next_sessions, "open"].to_numpy()}, index=sessions)


def intended_order(weights, units, cash, reference_close, adv, capital, max_adv=.01):
    arrays = [np.asarray(a, float) for a in (weights, units, reference_close, adv)]
    w, q, price, liquidity = arrays
    if any(a.shape != w.shape for a in arrays) or any(not np.isfinite(a).all() for a in arrays):
        raise ValueError("Finite aligned order inputs required")
    if (w < 0).any() or w.sum() > 1+1e-12 or (q < 0).any() or (price <= 0).any() or (liquidity <= 0).any() or cash < -1e-10:
        raise ValueError("Invalid long-only order state")
    nav = cash+q@price
    delta = w*nav/price-q
    cap = liquidity*max_adv/capital/price
    return np.clip(delta, -cap, cap)


def simulate(panel, targets, start, end, settings, *, delay=0, cadence=5, offset=0,
             cost_multiple=1., added_slippage_bps=0., impact_multiple=1., miss_every=0,
             fill_at_close=False, buy_hold=False, detailed=True):
    op, cl, adv = [panel[k] for k in ("opens", "closes", "adv")]
    if not op.index.equals(cl.index) or not op.columns.equals(cl.columns) or not targets.index.equals(op.index) or not targets.columns.equals(op.columns):
        raise ValueError("Exact price/target alignment required")
    if not adv.index.equals(op.index) or not adv.columns.equals(op.columns):
        raise ValueError("ADV alignment")
    if type(delay) is not int or delay < 0 or type(cadence) is not int or cadence < 1 or not 0 <= offset < cadence:
        raise ValueError("Invalid causal delay/cadence")
    if not np.isfinite(targets.to_numpy()).all() or (targets < 0).any().any() or (targets.sum(axis=1) > 1+1e-10).any():
        raise ValueError("Finite unlevered targets required")
    clock = panel.get("clock")
    if clock is None:
        clock = decision_times(op.index)
    chosen = np.flatnonzero((op.index >= start) & (op.index <= end))
    if not len(chosen) or chosen[0] < delay+1:
        raise ValueError("Evaluation requires completed warmup")
    expected = calendar(start, end).sessions_in_range(start, end)
    if not op.index[chosen].equals(expected):
        raise ValueError("Evaluation session coverage incomplete; no shortened terminal liquidation")
    if not np.isfinite(op.iloc[chosen]).all().all() or not np.isfinite(cl.iloc[chosen]).all().all() or (op.iloc[chosen] <= 0).any().any() or (cl.iloc[chosen] <= 0).any().any():
        raise ValueError("Missing/invalid prices or halt: stop, never fill forward")
    if min(cost_multiple, impact_multiple, added_slippage_bps) < 0 or miss_every < 0:
        raise ValueError("Invalid execution stress")
    capital = settings["reference_notional_usd"]
    base_bps = sum(settings[k] for k in ("half_spread_bps", "commission_bps", "slippage_bps"))
    units, cash, previous_nav = np.zeros(op.shape[1]), 1., 1.
    lots = [[] for _ in op.columns]
    trades, roundtrips, rows, weights, contributions = [], [], [], [], []
    operations = 0
    def transact(delta, prices, liquidity, day, decision, session):
        nonlocal units, cash
        if session != "terminal_close":
            # Prior ADV and frozen quantities constrain actual opening participation.
            delta = np.clip(delta, -liquidity*settings["max_adv_fraction"]/capital/prices,
                            liquidity*settings["max_adv_fraction"]/capital/prices)
        rates = (base_bps*cost_multiple+added_slippage_bps + settings["impact_bps_at_one_percent"]*impact_multiple*np.sqrt(np.abs(delta)*prices*capital/liquidity/.01))/10000
        sells = np.minimum(np.maximum(-delta, 0), units)
        buys = np.maximum(delta, 0)
        proceeds = sells*prices*(1-rates)
        budget = cash+proceeds.sum()
        spend = (buys*prices*(1+rates)).sum()
        # Reduce unaffordable quantities; rates remain at conservative intended size.
        if spend > budget:
            buys *= max(budget, 0)/spend
        actual = buys-sells
        fee = np.abs(actual)*prices*rates
        cash -= actual@prices+fee.sum()
        units += actual
        if cash < -1e-9 or (units < -1e-10).any():
            raise ArithmeticError("Cash/position invariant failed")
        if detailed:
            for j in np.flatnonzero(np.abs(actual) > 1e-12):
                quantity = actual[j]
                trades.append({"date": day, "decision_date": decision, "symbol": op.columns[j],
                               "session": session, "units": float(quantity), "price_proxy": float(prices[j]),
                               "dollars_per_initial_dollar": float(quantity*prices[j]), "fee": float(fee[j]),
                               "simulated": True})
                if quantity > 0:
                    lots[j].append([quantity, prices[j]*(1+rates[j]), day])
                else:
                    remaining = -quantity
                    while remaining > 1e-12 and lots[j]:
                        lot = lots[j][0]
                        n = min(remaining, lot[0])
                        pnl = n*(prices[j]*(1-rates[j])-lot[1])
                        roundtrips.append({"symbol": op.columns[j], "entry": lot[2], "exit": day,
                                          "units": n, "pnl": pnl, "return": pnl/(n*lot[1]),
                                          "holding_sessions": int(op.index.get_loc(day)-op.index.get_loc(lot[2]))})
                        remaining -= n
                        lot[0] -= n
                        if lot[0] <= 1e-12:
                            lots[j].pop(0)
        return fee, np.abs(actual)*prices
    opv, clv, advv, wv = op.to_numpy(), cl.to_numpy(), adv.to_numpy(), targets.to_numpy()
    pending = {}
    for step, i in enumerate(chosen):
        day = op.index[i]
        before_units = units.copy()
        pnl = before_units*(clv[i]-clv[i-1])
        fees, turnover = np.zeros(op.shape[1]), np.zeros(op.shape[1])
        # Cadence is based on execution sessions, never on observed outcomes.
        schedule = (step >= offset and (step-offset) % cadence == 0 and (not buy_hold or step == offset))
        if schedule:
            decision_i = i-1
            delta = intended_order(wv[decision_i], units, cash, clv[decision_i], advv[decision_i], capital, settings["max_adv_fraction"])
            pending[i+delay] = (delta, decision_i, advv[decision_i].copy())
        if i in pending:
            delta, decision_i, liquidity = pending.pop(i)
            if pd.Timestamp(clock.iloc[decision_i].decision_at) >= pd.Timestamp(clock.iloc[i-1].earliest_execution_at):
                raise ValueError("Same-bar or future-information execution")
            operations += 1
            if not miss_every or operations % miss_every:
                price = clv[i] if fill_at_close else opv[i]
                delta = np.maximum(delta, -units)  # A delayed sell cannot become a short.
                old = units.copy()
                fees, turnover = transact(delta, price, liquidity, day, op.index[decision_i], "close" if fill_at_close else "open")
                pnl += (units-old)*(clv[i]-price)
        nav = cash+units@clv[i]
        current_weights = units*clv[i]/nav
        if step == len(chosen)-1:
            # Unconditional predeclared terminal close liquidation, no close-derived signal.
            f, t = transact(-units.copy(), clv[i], advv[i-1], day, op.index[i-1], "terminal_close")
            fees += f
            turnover += t
            nav = cash
        contribution = pnl-fees
        if not np.isclose(contribution.sum(), nav-previous_nav, atol=1e-10):
            raise ArithmeticError("Dollar attribution does not reconcile")
        rows.append({"date": day, "nav_before": previous_nav, "nav": nav,
                     "net_return": nav/previous_nav-1, "turnover": turnover.sum()/previous_nav,
                     "cost_fraction": fees.sum()/previous_nav, "gross_exposure": current_weights.sum()})
        weights.append(current_weights)
        contributions.append(contribution)
        previous_nav = nav
    daily = pd.DataFrame(rows).set_index("date")
    return {"daily": daily, "weights": pd.DataFrame(weights, index=daily.index, columns=op.columns),
            "contributions": pd.DataFrame(contributions, index=daily.index, columns=op.columns),
            "trades": pd.DataFrame(trades), "roundtrips": pd.DataFrame(roundtrips),
            "pending_unfilled": len(pending)}
