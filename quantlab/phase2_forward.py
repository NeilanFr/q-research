"""Frozen phase-two shadow targets and first-observed outcomes. No broker transport."""
from __future__ import annotations
import json
from contextlib import closing
from pathlib import Path
from uuid import uuid4
import numpy as np
import pandas as pd
from .aggressive import feature_panel, ranked, top_weights
from .core import trading_cost
from .data import ROOT, calendar, digest, latest_completed_session, load_snapshot, now_utc, save_json, wide
from .experiment import connect_db, event, source_archive
from .phase2 import hypothesis_scores


def source_fingerprint():
    return {name:digest((ROOT/'quantlab'/name).read_bytes()) for name in
            ['aggressive.py','core.py','data.py','phase2.py','phase2_forward.py']}


def tables(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS phase2_policies(name TEXT PRIMARY KEY,sha256 TEXT NOT NULL,policy_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS phase2_forecasts(id TEXT PRIMARY KEY,policy TEXT NOT NULL,sleeve TEXT NOT NULL,
        entry_date TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,UNIQUE(policy,sleeve,entry_date));
      CREATE TABLE IF NOT EXISTS phase2_outcomes(batch TEXT PRIMARY KEY,path TEXT NOT NULL,sha256 TEXT NOT NULL);
    ''')
    db.commit()


def freeze_policy(run_id, name='phase2_shadows_v1', first_aggressive_entry='2026-09-22'):
    run=ROOT/'runs'/run_id
    manifest=json.loads((run/'manifest.json').read_text())
    if not manifest.get('parent_discovery') or json.loads((run/'completion.json').read_text())['status']!='complete':
        raise ValueError('A completed discovery-plus-validation run is required')
    policy={'name':name,'created_at_utc':now_utc(),'research_run':run_id,
            'role':'shadow_only','competition_policy_changed':False,'execution_enabled':False,
            'aggressive_reference':'equal_weight','overnight_reference':'cash',
            'aggressive_candidate':'momentum','overnight_candidate':'QQQ_pullback_overnight',
            'aggressive_shadows':['equal_weight','momentum','volume_momentum','momentum_top5','momentum_quality_pullback'],
            'excluded_historical_candidates':{'ridge_rank':'historical 1% ADV limit breach','reversal':'historical 1% ADV limit breach'},
            'posthoc_validation_selected_shadow':'volume_momentum; failed 10bps comparator, not independent evidence',
            'overnight_shadows':['cash','SPY_overnight','QQQ_overnight','QQQ_pullback_overnight'],
            'first_aggressive_entry':first_aggressive_entry,'rebalance_every':5,
            'reference_capital_per_sleeve':500000.,'max_gross':1.,'max_target_weight':.20,
            'stocks':json.loads((run/'universe_audit.json').read_text())['stocks'],
            'research_config':json.loads((run/'config.json').read_text()),
            'ridge':json.loads((run/'ridge_model.json').read_text()),
            'hypothesis':json.loads((run/'hypothesis.json').read_text()),'source_hashes':source_fingerprint(),
            'reason':'No challenger meets the existing promotion requirements; preserve baseline and cash references, collect new prospective evidence. Historical stock availability is biased and validation has been reused.',
            'outcome_convention':'Isolated round-trip prediction at 5bps/leg; aggressive next-open to open five sessions later. These batch outcomes are not a continuous funded strategy curve; use actual paper holdings/fills for that.'}
    raw=json.dumps(policy,sort_keys=True,allow_nan=False)
    path=ROOT/'config'/(name+'.json')
    with closing(connect_db()) as db:
        tables(db)
        if path.exists() or db.execute('SELECT 1 FROM phase2_policies WHERE name=?',(name,)).fetchone():
            raise ValueError('Frozen policy exists; use an explicit new version')
        db.execute('INSERT INTO phase2_policies VALUES (?,?,?)',(name,digest(raw.encode()),raw))
        save_json(path,policy)
        event(db,'phase2_shadow_policy_frozen',{'name':name,'research_run':run_id,'execution_enabled':False})
    return path


def forecast_times(asof,issued_at,sleeve,policy):
    now=pd.Timestamp(issued_at); date=pd.Timestamp(asof)
    if now.tzinfo is None or date!=latest_completed_session(now): raise ValueError('Stale data or naive clock')
    cal=calendar(str((date-pd.Timedelta(days=15)).date()),str((date+pd.Timedelta(days=40)).date()))
    entry=cal.next_session(date)
    available=cal.session_close(date)+pd.Timedelta(hours=1)
    if sleeve=='overnight':
        start=cal.session_close(entry)
        exit_date=cal.next_session(entry)
        cutoff=start-pd.Timedelta(minutes=15)
    elif sleeve=='aggressive':
        anchor=pd.Timestamp(policy['first_aggressive_entry'])
        if entry<anchor: raise ValueError('Entry predates frozen schedule')
        schedule=calendar(str(anchor.date()),str((entry+pd.Timedelta(days=30)).date()))
        if not schedule.is_session(anchor) or (len(schedule.sessions_in_range(anchor,entry))-1)%policy['rebalance_every']:
            raise ValueError('Not a scheduled five-session rebalance')
        start=cal.session_open(entry); cutoff=start-pd.Timedelta(minutes=2)
        exit_date=entry
        for _ in range(policy['rebalance_every']): exit_date=cal.next_session(exit_date)
    else: raise ValueError('Unknown sleeve')
    if not available<=now<cutoff: raise ValueError('Late issuance or incomplete bars; cannot reconstruct a forecast')
    protocol=json.loads((ROOT/'config/protocol.json').read_text())
    if str(exit_date.date())>protocol['competition_end_assumption']:
        raise ValueError('Outcome crosses assumed competition finish; freeze a terminal policy first')
    return {'feature_date':str(date.date()),'available_at':available.isoformat(),'entry_date':str(entry.date()),
            'exit_date':str(exit_date.date()),'entry_at':start.isoformat(),'exit_at':cal.session_open(exit_date).isoformat(),
            'issuance_cutoff':cutoff.isoformat()}


def _policy(path,db):
    p=json.loads(Path(path).read_text(encoding='utf-8'))
    recorded=db.execute('SELECT sha256 FROM phase2_policies WHERE name=?',(p['name'],)).fetchone()
    if not recorded or digest(json.dumps(p,sort_keys=True,allow_nan=False).encode())!=recorded[0]:
        raise ValueError('Unregistered or modified frozen policy')
    if p['execution_enabled'] is not False or p['role']!='shadow_only': raise ValueError('Shadow only')
    if p['source_hashes']!=source_fingerprint(): raise ValueError('Model source changed; new policy version required')
    return p


def predict(policy_path,snapshot=None,sleeve='overnight'):
    with closing(connect_db()) as db:
        tables(db); p=_policy(policy_path,db)
        bars,manifest,_=load_snapshot(snapshot or (ROOT/'data/PHASE2_LATEST').read_text().strip())
        issued=now_utc(); asof=bars.date.max()
        times=forecast_times(str(asof.date()),issued,sleeve,p)
        if pd.Timestamp(manifest['created_at_utc'])>pd.Timestamp(issued): raise ValueError('Future data vintage')
        needed=p['stocks']+['SPY'] if sleeve=='aggressive' else ['SPY','QQQ']
        required_sources=[s for s in manifest['raw_sources'] if s['symbol'] in needed]
        if len(required_sources)!=len(needed) or any(pd.Timestamp(s['fetched_at_utc'])<pd.Timestamp(times['available_at']) for s in required_sources):
            raise ValueError('Missing source or bars acquired before publication lag')
        dates=calendar(str((asof-pd.Timedelta(days=600)).date()),str(asof.date())).sessions_in_range(str((asof-pd.Timedelta(days=500)).date()),asof)
        recent=bars[bars.date.isin(dates)&bars.symbol.isin(needed)]
        raw=wide(recent,'close',needed); volume=wide(recent,'volume',needed)
        if not raw.index.equals(dates): raise ValueError('Incomplete current calendar')
        adv=(raw*volume).rolling(20).mean().iloc[-1]
        weights,scores={},{}
        if sleeve=='aggressive':
            f,e=feature_panel(recent,p['stocks'],p['research_config'])
            scores={'equal_weight':f['momentum']*0,'momentum':f['momentum'],'momentum_top5':f['momentum'],'volume_momentum':f['volume_momentum'],
                    'momentum_quality_pullback':hypothesis_scores(f,e,p['hypothesis'])}
            model=p['ridge']
            scores['ridge_rank']=sum(ranked(f[name],e)*coef for name,coef in zip(model['features'],model['coefficients']))
            for name in p['aggressive_shadows']:
                k=None if name=='equal_weight' else 5 if name.endswith('_top5') else 10
                weights[name]=top_weights(scores[name].iloc[[-1]],e.iloc[[-1]],k).iloc[0]
                if weights[name].max()>p['max_target_weight']+1e-10: raise ValueError('Concentration limit')
                if (weights[name]*p['reference_capital_per_sleeve']/adv.reindex(p['stocks'])).max()>p['research_config']['max_trade_adv_fraction']:
                    raise ValueError('Target liquidity limit')
        else:
            close=wide(recent,'adj_close',['SPY','QQQ'])
            pullback=float(close.QQQ.iloc[-1]<close.QQQ.iloc[-4])
            weights={'cash':pd.Series({'SPY':0.}), 'SPY_overnight':pd.Series({'SPY':1.}),
                     'QQQ_overnight':pd.Series({'QQQ':1.}), 'QQQ_pullback_overnight':pd.Series({'QQQ':pullback})}
            for w in weights.values():
                if (w*p['reference_capital_per_sleeve']/adv.reindex(w.index)).max()>p['research_config']['max_trade_adv_fraction']:
                    raise ValueError('Target liquidity limit')
        rows=[]
        for model,w in weights.items():
            if not np.isfinite(w).all() or (w<0).any() or w.sum()>1+1e-10: raise ValueError('Invalid long-only target')
            for symbol,weight in w.items():
                score=float(scores[model].iloc[-1][symbol]) if model in scores and pd.notna(scores[model].iloc[-1][symbol]) else None
                rows.append({'model':model,'symbol':symbol,'score':score,'target_weight':float(weight),
                             'reference_dollars':float(weight*p['reference_capital_per_sleeve']),
                             'reference_close':float(raw.iloc[-1][symbol]),'role':'shadow','order_status':'not_an_order'})
        issued=now_utc()
        forecast_times(str(asof.date()),issued,sleeve,p)
        batch='phase2_'+uuid4().hex[:16]
        dest=ROOT/'state/phase2_forward'/batch
        if db.execute('SELECT 1 FROM phase2_forecasts WHERE policy=? AND sleeve=? AND entry_date=?',(p['name'],sleeve,times['entry_date'])).fetchone():
            raise ValueError('Duplicate frozen policy/sleeve/session')
        dest.mkdir(parents=True)
        src=source_archive(dest/'source')
        issued=now_utc(); forecast_times(str(asof.date()),issued,sleeve,p)
        forecast={'id':batch,'policy':p['name'],'sleeve':sleeve,'issued_at_utc':issued,'times':times,'snapshot':manifest['id'],
                  'source_sha256':src,'targets':rows,'capital':p['reference_capital_per_sleeve'],
                  'execution_enabled':False,'cost_bps_per_leg':p['research_config']['cost_bps'],'interpretation':p['outcome_convention']}
        save_json(dest/'forecast.json',forecast)
        pd.DataFrame(rows).to_csv(dest/'position_intents.csv',index=False)
        hashes={f:digest((dest/f).read_bytes()) for f in ['forecast.json','position_intents.csv']}
        save_json(dest/'integrity.json',hashes)
        db.execute('INSERT INTO phase2_forecasts VALUES (?,?,?,?,?,?)',(batch,p['name'],sleeve,times['entry_date'],str(dest.relative_to(ROOT)),digest((dest/'integrity.json').read_bytes())))
        event(db,'phase2_shadow_forecast',{'batch':batch,'sleeve':sleeve,'entry_date':times['entry_date'],'submitted_orders':0})
    return dest


def evaluate(snapshot=None):
    bars,manifest,_=load_snapshot(snapshot or (ROOT/'data/PHASE2_LATEST').read_text().strip())
    statuses=[]
    with closing(connect_db()) as db:
        tables(db)
        for batch,path,expected in db.execute('SELECT id,path,sha256 FROM phase2_forecasts').fetchall():
            dest=ROOT/path
            if digest((dest/'integrity.json').read_bytes())!=expected: raise ValueError('Forecast integrity changed')
            for filename,sha in json.loads((dest/'integrity.json').read_text()).items():
                if digest((dest/filename).read_bytes())!=sha: raise ValueError('Frozen forecast modified')
            existing=db.execute('SELECT path,sha256 FROM phase2_outcomes WHERE batch=?',(batch,)).fetchone()
            if existing:
                if digest((ROOT/existing[0]).read_bytes())!=existing[1]: raise ValueError('Frozen outcome modified')
                statuses.append({'batch':batch,'status':'already_frozen'}); continue
            forecast=json.loads((dest/'forecast.json').read_text())
            times=forecast['times']; now=pd.Timestamp(now_utc())
            if pd.Timestamp(times['exit_at'])>=now or pd.Timestamp(times['exit_date'])>bars.date.max():
                statuses.append({'batch':batch,'status':'pending'}); continue
            if pd.Timestamp(manifest['created_at_utc'])>now: raise ValueError('Future outcome snapshot')
            symbols={r['symbol'] for r in forecast['targets']}
            sources=[s for s in manifest['raw_sources'] if s['symbol'] in symbols]
            if len(sources)!=len(symbols) or any(pd.Timestamp(s['fetched_at_utc'])<pd.Timestamp(times['exit_at']) for s in sources):
                raise ValueError('Outcome prices acquired before their observation time')
            rows=[]
            frame=pd.DataFrame(forecast['targets'])
            for model,group in frame.groupby('model'):
                symbols=group.symbol.tolist()
                op=wide(bars[bars.symbol.isin(symbols)],'adj_open',symbols)
                entry=wide(bars[bars.symbol.isin(symbols)],'adj_close',symbols).loc[times['entry_date']] if forecast['sleeve']=='overnight' else op.loc[times['entry_date']]
                asset=op.loc[times['exit_date']]/entry-1
                w=group.set_index('symbol').target_weight.reindex(symbols).to_numpy()
                rate=forecast['cost_bps_per_leg']/10000
                fee,_=trading_cost(np.zeros(len(w)),w,rate)
                gross=float(w@asset.to_numpy())
                sell_fee=float((1-fee)*(w*(1+asset.to_numpy())).sum()*rate)
                net=(1-fee)*(1+gross)-sell_fee-1
                rows.append({'model':model,'gross_asset_weighted_return':gross,'round_trip_net_return':net,'entry_fee':fee,'exit_fee':sell_fee})
            payload={'batch':batch,'observed_at_utc':now_utc(),'snapshot':manifest['id'],'results':rows,'interpretation':forecast['interpretation']}
            file=dest/'outcome.json'; save_json(file,payload)
            db.execute('INSERT INTO phase2_outcomes VALUES (?,?,?)',(batch,str(file.relative_to(ROOT)),digest(file.read_bytes())))
            event(db,'phase2_outcome_frozen',{'batch':batch,'snapshot':manifest['id']})
            statuses.append({'batch':batch,'status':'frozen'})
    return statuses
