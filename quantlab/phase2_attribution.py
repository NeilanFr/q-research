"""Reconcile saved sleeve trades into dollar holdings and exposure at each auction."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .data import ROOT,digest,now_utc,save_json


def audit_sleeves(run_id):
    run=ROOT/'runs'/run_id
    cfg=json.loads((run/'config.json').read_text())
    choice=json.loads((run/'parent_selection.json').read_text())
    cap_a,cap_o=cfg['aggressive_capital'],cfg['overnight_capital']
    rate=cfg['cost_bps']/10000
    for part in ['discovery','validation']:
        base=run/'aggressive'/part/choice['aggressive']
        path=run/'portfolio'/part
        out=path/'auction_attribution';out.mkdir(exist_ok=True)
        def read(file): return pd.read_csv(file,index_col=0,parse_dates=True)
        a=read(base/'daily.csv'); o=read(path/'overnight_daily.csv')
        combined=read(path/'combined_daily.csv')
        targets=read(base/'weights.csv'); preopen=read(base/'opening_weights.csv')
        prior_close=read(base/'closing_weights.csv').shift(1).fillna(0)
        trades=pd.read_csv(base/'trades.csv',parse_dates=['date'])
        contributions=pd.read_csv(base/'contributions.csv',parse_dates=['date'])
        day_pnl=contributions.pivot(index='date',columns='symbol',values='intraday_pnl').reindex(columns=targets.columns)*cap_a
        a_start=a.nav_before*cap_a; o_start=o.nav_before*cap_o
        entry_fee=o.exposure*rate/(1+o.exposure*rate)
        night_entry=o_start*(1-entry_fee)*o.exposure
        night_cash=o_start*(1-entry_fee)*(1-o.exposure)
        a_before_open=a_start*(1+a.overnight_pnl_fraction)
        night_before_exit=night_entry*(1+o.asset_overnight_return)
        fees={session:trades[trades.session==session].groupby('date').fees_per_initial_dollar.sum().reindex(a.index,fill_value=0)*cap_a for session in ['open','close']}
        a_after_open=a_before_open-fees['open']
        open_dollars=targets.mul(a_after_open,axis=0)
        close_dollars=open_dollars+day_pnl
        old_dollars=prior_close.mul(a_start,axis=0)
        preopen_dollars=preopen.mul(a_before_open,axis=0)
        regular_nav=a_after_open+o.nav*cap_o
        night_nav=a_start+night_entry+night_cash
        before_exit_nav=a_before_open+night_before_exit+night_cash
        preclose_nav=a.nav*cap_a+fees['close']+o.nav*cap_o
        d=combined[['aggressive_start','aggressive_value','aggressive_pnl','overnight_start','overnight_value','overnight_pnl','start_value','equity','net_return','drawdown','aggressive_contribution','overnight_contribution']].copy()
        d['aggressive_gap_pnl']=a_start*a.overnight_pnl_fraction
        d['aggressive_regular_pnl']=day_pnl.sum(axis=1)
        d['aggressive_fees']=fees['open']+fees['close']
        d['overnight_gross_pnl']=o.gross_pnl_per_initial_dollar*cap_o
        d['overnight_fees']=o.cost_per_initial_dollar*cap_o
        d['night_entry_gross']=(old_dollars.sum(axis=1)+night_entry)/night_nav
        d['before_open_exit_gross']=(preopen_dollars.sum(axis=1)+night_before_exit)/before_exit_nav
        d['regular_open_gross']=open_dollars.sum(axis=1)/regular_nav
        d['before_close_exit_gross']=close_dollars.sum(axis=1)/preclose_nav
        exposure_columns=['night_entry_gross','before_open_exit_gross','regular_open_gross','before_close_exit_gross']
        d['max_marked_gross']=d[exposure_columns].max(axis=1)
        d['max_marked_net']=d.max_marked_gross
        d['max_identifier_weight']=pd.concat([
            old_dollars.max(axis=1)/night_nav,night_entry/night_nav,
            preopen_dollars.max(axis=1)/before_exit_nav,night_before_exit/before_exit_nav,
            open_dollars.max(axis=1)/regular_nav,close_dollars.max(axis=1)/preclose_nav],axis=1).max(axis=1)
        if not np.allclose(d.aggressive_gap_pnl+d.aggressive_regular_pnl-d.aggressive_fees,d.aggressive_pnl,atol=1e-6):
            raise ArithmeticError('Aggressive dollar attribution failed')
        if not np.allclose(d.overnight_gross_pnl-d.overnight_fees,d.overnight_pnl,atol=1e-6):
            raise ArithmeticError('Overnight dollar attribution failed')
        if (d.max_marked_gross>1+1e-10).any() or (open_dollars<-1e-6).any().any():
            raise ArithmeticError('Unlevered holdings invariant failed')
        d.to_csv(out/'daily_dollars_and_exposure.csv')
        open_dollars.to_csv(out/'aggressive_open_holdings_dollars.csv')
        close_dollars.to_csv(out/'aggressive_preclose_holdings_dollars.csv')
        pd.DataFrame({'symbol':o.symbol,'entry_stock_dollars':night_entry,'entry_cash_dollars':night_cash,
                      'preexit_stock_dollars':night_before_exit,'end_cash_dollars':o.nav*cap_o}).to_csv(out/'overnight_holdings_dollars.csv')
        save_json(out/'audit.json',{'at_utc':now_utc(),'code_sha256':digest(Path(__file__).read_bytes()),
                  'input_sha256':{str(p.relative_to(run)):digest(p.read_bytes()) for p in [base/'daily.csv',base/'weights.csv',base/'trades.csv',base/'contributions.csv',path/'overnight_daily.csv']},
                  'max_reconciliation_error_dollars':float(max((d.aggressive_gap_pnl+d.aggressive_regular_pnl-d.aggressive_fees-d.aggressive_pnl).abs().max(),(d.overnight_gross_pnl-d.overnight_fees-d.overnight_pnl).abs().max())),
                  'exposure_note':'Exact fractional-share accounting at modeled auction marks; no observation of intraday extremes, executable quotes, or ETF look-through overlap.'})
    return run/'portfolio'
