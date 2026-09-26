"""Frozen model policy and immutable generic prospective portfolio targets."""
from __future__ import annotations
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import numpy as np
import pandas as pd
import requests

from .data import ROOT, calendar, digest, latest_completed_session, load_snapshot, now_utc, save_json, wide
from .experiment import connect_db, event
from .forward import forecast_times
from .paper import require
from .phase2_data import normalize
from .working_model import features, score_models, status, target_weights


def tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS model_policies(version TEXT PRIMARY KEY,at TEXT,policy_json TEXT,execution_json TEXT);
        CREATE TABLE IF NOT EXISTS model_forecasts(id TEXT PRIMARY KEY,version TEXT,decision_date TEXT,entry_at TEXT,path TEXT,sha256 TEXT,UNIQUE(version,entry_at));
        CREATE TABLE IF NOT EXISTS model_outcomes(batch TEXT PRIMARY KEY,observed_at TEXT,payload TEXT);
    """)
    db.commit()


def policy_version(policy):
    return digest(json.dumps(policy,sort_keys=True).encode())[:20]


def verify_frozen_policy(policy):
    with closing(connect_db()) as db:
        tables(db)
        row=db.execute("SELECT policy_json FROM model_policies WHERE version=?",(policy_version(policy),)).fetchone()
    require(row and json.loads(row[0])==policy,"Unregistered or modified competition policy")
    require(policy["old_sector_execution_prohibited"] is True,"Retired policy cannot transmit")
    for name,expected in policy["source_hashes"].items():
        require(digest((ROOT/"quantlab"/name).read_bytes())==expected,"Frozen model source changed")
    for name,expected in policy["artifacts"].items():
        require(digest((ROOT/policy["research_path"]/name).read_bytes())==expected,"Frozen trained model artifact changed")


def freeze():
    run=(ROOT/"state/WORKING_MODEL_RUN").read_text().strip()
    path=ROOT/"runs"/run
    decision=json.loads((path/"FINAL_DECISION.json").read_text())
    finalist=json.loads((path/"FINALIST_FROZEN.json").read_text())
    dest=ROOT/"config/model_v1"
    require(not (dest/"COMPETITION_V1.json").exists(),"Competition policy already frozen")
    require(decision["holdout_opened_once"] is True,"Final audit incomplete")
    require(decision["allocation"]["overnight"]==0,"This execution release supports the selected opening book only")
    selected=decision["selected"]
    spec=finalist["specs"][selected] if selected!="cash" else {"rule":"cash","top_k":None,"weighting":"equal","cadence":5,"night":False}
    cfg=finalist["cfg"]
    asof=latest_completed_session()
    cal=calendar(str(asof.date()),str((asof+pd.Timedelta(days=30)).date()))
    anchor=str(cal.next_session(asof).date())
    policy={"name":"COMPETITION_V1","created_at":now_utc(),"schema":"frozen_targets_v1","active_model":selected,
            "aggressive_version":"AGGRESSIVE_V1","overnight_version":None,"overnight_status":"REJECTED: no cost-qualified exposures in validation",
            "allocation":decision["allocation"],"spec":spec,"universe":finalist["universe"],"features":finalist["features"],"cfg":cfg,
            "rebalance_anchor":anchor,"max_weight":cfg["max_weight"],"gross":cfg["gross"],"research_path":str(path.relative_to(ROOT)).replace("\\","/"),
            "artifacts":{**finalist["artifacts"],"FINAL_DECISION.json":digest((path/"FINAL_DECISION.json").read_bytes()),"FINALIST_FROZEN.json":digest((path/"FINALIST_FROZEN.json").read_bytes())},
            "source_hashes":{n:digest((ROOT/"quantlab"/n).read_bytes()) for n in ("working_model.py","aggressive.py","core.py","data.py","competition.py")},
            "candidate_holdout_passed":decision["candidate_holdout_passed"],"old_sector_execution_prohibited":True,
            "reason":"Selected by frozen chronological validation and one-time holdout protocol; no subsequent fitting or candidate search. Not a guarantee of alpha; survivor/availability bias and negative validation rank IC remain."}
    version=policy_version(policy)
    execution=json.loads((ROOT/"config/paper_execution_v1.json").read_text())
    execution.update(name="model_paper_execution_v1",transmission_suspended=False,research_policy_version=version,active_model=selected,
                     symbols=policy["universe"],max_weight=cfg["max_weight"],max_gross=1.0,max_trade_adv_fraction=cfg["max_trade_adv_fraction"],
                     reason="Frozen AGGRESSIVE_V1 targets; only PAPER, next scheduled opening, all original execution guards retained")
    save_json(dest/"AGGRESSIVE_V1.json",{"model":selected,"spec":spec,"features":policy["features"],"universe":policy["universe"],"training":json.loads((path/"fitted.json").read_text()),"model_artifacts":policy["artifacts"],"allocation":1.0})
    save_json(dest/"OVERNIGHT_REJECTED.json",{"decision":"REJECTED","models":cfg["overnight"],"minimum_predicted_gross":cfg["overnight_min_predicted_gross"],"allocation":0.0,"reason":"All selective cost-qualified candidates stayed in cash. No credible positive net edge established; no overnight model is activated."})
    save_json(dest/"COMPETITION_V1.json",policy)
    save_json(dest/"paper_execution.json",execution)
    with closing(connect_db()) as db:
        tables(db)
        db.execute("INSERT INTO model_policies VALUES(?,?,?,?)",(version,now_utc(),json.dumps(policy,sort_keys=True),json.dumps(execution,sort_keys=True)))
        event(db,"competition_model_frozen",{"version":version,"active_model":selected,"path":str(dest.relative_to(ROOT))})
    status(f"COMPETITION_V1 frozen as {version}: {selected}, 100% aggressive book / 0% overnight; five-session opening schedule anchored {anchor}.")
    return dest


def model_times(asof,issued,policy):
    result=forecast_times(asof,issued)
    cal=calendar(policy["rebalance_anchor"],str((pd.Timestamp(result["entry_date"])+pd.Timedelta(days=30)).date()))
    anchor,entry=pd.Timestamp(policy["rebalance_anchor"]),pd.Timestamp(result["entry_date"])
    require(entry>=anchor and (len(cal.sessions_in_range(anchor,entry))-1)%policy["spec"]["cadence"]==0,"Not a frozen five-session rebalance date")
    exit_date=entry
    for _ in range(policy["spec"]["cadence"]):
        exit_date=cal.next_session(exit_date)
    result["exit_date"],result["exit_at"]=str(exit_date.date()),cal.session_open(exit_date).isoformat()
    protocol=json.loads((ROOT/"config/protocol.json").read_text())
    require(result["exit_date"]<=protocol["competition_end_assumption"],"Holding period crosses unconfirmed competition finish")
    return result


def fetch_current():
    policy=json.loads((ROOT/"config/model_v1/COMPETITION_V1.json").read_text())
    verify_frozen_policy(policy)
    session=latest_completed_session()
    end=str(session.date())
    start=str((session-pd.Timedelta(days=750)).date())
    symbols=sorted(set(policy["universe"]+["SPY"]))
    cache=ROOT/"data/model_downloads"/end
    cache.mkdir(parents=True,exist_ok=True)
    frames,sources=[],[]
    for symbol in symbols:
        meta=cache/(symbol+".json")
        if meta.exists():
            source=json.loads(meta.read_text())
            raw=(ROOT/source["file"]).read_bytes()
            require(digest(raw)==source["sha256"],"Current data cache changed")
        else:
            response=requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"period1":int(pd.Timestamp(start,tz="UTC").timestamp()),"period2":int((pd.Timestamp(end,tz="UTC")+pd.Timedelta(days=1)).timestamp()),
                        "interval":"1d","events":"div,splits","includeAdjustedClose":"true"},
                timeout=30,headers={"User-Agent":"quantlab-model-forward/1.0"})
            response.raise_for_status()
            raw=response.content
            raw_path=ROOT/"data/raw"/(digest(raw)+".json")
            raw_path.parent.mkdir(parents=True,exist_ok=True)
            if not raw_path.exists():
                raw_path.write_bytes(raw)
            source={"symbol":symbol,"url":response.url,"sha256":digest(raw),"file":str(raw_path.relative_to(ROOT)).replace("\\","/"),"fetched_at_utc":now_utc()}
        frame=normalize(json.loads(raw),symbol,start,end)
        require(str(frame.date.max().date())==end,"Provider has not published the latest completed session")
        save_json(meta,source)
        frames.append(frame);sources.append(source)
        print(f"Current bars {symbol}: {len(frame)}",flush=True)
    bars=pd.concat(frames,ignore_index=True).sort_values(["date","symbol"])
    content=bars.to_csv(index=False,float_format="%.12g").encode()
    sid=digest(content)[:20]
    path=ROOT/"data/snapshots"/sid
    if not path.exists():
        path.mkdir(parents=True)
        (path/"bars.csv").write_bytes(content)
        save_json(path/"manifest.json",{"id":sid,"created_at_utc":now_utc(),"start":start,"end":end,"symbols":symbols,
            "bars_sha256":digest(content),"raw_sources":sources,"source":"Current immutable forward bars, Yahoo adjusted-price proxy; not executable quotes"})
    (ROOT/"data/MODEL_LATEST").write_text(sid)
    return path


def predict(snapshot=None):
    import joblib
    policy=json.loads((ROOT/"config/model_v1/COMPETITION_V1.json").read_text())
    verify_frozen_policy(policy)
    sid=snapshot or (ROOT/"data/MODEL_LATEST").read_text().strip()
    bars,manifest,_=load_snapshot(sid)
    asof=str(bars.date.max().date())
    issued=now_utc()
    times=model_times(asof,issued,policy)
    require(pd.Timestamp(manifest["created_at_utc"])<=pd.Timestamp(issued),"Future snapshot")
    require(all(pd.Timestamp(s["fetched_at_utc"])>=pd.Timestamp(times["feature_available_at"]) for s in manifest["raw_sources"]),"Bars acquired before publication lag")
    panel=features(bars,policy["universe"],policy["cfg"])
    path=ROOT/policy["research_path"]
    model=json.loads((path/"fitted.json").read_text())
    scores=score_models(panel,model,joblib.load(path/"boost.joblib"))
    spec=policy["spec"]
    selected=policy["active_model"]
    if selected=="cash":
        weights=panel["closes"].iloc[-1]*0
    else:
        weights=target_weights(scores[spec["rule"]],panel["eligible"],panel["features"]["volatility"],spec["top_k"],spec["weighting"],policy["gross"],policy["max_weight"]).iloc[-1]
    shadow={}
    for name in ("quality_rule","ridge10","boost10","momentum","stock_equal"):
        w=target_weights(scores[name],panel["eligible"],panel["features"]["volatility"],None if name=="stock_equal" else 10,"equal",policy["gross"],policy["max_weight"]).iloc[-1]
        shadow[name]={s:float(v) for s,v in w.items() if v>0}
    intents=[{"symbol":s,"model":selected,"role":"active","target_weight":float(w),"reference_close":float(panel["raw"].iloc[-1][s])} for s,w in weights.items() if w>0]
    batch_id="model_"+uuid4().hex[:16]
    issued=now_utc()
    times=model_times(asof,issued,policy)
    batch={"schema":"frozen_targets_v1","id":batch_id,"version":policy_version(policy),"policy":policy,"active_model":selected,
        "issued_at_utc":issued,**times,"snapshot":sid,"position_intents":intents,"shadow_targets":shadow,
        "dollar_adv":{s:float(v) for s,v in panel["adv"].iloc[-1].items()},"scores":{s:float(v) for s,v in scores[spec["rule"]].iloc[-1].dropna().items()} if selected!="cash" else {},
        "cash_target_weight":float(1-weights.sum()),"execution_enabled":False,"note":"Frozen weights only. Actual NAV, live quotes, whole-share deltas, costs and account safeguards are applied by PAPER engine."}
    out=ROOT/"state/model_forward"/batch_id
    with closing(connect_db()) as db:
        tables(db)
        db.execute("BEGIN IMMEDIATE")
        require(not db.execute("SELECT 1 FROM model_forecasts WHERE version=? AND entry_at=?",(batch["version"],batch["entry_at"])).fetchone(),"Duplicate frozen competition forecast")
        save_json(out/"forecast.json",batch)
        pd.DataFrame(intents,columns=["symbol","model","role","target_weight","reference_close"]).to_csv(out/"targets.csv",index=False)
        db.execute("INSERT INTO model_forecasts VALUES(?,?,?,?,?,?)",(batch_id,batch["version"],asof,batch["entry_at"],str(out.relative_to(ROOT)),digest((out/"forecast.json").read_bytes())))
        model_times(asof,now_utc(),policy)
        db.commit()
        event(db,"competition_forward_frozen",{"batch":batch_id,"version":batch["version"],"entry_at":batch["entry_at"],"active_model":selected})
    status(f"Prospective {batch_id} frozen for {batch['entry_at']}; {len(intents)} stock targets, {batch['cash_target_weight']:.2%} reference cash. No broker orders.")
    return out


def load_model_forecast(batch_id,db):
    row=db.execute("SELECT id,version,decision_date,entry_at,path,sha256 FROM model_forecasts "+("WHERE id=?" if batch_id else "ORDER BY entry_at DESC LIMIT 1"),(batch_id,) if batch_id else ()).fetchone()
    require(row is not None,"No generic frozen target portfolio")
    path=(ROOT/row[4]/"forecast.json").resolve()
    require(path.is_relative_to(ROOT/"state/model_forward"),"Invalid generic forecast path")
    raw=path.read_bytes()
    require(digest(raw)==row[5],"Frozen model forecast integrity failure")
    batch=json.loads(raw)
    require((batch["id"],batch["version"],batch["decision_date"],batch["entry_at"])==row[:4],"Generic forecast ledger mismatch")
    verify_frozen_policy(batch["policy"])
    return batch


def evaluate(snapshot=None):
    """First-observed open-to-open target-book outcomes, never broker fills."""
    sid=snapshot or (ROOT/"data/MODEL_LATEST").read_text().strip()
    bars,_,_=load_snapshot(sid)
    with closing(connect_db()) as db:
        tables(db)
        ids=[r[0] for r in db.execute("SELECT id FROM model_forecasts ORDER BY entry_at")]
        for batch_id in ids:
            batch=load_model_forecast(batch_id,db)
            if db.execute("SELECT 1 FROM model_outcomes WHERE batch=?",(batch_id,)).fetchone():
                continue
            if pd.Timestamp(now_utc())<pd.Timestamp(batch["exit_at"]) or bars.date.max()<pd.Timestamp(batch["exit_date"]):
                continue
            prices=wide(bars,"adj_open",batch["policy"]["universe"]+["SPY"])
            r=prices.loc[batch["exit_date"]]/prices.loc[batch["entry_date"]]-1
            books={**batch["shadow_targets"],"ACTIVE":[(o["symbol"],o["target_weight"]) for o in batch["position_intents"]]}
            rows={}
            for name,w in books.items():
                w=dict(w)
                gross=sum(v*float(r[s]) for s,v in w.items())
                cost=batch["policy"]["cfg"]["cost_bps"]/10000
                # Isolated round-trip target book, costs both entry and exit.
                invested=sum(w.values())
                rows[name]={"gross":gross,"net_proxy":gross-cost*invested-cost*(invested+gross)}
            payload={"at":now_utc(),"snapshot":sid,"models":rows,"benchmark_spy":float(r.SPY),"convention":"Isolated five-session hypothetical round trip; not continuous broker performance"}
            db.execute("INSERT INTO model_outcomes VALUES(?,?,?)",(batch_id,now_utc(),json.dumps(payload,sort_keys=True)))
            db.commit()
    return {"batches_checked":len(ids)}


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["freeze","fetch","predict","evaluate"])
    parser.add_argument("--snapshot")
    args=parser.parse_args()
    print({"freeze":freeze,"fetch":fetch_current,"predict":lambda:predict(args.snapshot),"evaluate":lambda:evaluate(args.snapshot)}[args.action]())
