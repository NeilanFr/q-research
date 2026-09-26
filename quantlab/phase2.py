"""Two-stage research screen. Discovery selections are persisted before validation."""
from __future__ import annotations

import json
import os
import platform
import traceback
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

from .aggressive import feature_panel, ranked, ridge_score, simulate_close, top_weights
from .data import ROOT, calendar, digest, load_snapshot, now_utc, save_json, wide
from .experiment import connect_db, event, rank_ic, source_archive
from .overnight import run_overnight
from .report import _block_ci, _metrics


def historical_panel(bars, cfg, universe):
    """Membership checks use only the declared historical interval, never 2023+."""
    if pd.Timestamp(cfg['validation_end']) >= pd.Timestamp('2023-01-01'):
        raise ValueError('Historical holdout sealed')
    if not (pd.Timestamp(cfg['data_start']) < pd.Timestamp(cfg['discovery_start']) <
            pd.Timestamp(cfg['discovery_end']) < pd.Timestamp(cfg['validation_start']) <
            pd.Timestamp(cfg['validation_end'])):
        raise ValueError('Invalid chronological partitions')
    b = bars[(bars.date >= cfg['data_start']) & (bars.date <= cfg['validation_end'])].copy()
    expected = calendar(cfg['data_start'], cfg['validation_end']).sessions_in_range(cfg['data_start'], cfg['validation_end'])
    records, accepted = [], []
    for record in universe['records']:
        symbol = record.get('symbol')
        sub = b[b.symbol == symbol].sort_values('date')
        reason = ''
        if not record['research_eligible']:
            reason = 'historical identity unresolved/unavailable: ' + record.get('lineage_note', '')
        elif not pd.DatetimeIndex(sub.date).equals(expected):
            reason = 'missing or incomplete historical session coverage; no fabricated delisting returns'
        elif not np.isfinite(sub[['open','close','adj_open','adj_close','volume']]).all().all():
            reason = 'invalid historical price/volume'
        if not reason:
            accepted.append(symbol)
        records.append({**record, 'included': not bool(reason), 'exclusion': reason, 'historical_bars': len(sub)})
    if len(accepted) < 20 or len(set(accepted)) != len(accepted):
        raise ValueError('Insufficient or duplicated stock identities')
    for symbol in cfg['overnight_symbols']:
        if not pd.DatetimeIndex(b[b.symbol == symbol].sort_values('date').date).equals(expected):
            raise ValueError('Incomplete ETF: ' + symbol)
    # All later features and outcomes receive this trimmed, explicitly screened panel.
    return b[b.symbol.isin(accepted + cfg['overnight_symbols'])], sorted(accepted), records


def window_stats(returns, horizon=20):
    r = np.asarray(returns, float)
    if not np.isfinite(r).all() or (r <= -1).any() or len(r) < horizon:
        raise ValueError('Need valid contiguous returns for window statistics')
    windows = np.lib.stride_tricks.sliding_window_view(r, horizon)
    wealth = np.column_stack([np.ones(len(windows)), np.cumprod(1 + windows, axis=1)])
    outcomes = wealth[:, -1] - 1
    drawdowns = (wealth / np.maximum.accumulate(wealth, axis=1) - 1).min(axis=1)
    row = {'windows_20': len(outcomes), 'mean_20': float(outcomes.mean()),
           'median_20': float(np.median(outcomes)), 'skew_20': float(pd.Series(outcomes).skew()),
           'worst_20': float(outcomes.min()), 'worst_20_drawdown': float(drawdowns.min()),
           'prob_20_drawdown_below_minus10': float((drawdowns < -.10).mean())}
    for q in (75, 90, 95): row[f'p{q}_20'] = float(np.percentile(outcomes, q))
    for value in (.05, .10, .20): row[f'prob_20_above_{int(value*100)}'] = float((outcomes > value).mean())
    for value in (.05, .10): row[f'prob_20_below_minus{int(value*100)}'] = float((outcomes < -value).mean())
    return row


def combine_sleeves(aggressive, overnight, a_capital=500000., o_capital=500000.):
    a, o = aggressive['daily'], overnight['daily']
    if not a.index.equals(o.index): raise ValueError('Sleeve dates must match exactly')
    if a_capital <= 0 or o_capital <= 0: raise ValueError('Positive sleeve capital required')
    d = pd.DataFrame(index=a.index)
    for name, source, capital in [('aggressive', a, a_capital), ('overnight', o, o_capital)]:
        d[name+'_start'] = source.nav_before * capital
        d[name+'_value'] = source.nav * capital
        d[name+'_pnl'] = d[name+'_value'] - d[name+'_start']
    d['start_value'] = d.aggressive_start + d.overnight_start
    d['equity'] = d.aggressive_value + d.overnight_value
    d['net_return'] = d.equity / d.start_value - 1
    d['aggressive_contribution'] = d.aggressive_pnl / d.start_value
    d['overnight_contribution'] = d.overnight_pnl / d.start_value
    a_share, o_share = d.aggressive_start / d.start_value, d.overnight_start / d.start_value
    d['regular_gross_exposure'] = a_share * a.gross_exposure
    d['overnight_gross_exposure'] = a_share * a.overnight_exposure + o_share * o.exposure
    d['gross_exposure'] = d[['regular_gross_exposure','overnight_gross_exposure']].max(axis=1)
    d['net_exposure'] = d.gross_exposure
    # Stock and ETF identifiers are disjoint; underlying economic overlap is not removed.
    d['max_identifier_weight'] = np.maximum(a_share * a.max_weight, o_share * o.exposure)
    wealth = np.r_[a_capital+o_capital, d.equity]
    d['drawdown'] = (wealth / np.maximum.accumulate(wealth)-1)[1:]
    if not np.allclose(d.net_return, d.aggressive_contribution+d.overnight_contribution):
        raise ArithmeticError('Combined attribution mismatch')
    if not np.allclose(d.start_value.iloc[1:], d.equity.iloc[:-1]):
        raise ArithmeticError('Sleeve capital was reset inside partition')
    return d


def hypothesis_scores(features, eligible, hypothesis):
    if hypothesis['rule'] == 'momentum_quality_pullback':
        # Registered after inspecting discovery. No adjustable hyperparameter search.
        score = ranked(features['momentum'], eligible) + ranked(features['reversal'], eligible)
        return score.where(features['momentum'] > 0)
    if hypothesis['rule'] == 'momentum_low_vol':
        return ranked(features['momentum'], eligible) - ranked(features['volatility'], eligible)
    raise ValueError('Unknown deterministic hypothesis')


def _save_sim(result, path):
    path.mkdir(parents=True, exist_ok=True)
    for key, value in result.items():
        if isinstance(value, pd.DataFrame): value.to_csv(path / (key+'.csv'), index=key not in {'trades','contributions'})


def _summary(part, name, sim, stress, equal, spy, cfg):
    d = sim['daily']
    if not d.index.equals(equal['daily'].index) or not d.index.equals(spy['daily'].index):
        raise ValueError('Unmatched comparison dates')
    diff = d.net_return-equal['daily'].net_return
    lo, hi = _block_ci(diff, cfg, 252)
    row = {'partition': part, 'model': name, **_metrics(d.net_return), **window_stats(d.net_return),
           'active_equal_annual': float(diff.mean()*252), 'active_equal_ci_low': lo, 'active_equal_ci_high': hi,
           'stress_cagr': _metrics(stress['daily'].net_return)['cagr'],
           'beta_spy': float(d.net_return.cov(spy['daily'].net_return)/spy['daily'].net_return.var()),
           'annual_turnover': float(d.turnover.mean()*252), 'annual_cost': float(d.cost_fraction.mean()*252),
           'peak_weight': float(d.max_weight.max()), 'mean_effective_n': float(d.effective_n.mean()),
           'mean_gross_exposure': float(d.gross_exposure.mean()), 'mean_net_exposure': float(d.net_exposure.mean()),
           'max_trade_adv_fraction': float(d.max_trade_adv_fraction.max()),
           'evidence': 'availability-biased historical screen; reused validation; unadjusted intervals'}
    if name == 'ridge_rank' and part == 'discovery': row['evidence'] += '; IN-SAMPLE FIT, not predictive evidence'
    return row


def run_phase2(cfg, snapshot=None, parent=None, hypothesis_path=None):
    """Without parent: discovery only. With parent: frozen selections plus validation."""
    run_id = pd.Timestamp(now_utc()).strftime('%Y%m%dT%H%M%S') + '_phase2_' + uuid4().hex[:6]
    out = ROOT / 'runs' / run_id
    out.mkdir(parents=True)
    db = connect_db()
    db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)', (run_id, now_utc(), json.dumps(cfg,sort_keys=True), snapshot, None, 'started', None))
    db.commit()
    current = None
    try:
        if cfg['shorting_enabled'] or cfg['leverage'] != 1: raise ValueError('Only long-only unlevered research is authorized')
        if cfg['cost_bps'] > cfg['stress_cost_bps']: raise ValueError('Invalid cost stress')
        save_json(out/'config.json', cfg)
        source = source_archive(out/'source')
        (out/'protocol.md').write_text((ROOT/'docs/PHASE2_PROTOCOL.md').read_text(encoding='utf-8'),encoding='utf-8')
        sid = snapshot or (ROOT/'data/PHASE2_LATEST').read_text().strip()
        bars, manifest, _ = load_snapshot(sid)
        universe = manifest['universe']
        bars, stocks, exclusions = historical_panel(bars, cfg, universe)
        save_json(out/'universe_audit.json', {'records': exclusions, 'stocks': stocks, 'evidence_cap': universe['evidence_cap']})
        pd.DataFrame(exclusions).to_csv(out/'universe_audit.csv', index=False)
        save_json(out/'data_manifest.json', manifest)
        save_json(out/'manifest.json', {'id':run_id,'created_at_utc':now_utc(),'snapshot':sid,'source_sha256':source,
                  'python':platform.python_version(),'holdout_opened':False,'parent_discovery':parent,
                  'ledger_trials_before_run':db.execute('SELECT COUNT(*) FROM trials').fetchone()[0]})
        db.execute('UPDATE runs SET snapshot=?,source_sha256=? WHERE id=?',(sid,source,run_id))
        db.commit()
        selection = None
        if parent:
            pp = ROOT/'runs'/parent
            pm = json.loads((pp/'manifest.json').read_text())
            if pm['snapshot'] != sid or json.loads((pp/'config.json').read_text()) != cfg:
                raise ValueError('Discovery parent must use identical data/config')
            selection = json.loads((pp/'selection.json').read_text())
            recorded=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE kind='phase2_discovery_selection'").fetchall()]
            if not any(r == selection|{'run':parent} for r in recorded):
                raise ValueError('Discovery selection differs from its original ledger record')
            save_json(out/'parent_selection.json', selection)
        parts = ('discovery','validation') if parent else ('discovery',)
        features, eligible = feature_panel(bars, stocks, cfg)
        opens, closes = [wide(bars, field, stocks) for field in ('adj_open','adj_close')]
        adv = (wide(bars,'close',stocks)*wide(bars,'volume',stocks)).rolling(20).mean()
        scores = {name: features[name] for name in cfg['models'] if name not in {'equal_weight','ridge_rank'}}
        scores['equal_weight'] = closes*0
        scores['ridge_rank'], coefficients, fitted = ridge_score(features, eligible, opens, cfg)
        coefficients.to_csv(out/'ridge_coefficients.csv',index=False)
        save_json(out/'ridge_model.json',fitted)
        specs = {name: (name, None if name=='equal_weight' else cfg['top_k']) for name in cfg['models']}
        if parent:
            for rule in sorted({selection['aggressive'], 'momentum'}):
                specs[rule+'_top5'] = (rule, cfg['concentrated_top_k'])
        hypothesis = None
        if hypothesis_path:
            if not parent: raise ValueError('AI follow-up requires completed discovery parent')
            hypothesis = json.loads(Path(hypothesis_path).read_text(encoding='utf-8'))
            if hypothesis['discovery_run'] != parent: raise ValueError('Wrong hypothesis provenance')
            scores[hypothesis['rule']] = hypothesis_scores(features,eligible,hypothesis)
            specs[hypothesis['rule']] = (hypothesis['rule'],cfg['top_k'])
            save_json(out/'hypothesis.json',hypothesis)
        targets = {name: top_weights(scores[rule],eligible,k) for name,(rule,k) in specs.items()}
        night_names = [f'{s}_{kind}' for s in cfg['overnight_symbols'] for kind in ('overnight','intraday','close_buy_hold')]
        night_names += [f'{s}_{r}_{suffix}' for s in ('SPY','QQQ') for r in ('risk_on','pullback') for suffix in ('overnight','exposure_control')]
        for part in parts:
            for name in [*specs, 'spy_buy_hold', 'sector_equal_weight'] + ['overnight/'+n for n in night_names]:
                origin = 'AI_adaptive' if hypothesis and name==hypothesis['rule'] else 'predeclared_systematic'
                db.execute('INSERT INTO trials VALUES (?,?,?,?,?,?)',(run_id,part,name,origin,'planned',None))
            if parent:
                for name in ['combined_50_50','time_sharing_auction_proxy','time_sharing_10bps_per_leg','intraday_aggressive_only']:
                    db.execute('INSERT INTO trials VALUES (?,?,?,?,?,?)',(run_id,part,'portfolio/'+name,'predeclared_portfolio_diagnostic','planned',None))
        event(db,'phase2_registered',{'run':run_id,'parts':parts,'models':list(specs),'parent':parent,'selection':selection,'hypothesis':hypothesis})
        night = run_overnight(bars[bars.symbol.isin(cfg['overnight_symbols'])],cfg,out/'overnight',parts=parts)
        db.execute("UPDATE trials SET status='complete' WHERE run_id=? AND model LIKE 'overnight/%'",(run_id,))
        db.commit()
        summaries, yearly, regimes, costs, residuals, failures = [],[],[],[],[],[]
        all_results = {}
        for part in parts:
            # Match overnight exit sessions: entry close must also be inside split.
            partition_dates = opens.index[(opens.index>=cfg[part+'_start']) & (opens.index<=cfg[part+'_end'])]
            start, end = str(partition_dates[1].date()), str(partition_dates[-1].date())
            results, stresses = {},{}
            def simulate(name, cost, intraday=False):
                if name in specs:
                    op, cl, w, av = opens,closes,targets[name],adv
                else:
                    symbols = ['SPY'] if name=='spy_buy_hold' else [s for s in cfg['overnight_symbols'] if s.startswith('XL')]
                    op,cl = [wide(bars,f,symbols) for f in ('adj_open','adj_close')]
                    w,av = cl*0+1/len(symbols), None
                return simulate_close(op,cl,w,start,end,cost,cadence=cfg['rebalance_every'],
                       buy_hold=name=='spy_buy_hold',intraday=intraday,adv=av,capital=cfg['aggressive_capital'],max_adv=cfg['max_trade_adv_fraction'])
            for name in ['spy_buy_hold','sector_equal_weight',*specs]:
                current=(run_id,part,name)
                db.execute("UPDATE trials SET status='running' WHERE run_id=? AND partition=? AND model=?",current)
                db.commit()
                try:
                    result=simulate(name,cfg['cost_bps'])
                    stress=simulate(name,cfg['stress_cost_bps'])
                except ValueError as exc:
                    if 'liquidity limit' not in str(exc).lower() or name in {'equal_weight','momentum','spy_buy_hold'}:
                        raise
                    failures.append({'partition':part,'model':name,'error':str(exc),'status':'infeasible_at_fixed_liquidity_limit'})
                    db.execute("UPDATE trials SET status='failed',error=? WHERE run_id=? AND partition=? AND model=?",(str(exc),*current))
                    event(db,'phase2_candidate_rejected',failures[-1]|{'run':run_id})
                    print(f'{part}/{name}: REJECTED {exc}',flush=True)
                    continue
                results[name],stresses[name]=result,stress
                _save_sim(result,out/'aggressive'/part/name)
                stress['daily'].to_csv(out/'aggressive'/part/name/'stress_daily.csv')
                if name in specs:
                    targets[name].loc[:end].to_csv(out/'aggressive'/part/name/'target_weights.csv')
                    if part=='discovery' and name in cfg['models']:
                        y = opens.shift(-6)/opens.shift(-1)-1
                        label_exit = pd.Series(opens.index,index=opens.index).shift(-6)
                        mask = (opens.index>=cfg['discovery_start']) & (label_exit<=pd.Timestamp(cfg['discovery_end'])).to_numpy()
                        score=scores[specs[name][0]].where(eligible)
                        ic=rank_ic(score,y.where(eligible & score.notna())).loc[mask]
                        residuals.append({'model':name,'mean_five_session_ic':float(ic.mean()),'ic_dates':int(ic.notna().sum())})
                for cost in sorted(set([0, cfg['cost_bps'], cfg['stress_cost_bps']])):
                    try:
                        at_cost=result if cost==cfg['cost_bps'] else stress if cost==cfg['stress_cost_bps'] else simulate(name,cost)
                        costs.append({'partition':part,'model':name,'cost_bps':cost,'status':'complete',**_metrics(at_cost['daily'].net_return)})
                    except ValueError as exc:
                        if 'liquidity limit' not in str(exc).lower(): raise
                        costs.append({'partition':part,'model':name,'cost_bps':cost,'status':'liquidity_rejected','error':str(exc)})
                db.execute("UPDATE trials SET status='complete' WHERE run_id=? AND partition=? AND model=?",current)
                db.commit()
                print(f'{part}/{name}: {_metrics(result["daily"].net_return)["cagr"]:.2%} net CAGR',flush=True)
            market_state = features['market_trend'].iloc[:,0].shift(1).reindex(results['equal_weight']['daily'].index)>0
            for name,result in results.items():
                row=_summary(part,name,result,stresses[name],results['equal_weight'],results['spy_buy_hold'],cfg)
                comparator='momentum_top5' if name.endswith('_top5') and name!='momentum_top5' else 'momentum' if name not in {'equal_weight','momentum','spy_buy_hold','sector_equal_weight'} else 'equal_weight'
                diff=result['daily'].net_return-results[comparator]['daily'].net_return
                row['comparator']=comparator
                row['active_comparator_annual']=float(diff.mean()*252)
                row['active_comparator_ci_low'],row['active_comparator_ci_high']=_block_ci(diff,cfg,252)
                row['stress_active_equal_annual']=float((stresses[name]['daily'].net_return-stresses['equal_weight']['daily'].net_return).mean()*252)
                row['stress_active_comparator_annual']=float((stresses[name]['daily'].net_return-stresses[comparator]['daily'].net_return).mean()*252)
                summaries.append(row)
                d=result['daily']
                for year,sub in d.groupby(d.index.year):
                    yearly.append({'partition':part,'model':name,'year':int(year),**_metrics(sub.net_return),
                                   'active_equal_annual':float((sub.net_return-results['equal_weight']['daily'].net_return.loc[sub.index]).mean()*252)})
                for state,sub in d.groupby(market_state):
                    regimes.append({'partition':part,'model':name,'market_above_prior_200sma':bool(state),**_metrics(sub.net_return)})
            all_results[part]=(results,stresses)
            if not parent:
                candidates=[r for r in summaries if r['model'] in cfg['models'] and r['model'] not in {'equal_weight','ridge_rank'}]
                winner=max(candidates,key=lambda r:r['mean_20'])['model']
                ns=pd.read_csv(out/'overnight/summary.csv')
                options=ns[(ns.partition=='discovery') & ns.model.isin(['SPY_overnight','QQQ_overnight','SPY_risk_on_overnight','QQQ_risk_on_overnight','SPY_pullback_overnight','QQQ_pullback_overnight'])]
                n=options.sort_values(['cagr','model'],ascending=[False,True]).iloc[0]
                selection={'created_at_utc':now_utc(),'aggressive':winner,'overnight':str(n['model']),
                           'aggressive_rule':'highest discovery mean contiguous 20-session return among unfitted top10 rules',
                           'overnight_rule':'highest discovery net CAGR among SPY/QQQ unconditional and two conditions',
                           'promotion':False,'role':'selected for harder validation; not authorized alpha or orders'}
                save_json(out/'selection.json',selection)
                event(db,'phase2_discovery_selection',selection|{'run':run_id})
            else:
                _portfolio_reports(out,part,results,stresses,night,selection,simulate,cfg)
                db.execute("UPDATE trials SET status='complete' WHERE run_id=? AND partition=? AND model LIKE 'portfolio/%'",(run_id,part))
                db.commit()
        summary=pd.DataFrame(summaries)
        summary.sort_values(['partition','mean_20'],ascending=[True,False]).to_csv(out/'aggressive_summary.csv',index=False)
        for filename, rows in [('yearly',yearly),('regimes',regimes),('cost_sensitivity',costs),('discovery_signal_diagnostics',residuals)]:
            pd.DataFrame(rows).to_csv(out/(filename+'.csv'),index=False)
        summary.to_csv(out/'summary.csv',index=False)
        pd.DataFrame(failures,columns=['partition','model','error','status']).to_csv(out/'failures.csv',index=False)
        _write_report(out,summary,selection,len(stocks),parent)
        save_json(out/'completion.json',{'at_utc':now_utc(),'status':'complete','parts':parts,'holdout_opened':False,'stocks':len(stocks)})
        db.execute("UPDATE runs SET status='complete' WHERE id=?",(run_id,))
        event(db,'phase2_completed',{'run':run_id,'parts':parts})
        (ROOT/'state'/('PHASE2_'+('COMPLETE' if parent else 'DISCOVERY'))).write_text(run_id)
    except Exception as exc:
        error=f'{type(exc).__name__}: {exc}'
        (out/'error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        db.execute("UPDATE runs SET status='failed',error=? WHERE id=?",(error,run_id))
        db.execute("UPDATE trials SET status=CASE WHEN status='running' THEN 'failed' ELSE 'not_run' END,error=? WHERE run_id=? AND status IN ('running','planned')",(error,run_id))
        event(db,'phase2_failed',{'run':run_id,'error':error})
        raise
    finally: db.close()
    return out


def _write_report(out,summary,selection,stock_count,parent):
    lines=['# Phase 2 empirical screen','',f'Run `{out.name}`; {stock_count} retrievable stocks from 127 dated historical identities.',
           'Availability/survivor bias limits stock results to exploratory screening. Discovery is 2013-2017; validation is reused 2018-2022. Historical 2023+ outcomes remain sealed.',
           '',f'Discovery-frozen choices: {selection["aggressive"]} top10 and {selection["overnight"]}. Selection is not promotion.',
           'Costs are 5bps per traded dollar, stressed at 10bps. Windows are overlapping contiguous 20-session historical observations, not forecasts or independent trials. Block intervals are exploratory and unadjusted for search.',
           '', '| Split | Rule | Net CAGR | Mean 20 sessions | P90 | P(>10%) | Max drawdown | 10bps CAGR |',
           '|---|---|---:|---:|---:|---:|---:|---:|']
    for _,r in summary.sort_values(['partition','mean_20'],ascending=[True,False]).iterrows():
        lines.append(f'| {r["partition"]} | {r["model"]} | {r.cagr:.2%} | {r.mean_20:.2%} | {r.p90_20:.2%} | {r.prob_20_above_10:.2%} | {r.max_drawdown:.2%} | {r.stress_cagr:.2%} |')
    lines += ['', 'The ridge model is fitted on all discovery labels; its discovery row is in-sample. Validation uses frozen coefficients. The top5 check and AI follow-up are adaptive research, not independent confirmation.',
              '', 'Files: `aggressive_summary.csv` includes tails, beta, concentration, turnover, exposure, paired block intervals and stress. `yearly.csv`, `regimes.csv`, `cost_sensitivity.csv` and per-model contributions expose instability and failures. `overnight/report.md` reports every unconditional/conditional exposure and its costs.',
              '', 'The historical universe is fixed by the archived SEC rosters: [QQQ September 2012](https://www.sec.gov/Archives/edgar/data/1067839/000110465913005075/a12-24340_1n30b2.htm), [DIA October 2012](https://www.sec.gov/Archives/edgar/data/1041130/000119312513076798/d477369d497.htm). `universe_audit.csv` preserves every exclusion. Adjusted-price histories are revised proxies, not point-in-time cash/share ledgers.',
              '', 'Conditional close entries use the previous completed daily bar. See [exchange auction timing](https://www.nyse.com/trade/auctions); a final close cannot be an input to its own closing-auction order.']
    failures=pd.read_csv(out/'failures.csv')
    if len(failures):
        lines += ['', 'Rejected trials are retained and excluded from the feasible ranking:']
        lines += [f'- {r["partition"]}/{r["model"]}: {r["error"]}' for _,r in failures.iterrows()]
    if parent:
        lines += ['',f'Parent discovery `{parent}`. `parent_selection.json` and `hypothesis.json` were saved before validation.',
                  'See `portfolio/validation/comparison.csv` for $1M-equivalent baselines and combined results, and `combined_daily.csv` for the two independently compounding $500k sleeves. The time-sharing diagnostic assumes auction prices and uses higher per-leg costs; it does not establish executable capital reuse.', '', '![Validation equity](equity.png)']
        os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.cache/matplotlib'))
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        streams=pd.read_csv(out/'portfolio/validation/returns.csv',index_col=0,parse_dates=True)
        fig,ax=plt.subplots(figsize=(11,6))
        for name in [selection['aggressive'],'overnight_only','combined_50_50','spy_buy_hold','equal_weight','time_sharing_10bps_per_leg']:
            ax.plot(streams.index,(1+streams[name]).cumprod()*1e6,label=name)
        ax.set(title='Reused historical validation, 2018-2022; biased stock availability',ylabel='Reference dollars, initial $1M')
        ax.legend(fontsize=8); ax.grid(alpha=.2); fig.tight_layout(); fig.savefig(out/'equity.png',dpi=160); plt.close(fig)
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def _portfolio_reports(out,part,results,stresses,night,selection,simulate,cfg):
    name=selection['aggressive']
    n={k:v[v.partition==part].drop(columns=['partition']) if k=='daily' else v.loc[results[name]['daily'].index] for k,v in night[selection['overnight']].items()}
    path=out/'portfolio'/part
    path.mkdir(parents=True)
    combined=combine_sleeves(results[name],n,cfg['aggressive_capital'],cfg['overnight_capital'])
    combined.to_csv(path/'combined_daily.csv')
    results[name]['weights'].mul(combined.aggressive_start,axis=0).to_csv(path/'aggressive_holdings_reference_dollars.csv')
    n['weights'].mul(combined.overnight_start,axis=0).to_csv(path/'overnight_holdings_reference_dollars.csv')
    n['daily'].to_csv(path/'overnight_daily.csv')
    intraday=simulate(name,cfg['cost_bps'],True)
    intraday_stress=simulate(name,cfg['stress_cost_bps'],True)
    _save_sim(intraday,path/'intraday_expression')
    timeshare=(1+intraday['daily'].net_return)*(1+n['daily'].net_return)-1
    stress_share=(1+intraday_stress['daily'].net_return)*(1+n['daily'].net_return_stress)-1
    streams={name:results[name]['daily'].net_return,'overnight_only':n['daily'].net_return,
             'combined_50_50':combined.net_return,'time_sharing_auction_proxy':timeshare,
             'time_sharing_10bps_per_leg':stress_share,'intraday_aggressive_only':intraday['daily'].net_return,
             **{k:results[k]['daily'].net_return for k in ('spy_buy_hold','sector_equal_weight','equal_weight')}}
    rows=[]
    for model,r in streams.items():
        rows.append({'model':model,**_metrics(r),**window_stats(r)})
    pd.DataFrame(rows).to_csv(path/'comparison.csv',index=False)
    pd.DataFrame(streams).to_csv(path/'returns.csv')
    (path/'TIMING.md').write_text('Values are marked at daily close before the next overnight entry fee. Overnight fees/returns belong to the next exit session. Each sleeve compounds its own initial capital with no daily 50/50 reset. Holdings files are target/reference dollars; exact trade dollars and fees live in each sleeve accounting output. Exposure statistics use starting sleeve shares and instrument identifiers; stock overlap inside ETFs is not diversified away. Time-sharing multiplies sequential overnight and intraday factors, liquidates stocks every close, re-enters every open, and retains the five-session ranking schedule. The 10bps-per-leg case stresses every leg for non-simultaneous fills; daily OHLC cannot establish available auction prices, latency, funding, capacity, or executable capital reuse. No brokerage execution is simulated.\n',encoding='utf-8')
