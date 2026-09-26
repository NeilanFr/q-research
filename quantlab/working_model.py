"""Compact chronological model screen and a single preregistered final audit.

The old research modules and their sealed guards are unchanged. Only `audit`
can score 2023+ here, after freezing exact artifacts and criteria on disk.
"""
from __future__ import annotations
from contextlib import closing
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .aggressive import feature_panel, ranked
from .core import trading_cost
from .data import ROOT, digest, load_snapshot, now_utc, save_json, wide
from .experiment import connect_db, event
from .phase2 import window_stats
from .report import _metrics

SECTORS = ["XLB","XLE","XLF","XLI","XLK","XLP","XLU","XLV","XLY"]


def status(message):
    path = ROOT/"WORKING_MODEL_STATUS.md"
    with path.open("a",encoding="utf-8") as stream:
        stream.write(f"\n- {now_utc()}: {message}\n")
    print(message,flush=True)


def features(bars, symbols, cfg):
    f,e = feature_panel(bars,symbols,cfg)
    c,o,raw,volume = [wide(bars,n,symbols) for n in ("adj_close","adj_open","close","volume")]
    adv = (raw*volume).rolling(20).mean()
    e &= (raw>=5) & (adv>=cfg["min_dollar_volume"])
    base = ["momentum","acceleration","reversal","residual_momentum","breakout","volume_momentum","volatility","volume_anomaly","gap_continuation"]
    x = {n:ranked(f[n],e).fillna(0) for n in base}
    x["gap_20"] = ranked((o/c.shift(1)-1).rolling(20).mean(),e).fillna(0)
    x["vol_ratio"] = ranked(c.pct_change(fill_method=None).rolling(10).std()/f["volatility"],e).fillna(0)
    x["momentum_reversal"] = x["momentum"]*x["reversal"]
    x["breakout_volume"] = x["breakout"]*x["volume_anomaly"]
    x["momentum_lowvol"] = x["momentum"]*(-x["volatility"])
    x["market_trend"] = f["market_trend"].clip(-.5,.5).fillna(0)
    x["trend_reversal"] = x["market_trend"]*x["reversal"]
    array = np.stack([v.to_numpy() for v in x.values()],axis=2)
    return {"x":array,"feature_names":list(x),"eligible":e,"features":f,"opens":o,"closes":c,"adv":adv,"raw":raw}


def fit_models(panel,cfg,path):
    import joblib
    from sklearn.ensemble import HistGradientBoostingRegressor
    from threadpoolctl import threadpool_limits
    x,e,op,cl = panel["x"],panel["eligible"].to_numpy(),panel["opens"],panel["closes"]
    h = cfg["horizon"]
    labels = op.shift(-(h+1))/op.shift(-1)-1
    labels = labels.sub(labels.where(panel["eligible"]).mean(axis=1),axis=0).clip(-.5,.5)
    exit_dates = pd.Series(op.index,index=op.index).shift(-(h+1))
    dates = (op.index>=cfg["train_start"]) & (exit_dates<=cfg["train_end"]).to_numpy()
    good = e & dates[:,None] & np.isfinite(labels.to_numpy()) & np.isfinite(x).all(axis=2)
    xx,yy = x[good],labels.to_numpy()[good]
    if len(yy)<10000:
        raise ValueError("Insufficient training observations")
    matrix = np.column_stack([np.ones(len(xx)),xx])
    penalty = np.eye(matrix.shape[1])*cfg["ridge_penalty"]
    penalty[0,0]=0
    ridge = np.linalg.solve(matrix.T@matrix/len(yy)+penalty,matrix.T@yy/len(yy))
    tree = HistGradientBoostingRegressor(**cfg["tree"])
    with threadpool_limits(limits=4):
        tree.fit(xx,yy)
    # Features from close t predict entry close t+1 -> open t+2; the entry close
    # is never an input to an order assumed filled at that same close.
    night = (op.shift(-2)/cl.shift(-1)-1).clip(-.1,.1)
    night_dates = pd.Series(op.index,index=op.index).shift(-2)
    ngood = e & ((op.index>=cfg["train_start"]) & (night_dates<=cfg["train_end"]).to_numpy())[:,None] & np.isfinite(night.to_numpy())
    nx = np.column_stack([np.ones(ngood.sum()),x[ngood]])
    nridge = np.linalg.solve(nx.T@nx/ngood.sum()+penalty,nx.T@night.to_numpy()[ngood]/ngood.sum())
    model = {"feature_names":panel["feature_names"],"ridge":ridge.tolist(),"night_ridge":nridge.tolist(),"training_rows":len(yy),
             "night_training_rows":int(ngood.sum()),"last_label_exit":str(exit_dates[dates].max()),"tree_params":cfg["tree"]}
    save_json(path/"fitted.json",model)
    joblib.dump(tree,path/"boost.joblib")
    return model,tree


def score_models(panel,model,tree):
    from threadpoolctl import threadpool_limits
    x = panel["x"]
    if model["feature_names"] != panel["feature_names"]:
        raise ValueError("Feature schema changed")
    ridge = np.asarray(model["ridge"])
    night = np.asarray(model["night_ridge"])
    frames = {}
    with threadpool_limits(limits=4):
        pred = tree.predict(x.reshape(-1,x.shape[-1])).reshape(x.shape[:2])
    values = {"ridge10":ridge[0]+x@ridge[1:],"boost10":pred,"night":night[0]+x@night[1:]}
    idx = {n:i for i,n in enumerate(panel["feature_names"])}
    values["quality_rule"] = x[:,:,idx["momentum"]]+x[:,:,idx["residual_momentum"]]+x[:,:,idx["breakout"]]-.5*x[:,:,idx["volatility"]]+.25*x[:,:,idx["reversal"]]
    values["momentum"] = panel["features"]["momentum"].to_numpy()
    values["stock_equal"] = np.zeros(x.shape[:2])
    for name,value in values.items():
        frames[name] = pd.DataFrame(value,index=panel["opens"].index,columns=panel["opens"].columns).where(panel["eligible"])
    return frames


def target_weights(score,eligible,vol,k=10,weighting="equal",gross=.995,cap=.4):
    values = score.to_numpy()
    out = np.zeros_like(values)
    risk = vol.to_numpy()
    valid = eligible.to_numpy() & np.isfinite(values)
    for i in range(len(values)):
        selected = np.flatnonzero(valid[i])
        if not len(selected):
            continue
        selected = selected[np.argsort(-values[i,selected],kind="stable")[:k or len(selected)]]
        if weighting == "equal":
            w = np.ones(len(selected))
        elif weighting == "signal":
            # Bounded rank-strength avoids ill-defined negative score weights.
            w = np.linspace(1.5,.5,len(selected))
        elif weighting == "inverse_vol":
            w = 1/np.maximum(risk[i,selected],.005)
        else:
            raise ValueError("Unknown sizing")
        w = w/w.sum()*gross
        for _ in range(30):
            excess = np.maximum(w-cap,0).sum()
            w = np.minimum(w,cap)
            free = w<cap-1e-12
            if excess<1e-12 or not free.any():
                break
            w[free] += excess*w[free]/w[free].sum()
        out[i,selected] = w
    return pd.DataFrame(out,index=score.index,columns=score.columns)


def simulate(panel,targets,start,end,cfg,cost,cadence=5,night=False,night_gate=None):
    op,cl = panel["opens"].to_numpy(),panel["closes"].to_numpy()
    adv = panel["adv"].to_numpy()
    idx = panel["opens"].index
    chosen = np.flatnonzero((idx>=start)&(idx<=end))
    if not len(chosen) or chosen[0]<2 or not np.isfinite(op[chosen]).all() or not np.isfinite(cl[chosen]).all():
        raise ValueError("Incomplete evaluation price panel")
    nav,previous = 1.,np.zeros(op.shape[1])
    rows,contributions = [],[]
    rate = cost/10000
    for j,i in enumerate(chosen):
        if night:
            # Outcome date i: enter close i-1 using features available at i-2.
            target = targets.iloc[i-2].to_numpy().copy()
            if night_gate is not None and not bool(night_gate.iloc[i-2]):
                target[:]=0
            capacity = adv[i-2]*cfg["max_trade_adv_fraction"]/(cfg["capital"]*nav)
            target = np.minimum(target,capacity*.99)
            entry_fee,delta = trading_cost(np.zeros_like(target),target,rate)
            returns = op[i]/cl[i-1]-1
            pnl = (1-entry_fee)*target*returns
            exit_values = (1-entry_fee)*target*(1+returns)
            fees = np.abs(delta)*rate + exit_values*rate
            factor = 1+pnl.sum()-fees.sum()
            turn = np.abs(delta).sum()+exit_values.sum()
            contribution = pnl-fees
            weight = target
            max_adv = float(np.max(np.maximum(np.abs(delta),exit_values)*cfg["capital"]*nav/adv[i-2]))
        else:
            gap = op[i]/cl[i-1]-1
            gap_pnl = previous*gap
            gap_factor = 1+gap_pnl.sum()
            pre = previous*(1+gap)/gap_factor
            target = targets.iloc[i-1].to_numpy().copy() if j==0 or j%cadence==0 else pre.copy()
            # Feasible trade quantities, rather than silently filling illiquid
            # target changes. Residual holdings and cash remain in the book.
            capacity = adv[i-1]*cfg["max_trade_adv_fraction"]/(cfg["capital"]*nav*gap_factor)*.99
            target = pre+np.clip(target-pre,-capacity,capacity)
            if target.sum()>.999:
                positive = np.maximum(target-pre,0)
                if positive.sum():
                    target -= positive/positive.sum()*(target.sum()-.999)
            fee,delta = trading_cost(pre,target,rate)
            day = cl[i]/op[i]-1
            pnl = gap_pnl+gap_factor*(1-fee)*target*day
            fees = gap_factor*np.abs(delta)*rate
            factor = gap_factor*(1-fee)*(1+target@day)
            previous = target*(1+day)/(1+target@day)
            turn = gap_factor*np.abs(delta).sum()
            weight = target
            max_adv = float(np.max(np.abs(delta)*cfg["capital"]*nav*gap_factor/adv[i-1]))
            if j==len(chosen)-1:
                fees += factor*previous*rate
                turn += factor*previous.sum()
                factor -= factor*previous.sum()*rate
            contribution = pnl-fees
        if not np.isclose(contribution.sum(),factor-1,atol=1e-10):
            raise ArithmeticError("Account P&L attribution failure")
        rows.append({"date":idx[i],"nav_before":nav,"nav":nav*factor,"net_return":factor-1,"turnover":turn,
                     "cost_fraction":float(fees.sum()),"max_weight":float(weight.max()),"gross_exposure":float(weight.sum()),
                     "max_trade_adv_fraction":max_adv})
        contributions.append(contribution)
        nav *= factor
    return {"daily":pd.DataFrame(rows).set_index("date"),"contributions":pd.DataFrame(contributions,index=idx[chosen],columns=panel["opens"].columns)}


def statistics(sim,stress,benchmark):
    d = sim["daily"]
    row = {**_metrics(d.net_return),**window_stats(d.net_return),"stress_cagr":_metrics(stress["daily"].net_return)["cagr"],
           "stress_mean_20":window_stats(stress["daily"].net_return)["mean_20"],"annual_turnover":float(d.turnover.mean()*252),
           "annual_cost":float(d.cost_fraction.mean()*252),"max_weight":float(d.max_weight.max()),
           "mean_gross":float(d.gross_exposure.mean()),"active_sessions":int((d.gross_exposure>0).sum()),
           "max_trade_adv_fraction":float(d.max_trade_adv_fraction.max()),
           "beta_spy":float(d.net_return.cov(benchmark)/benchmark.var())}
    return {k:(None if isinstance(v,(float,np.floating)) and not np.isfinite(v) else v) for k,v in row.items()}


def record_trial(run,partition,name):
    with closing(connect_db()) as db:
        db.execute("INSERT OR IGNORE INTO trials VALUES(?,?,?,?,?,?)",(run,partition,name,"working_model_predeclared","running",None))
        db.commit()


def write_result(path,name,sim,stress,row):
    out = path/name
    out.mkdir(parents=True,exist_ok=True)
    sim["daily"].to_csv(out/"daily.csv")
    sim["contributions"].to_csv(out/"contributions.csv")
    stress["daily"].to_csv(out/"stress.csv")
    save_json(out/"metrics.json",row)


def acceptance(sim,stress,equal,momentum,equal_stress,momentum_stress,cfg):
    d = sim["daily"].net_return
    excess = d-equal["daily"].net_return
    yearly = excess.groupby(excess.index.year).mean()*252
    c = sim["contributions"]-equal["contributions"]
    paired = c.sum()
    best = paired.idxmax()
    pnl = sim["contributions"].sum().clip(lower=0)
    checks = {"beats_equal_5bps":bool(excess.mean()>0),"beats_momentum_5bps":bool((d-momentum["daily"].net_return).mean()>0),
              "beats_equal_10bps":bool((stress["daily"].net_return-equal_stress["daily"].net_return).mean()>0),
              "beats_momentum_10bps":bool((stress["daily"].net_return-momentum_stress["daily"].net_return).mean()>0),
              "positive_years":int((yearly>0).sum())>=3,"leave_best_year":bool(yearly.drop(yearly.idxmax()).mean()>0),
              "leave_best_stock":bool((excess-c[best]).mean()>0),
              "single_stock_share":bool(pnl.max()/max(pnl.sum(),1e-12)<=.5),
              "mean_20":window_stats(d)["mean_20"]>window_stats(equal["daily"].net_return)["mean_20"],
              "drawdown":_metrics(d)["max_drawdown"]>-.6}
    return {"passed":all(checks.values()),"checks":checks,"yearly_excess":yearly.to_dict(),"best_incremental_stock":best,
            "positive_pnl_largest_share":float(pnl.max()/max(pnl.sum(),1e-12))}


def screen(resume=False):
    cfg = json.loads((ROOT/"config/working_model_study.json").read_text())
    pointer = ROOT/"state/WORKING_MODEL_RUN"
    if pointer.exists() and not resume:
        raise ValueError("A registered model screen already exists; resume its recorded artifacts, do not silently rerun")
    run = pointer.read_text().strip() if resume else pd.Timestamp(now_utc()).strftime("%Y%m%dT%H%M%S")+"_working_model"
    path = ROOT/"runs"/run
    path.mkdir(parents=True,exist_ok=resume)
    if resume and (path/"FINALIST_FROZEN.json").exists():
        raise ValueError("Finalist already frozen; no repeated screening")
    if resume and json.loads((path/"config.json").read_text())!=cfg:
        raise ValueError("Cannot change registered settings on operational retry")
    save_json(path/"config.json",cfg)
    symbols = json.loads((ROOT/"runs"/cfg["universe_run"]/"universe_audit.json").read_text())["stocks"]
    save_json(path/"universe.json",{"symbols":symbols,"selection":"fixed from existing historical audit, no holdout coverage filtering"})
    save_json(path/"registration.json",{"at":now_utc(),"config_sha256":digest((path/"config.json").read_bytes()),"source_sha256":digest(Path(__file__).read_bytes()),"holdout_opened":False})
    pointer.write_text(run)
    with closing(connect_db()) as db:
        db.execute("INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?)",(run,now_utc(),json.dumps(cfg),cfg["snapshot"],digest(Path(__file__).read_bytes()),"started",None))
        event(db,"working_model_operational_retry" if resume else "working_model_registered",{"run":run,"holdout_opened":False,"reason":"same registered settings; Windows thread-pool permission failure before fitting" if resume else "initial registration"})
    status(f"Registered {run}; train ends 2017, validation ends 2022, final holdout sealed.")
    bars,_,_ = load_snapshot(cfg["snapshot"])
    historical = bars[bars.date<=cfg["validation_end"]].copy()
    del bars
    panel = features(historical,symbols,cfg)
    model,tree = fit_models(panel,cfg,path)
    scores = score_models(panel,model,tree)
    status(f"Fitted rank/rule, ridge and 100-tree boosting models on {model['training_rows']} purged training labels.")
    start,end = cfg["validation_start"],cfg["validation_end"]
    benchmark = wide(historical,"adj_close",["SPY"]).SPY.pct_change(fill_method=None).loc[start:end]
    results,stresses,rows,specs,checks = {},{},{},{},{}
    def trial(name,rule,k,weighting,cadence=5,night=False,gate=None,custom=None):
        record_trial(run,"validation",name)
        source = custom or panel
        score = scores[rule] if custom is None else source["closes"]*0
        target = target_weights(score,source["eligible"],source["features"]["volatility"],k,weighting,cfg["gross"],cfg["max_weight"])
        sim = simulate(source,target,start,end,cfg,5,cadence,night,gate)
        stress = simulate(source,target,start,end,cfg,10,cadence,night,gate)
        row = statistics(sim,stress,benchmark)
        results[name],stresses[name],rows[name] = sim,stress,row
        specs[name] = {"rule":rule,"top_k":k,"weighting":weighting,"cadence":cadence,"night":night}
        write_result(path/"validation",name,sim,stress,row)
        with closing(connect_db()) as db:
            db.execute("UPDATE trials SET status='complete' WHERE run_id=? AND partition='validation' AND model=?",(run,name));db.commit()
        status(f"Validation {name}: CAGR {row['cagr']:.2%}; stress {row['stress_cagr']:.2%}; mean 20d {row['mean_20']:.2%}; P(+10%) {row['prob_20_above_10']:.1%}.")
    trial("stock_equal","stock_equal",None,"equal")
    trial("momentum","momentum",10,"equal")
    sector = features(historical,SECTORS,{**cfg,"min_dollar_volume":1})
    trial("old_sector_baseline","stock_equal",None,"equal",1,custom=sector)
    for rule in cfg["screen"]:
        trial(rule,rule,10,"equal")
    # Select the predictive family on validation, then only the requested nine
    # concentration/sizing combinations. No further feature/hyperparameter grid.
    family = max(cfg["screen"],key=lambda n:rows[n]["stress_mean_20"])
    status(f"Family selected on reused validation: {family}. Testing only top3/5/10 and three sizing rules.")
    candidates = list(cfg["screen"])
    for k in cfg["concentration"]:
        for weighting in cfg["weighting"]:
            name = f"{family}_top{k}_{weighting}"
            if k==10 and weighting=="equal":
                continue
            trial(name,family,k,weighting)
            candidates.append(name)
    for name in candidates:
        checks[name] = acceptance(results[name],stresses[name],results["stock_equal"],results["momentum"],stresses["stock_equal"],stresses["momentum"],cfg)
    survivors = [n for n in candidates if checks[n]["passed"]]
    selected = max(survivors,key=lambda n:rows[n]["stress_mean_20"]) if survivors else "stock_equal"
    # One cadence perturbation is a stability diagnostic, not another winner.
    s = specs[selected]
    trial("stability_cadence10",s["rule"],s["top_k"],s["weighting"],10)
    if selected!="stock_equal" and rows["stability_cadence10"]["stress_mean_20"] <= rows["stock_equal"]["stress_mean_20"]:
        checks[selected]["stability_failed"] = True
        selected = "stock_equal"
    save_json(path/"validation_acceptance.json",checks)
    # Three distinct selective overnight alternatives, all using lagged closes.
    top_night = target_weights(scores["night"],panel["eligible"],panel["features"]["volatility"],5)
    predicted = (top_night*scores["night"].fillna(0)).sum(axis=1)
    gate = predicted>cfg["overnight_min_predicted_gross"]
    night_names = []
    for name in cfg["overnight"]:
        n_gate = gate.copy()
        rule = "night"
        if name=="ridge_weekly":
            n_gate &= np.arange(len(gate))%5==0
        if name=="momentum_regime":
            rule = "momentum"
            f=panel["features"]
            n_gate = (f["market_trend"].iloc[:,0]>0)&(f["volatility"].median(axis=1)*np.sqrt(252)<.30)&(predicted>cfg["overnight_min_predicted_gross"])
        trial("overnight_"+name,rule,5,"equal",1,True,n_gate)
        night_names.append("overnight_"+name)
    night_checks = {}
    for n in night_names:
        d,stress = results[n]["daily"],stresses[n]["daily"]
        years = stress.net_return.groupby(stress.index.year).sum()
        con = results[n]["contributions"].sum().clip(lower=0)
        night_checks[n] = bool(stress.net_return.mean()>0 and rows[n]["active_sessions"]>=100 and (years>0).sum()>=3 and con.max()/max(con.sum(),1e-12)<.5)
    night_ok = [n for n in night_names if night_checks[n]]
    selected_night = max(night_ok,key=lambda n:rows[n]["stress_mean_20"]) if night_ok else None
    best_night = selected_night or max(night_names,key=lambda n:rows[n]["stress_mean_20"])
    # Separate books with initial 50/50 capital; no daily capital resets.
    for cost,collection in ((5,results),(10,stresses)):
        a,n = collection[selected]["daily"],collection[best_night]["daily"]
        combined = (a.nav+n.nav)/2
        before = (a.nav_before+n.nav_before)/2
        combined_daily = a.copy()
        combined_daily["nav"],combined_daily["nav_before"],combined_daily["net_return"] = combined,before,combined/before-1
        for col in ("turnover","cost_fraction","gross_exposure"):
            combined_daily[col] = (a[col]*a.nav_before+n[col]*n.nav_before)/(a.nav_before+n.nav_before)
        combined_daily["max_weight"] = np.maximum(a.max_weight*a.nav_before,n.max_weight*n.nav_before)/(a.nav_before+n.nav_before)
        collection["combined_50_50"] = {"daily":combined_daily,"contributions":pd.DataFrame(index=a.index)}
    rows["combined_50_50"] = statistics(results["combined_50_50"],stresses["combined_50_50"],benchmark)
    write_result(path/"validation","combined_50_50",results["combined_50_50"],stresses["combined_50_50"],rows["combined_50_50"])
    allocation = {"aggressive":1.0,"overnight":0.0}
    if selected_night and rows["combined_50_50"]["stress_mean_20"]>=rows[selected]["stress_mean_20"] and rows["combined_50_50"]["prob_20_above_10"]>=rows[selected]["prob_20_above_10"]-.02:
        allocation = {"aggressive":.5,"overnight":.5}
    pd.DataFrame.from_dict(rows,orient="index").rename_axis("model").to_csv(path/"validation_summary.csv")
    # Residuals and feature importance are diagnostic, never holdout-guided.
    diagnostics(panel,scores,model,tree,cfg,path,selected,results)
    freeze = {"at":now_utc(),"run":run,"snapshot":cfg["snapshot"],"selected_aggressive":selected,"aggressive_spec":specs[selected],
              "selected_overnight":selected_night,"best_rejected_overnight":best_night if not selected_night else None,
              "night_checks":night_checks,"allocation":allocation,"specs":specs,"universe":symbols,"features":panel["feature_names"],
              "cfg":cfg,"acceptance":cfg["final_acceptance"],"fallback_spec":specs["stock_equal"],
              "artifacts":{n:digest((path/n).read_bytes()) for n in ("fitted.json","boost.joblib","config.json","universe.json")},
              "source_sha256":digest(Path(__file__).read_bytes()),"validation_passed":selected!="stock_equal",
              "holdout_protocol":"One audit of frozen aggressive candidate, selected overnight if any, stock equal, momentum and old sector comparator. No refits, no selecting another failed candidate after results. Fallback stock equal only if positive stress return; else cash."}
    save_json(path/"FINALIST_FROZEN.json",freeze)
    with closing(connect_db()) as db:
        event(db,"working_model_finalist_frozen",{"run":run,"sha256":digest((path/"FINALIST_FROZEN.json").read_bytes()),"selected":selected,"allocation":allocation})
    status(f"FINALIST FROZEN: {selected}; overnight {selected_night or 'REJECTED'}; allocation {allocation}. Holdout still sealed.")
    return path


def diagnostics(panel,scores,model,tree,cfg,path,selected,results):
    from threadpoolctl import threadpool_limits
    labels = panel["opens"].shift(-(cfg["horizon"]+1))/panel["opens"].shift(-1)-1
    labels = labels.sub(labels.mean(axis=1),axis=0)
    exits = pd.Series(labels.index,index=labels.index).shift(-(cfg["horizon"]+1))
    valid = (labels.index>=cfg["validation_start"]) & (exits<=cfg["validation_end"]).to_numpy()
    diagnostics_rows=[]
    for name in cfg["screen"]:
        ic = scores[name].rank(axis=1).corrwith(labels.rank(axis=1),axis=1).loc[valid]
        diagnostics_rows.append({"model":name,"mean_validation_rank_ic":float(ic.mean()),"positive_ic_fraction":float((ic>0).mean())})
    save_json(path/"residual_diagnostics.json",{"models":diagnostics_rows,"ridge_coefficients":dict(zip(panel["feature_names"],model["ridge"][1:])),
        "finding":"Validation IC and residual portfolio attribution are descriptive; validation has been reused. No additional search after the frozen finalist.",
        "mechanism_tested":"Ten-session relative-return model with trend/reversal, breakout/volume and low-volatility interactions; lagged-close overnight labels avoid same-close leakage."})
    sample_dates=np.flatnonzero(valid)[::10]
    xx=panel["x"][sample_dates]
    yy=labels.to_numpy()[sample_dates]
    mask=panel["eligible"].to_numpy()[sample_dates]&np.isfinite(yy)
    importance=[]
    with threadpool_limits(limits=4):
        base=tree.predict(xx.reshape(-1,xx.shape[-1])).reshape(xx.shape[:2])
        baseline=float(np.mean((base[mask]-yy[mask])**2))
        for j,name in enumerate(panel["feature_names"]):
            changed=xx.copy()
            changed[:,:,j]=np.roll(changed[:,:,j],1,axis=1)
            altered=tree.predict(changed.reshape(-1,xx.shape[-1])).reshape(xx.shape[:2])
            importance.append({"feature":name,"validation_mse_increase_on_within_date_permutation":float(np.mean((altered[mask]-yy[mask])**2)-baseline)})
    save_json(path/"boost_feature_importance.json",{"sample":"every tenth validation feature date; within-date stock permutation, diagnostic only","features":importance})
    for name in (selected,"stock_equal","momentum"):
        d=results[name]["daily"]
        d.groupby(d.index.year).net_return.agg(["mean","std"]).to_csv(path/(name+"_yearly.csv"))
        d.nsmallest(10,"net_return").to_csv(path/(name+"_failure_days.csv"))
        results[name]["contributions"].sum().sort_values(ascending=False).to_csv(path/(name+"_stock_attribution.csv"))


def audit():
    import joblib
    run=(ROOT/"state/WORKING_MODEL_RUN").read_text().strip()
    path=ROOT/"runs"/run
    frozen=json.loads((path/"FINALIST_FROZEN.json").read_text())
    for name,expected in frozen["artifacts"].items():
        if digest((path/name).read_bytes())!=expected:
            raise ValueError("Frozen model artifact modified")
    if frozen["source_sha256"]!=digest(Path(__file__).read_bytes()):
        raise ValueError("Research implementation changed since freeze")
    marker=path/"HOLDOUT_OPENED.json"
    with marker.open("x",encoding="utf-8") as stream:
        json.dump({"at":now_utc(),"freeze_sha256":digest((path/"FINALIST_FROZEN.json").read_bytes()),"one_time":True},stream,indent=2)
    with closing(connect_db()) as db:
        event(db,"working_model_holdout_opened",{"run":run,"freeze_sha256":digest((path/"FINALIST_FROZEN.json").read_bytes())})
    status("Opening final holdout ONCE under the already frozen protocol.")
    cfg=frozen["cfg"]
    bars,_,_=load_snapshot(cfg["snapshot"])
    bars=bars[bars.date<=cfg["holdout_end"]]
    panel=features(bars,frozen["universe"],cfg)
    scores=score_models(panel,json.loads((path/"fitted.json").read_text()),joblib.load(path/"boost.joblib"))
    benchmark=wide(bars,"adj_close",["SPY"]).SPY.pct_change(fill_method=None).loc[cfg["holdout_start"]:cfg["holdout_end"]]
    names=list(dict.fromkeys([frozen["selected_aggressive"],"stock_equal","momentum","old_sector_baseline"]+([frozen["selected_overnight"]] if frozen["selected_overnight"] else [])))
    results,stresses,rows={},{},{}
    for name in names:
        record_trial(run,"final_holdout",name)
        spec=frozen["specs"][name]
        p=features(bars,SECTORS,{**cfg,"min_dollar_volume":1}) if name=="old_sector_baseline" else panel
        score=p["closes"]*0 if name=="old_sector_baseline" else scores[spec["rule"]]
        w=target_weights(score,p["eligible"],p["features"]["volatility"],spec["top_k"],spec["weighting"],cfg["gross"],cfg["max_weight"])
        gate=None
        if spec["night"]:
            predicted=(target_weights(scores["night"],panel["eligible"],panel["features"]["volatility"],5)*scores["night"].fillna(0)).sum(axis=1)
            gate=predicted>cfg["overnight_min_predicted_gross"]
            if name.endswith("ridge_weekly"):
                gate &= np.arange(len(gate))%5==0
            if name.endswith("momentum_regime"):
                gate &= (panel["features"]["market_trend"].iloc[:,0]>0)&(panel["features"]["volatility"].median(axis=1)*np.sqrt(252)<.30)
        sim=simulate(p,w,cfg["holdout_start"],cfg["holdout_end"],cfg,5,spec["cadence"],spec["night"],gate)
        stress=simulate(p,w,cfg["holdout_start"],cfg["holdout_end"],cfg,10,spec["cadence"],spec["night"],gate)
        rows[name]=statistics(sim,stress,benchmark)
        results[name],stresses[name]=sim,stress
        write_result(path/"holdout",name,sim,stress,rows[name])
        with closing(connect_db()) as db:
            db.execute("UPDATE trials SET status='complete' WHERE run_id=? AND partition='final_holdout' AND model=?",(run,name));db.commit()
        status(f"Holdout {name}: CAGR {rows[name]['cagr']:.2%}, stress {rows[name]['stress_cagr']:.2%}, max DD {rows[name]['max_drawdown']:.2%}.")
    candidate=frozen["selected_aggressive"]
    d=results[candidate]["daily"].net_return
    stress=stresses[candidate]["daily"].net_return
    passed=frozen["validation_passed"] and bool(d.mean()>0 and stress.mean()>0 and rows[candidate]["max_drawdown"]>-.6 and all((d-results[b]["daily"].net_return).mean()>0 and (stress-stresses[b]["daily"].net_return).mean()>0 for b in ("stock_equal","momentum")))
    selected=candidate if passed else "stock_equal" if rows["stock_equal"]["stress_cagr"]>0 and rows["stock_equal"]["max_drawdown"]>-.6 else "cash"
    night=frozen["selected_overnight"]
    if night and rows[night]["stress_cagr"]<=0:
        night=None
    allocation=frozen["allocation"] if passed and night else {"aggressive":1.0,"overnight":0.0}
    decision={"at":now_utc(),"selected":selected,"candidate":candidate,"candidate_holdout_passed":passed,"overnight":night,
              "allocation":allocation,"fallback_used":not passed,"old_sector_execution_prohibited":True,"holdout_opened_once":True,"results":rows}
    save_json(path/"FINAL_DECISION.json",decision)
    pd.DataFrame.from_dict(rows,orient="index").rename_axis("model").to_csv(path/"holdout_summary.csv")
    with closing(connect_db()) as db:
        event(db,"working_model_final_decision",{"run":run,"decision":decision})
        db.execute("UPDATE runs SET status='complete' WHERE id=?",(run,));db.commit()
    status(f"Final decision: {selected}; candidate passed={passed}; overnight={night}; allocation={allocation}. No more model search.")
    return path


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["screen","resume-screen","audit"])
    args=parser.parse_args()
    print(audit() if args.action=="audit" else screen(resume=args.action=="resume-screen"))
