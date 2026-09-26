"""Commissioning, reporting and NYSE-aware scheduling for PAPER execution."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import pandas as pd

from .data import ROOT, calendar, digest, latest_completed_session, now_utc, save_json
from .paper import (BrokerSafetyError, PaperLedger, build_plan, canonical, connection_config,
                    execution_policy, load_forecast, masked, number, redacted, require,
                    stamp, validate_forecast, validate_snapshot)


def source_fingerprint():
    # Bind tests and arms to the executable, installed SDK, all execution/config
    # inputs (including the private allowlist), scripts and complete test suite.
    import importlib.metadata
    paths = sorted({*(ROOT/"quantlab").rglob("*.py"), *(ROOT/"tests").rglob("*.py"),
                    *(ROOT/"config").rglob("*.json"), *(ROOT/"scripts").glob("*.ps1"),
                    *(ROOT).glob("requirements*.lock"), ROOT/"pyproject.toml"})
    import ibapi
    sdk = Path(ibapi.__file__).parent
    files = {str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in paths}
    files.update({"sdk/"+str(p.relative_to(sdk)):digest(p.read_bytes()) for p in sdk.rglob("*.py")})
    files["python_executable"] = digest(Path(sys.executable).read_bytes())
    files["python_version"] = sys.version
    files["installed_versions"] = sorted((d.metadata["Name"],d.version) for d in importlib.metadata.distributions())
    return digest(canonical(files).encode())


def self_test():
    before = source_fingerprint()
    result = subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-v"],cwd=ROOT,capture_output=True,text=True)
    output = ROOT/"state/paper_checks"
    output.mkdir(parents=True,exist_ok=True)
    (output/"latest-tests.txt").write_text(result.stdout+result.stderr,encoding="utf-8")
    require(result.returncode == 0, "Complete test suite failed; see state/paper_checks/latest-tests.txt")
    require(source_fingerprint() == before, "Code changed while tests ran")
    attestation = {"at":now_utc(),"source_sha256":before,"returncode":result.returncode,"log_sha256":digest((output/"latest-tests.txt").read_bytes())}
    save_json(output/"passed.json",attestation)
    return attestation


def require_tested():
    path = ROOT/"state/paper_checks/passed.json"
    require(path.exists(), "Run paper check: complete suite must pass before transmission")
    record = json.loads(path.read_text())
    require(record["source_sha256"] == source_fingerprint() and record["returncode"] == 0, "Execution code changed since complete tests passed")
    require(record["log_sha256"] == digest((path.parent/"latest-tests.txt").read_bytes()), "Passing test log changed or missing")
    require(0 <= (stamp(now_utc())-stamp(record["at"])).total_seconds() <= 24*3600, "Complete test attestation is older than 24 hours")


def local_status():
    listeners = {}
    for port in (7496,7497,4001,4002):
        with socket.socket() as s:
            s.settimeout(.25)
            listeners[str(port)] = s.connect_ex(("127.0.0.1",port)) == 0
    try:
        import ibapi
        sdk = ibapi.__version__
    except ImportError:
        sdk = None
    result = {"at":now_utc(),"sdk_version":sdk,"local_listeners":listeners,"connection_config_exists":(ROOT/"config/private_paper.json").exists(),"active_policy":execution_policy()["research_policy_version"]}
    try:
        batch = load_forecast()
        result["forecast"] = {k:batch[k] for k in ("id","version","issued_at_utc","entry_at")}
        validate_forecast(batch,execution_policy(),now_utc())
        result["forecast_valid"] = True
    except (BrokerSafetyError, ValueError) as exc:
        result["forecast_valid"],result["forecast_blocker"] = False,str(exc)
    save_json(ROOT/"state/paper_checks/local_status.json",result)
    return result


def save_evidence(kind, value):
    path = ROOT/"state/private/paper"/(pd.Timestamp(now_utc()).strftime("%Y%m%dT%H%M%S%f")+"_"+kind+".json")
    save_json(path,value)
    return path


def accounting(ledger, snapshot, quotes=None):
    with ledger.lock:
        baseline_row = ledger.db.execute("SELECT payload FROM observations WHERE kind='account_snapshot' ORDER BY at LIMIT 1").fetchone()
        commissions = {r[0]:json.loads(r[1]) for r in ledger.db.execute("SELECT id,payload FROM commissions")}
        own_refs = {r[0] for r in ledger.db.execute("SELECT order_ref FROM orders")}
    baseline = json.loads(baseline_row[0]) if baseline_row else snapshot
    fills = [f for f in ledger.effective_fills() if f["account"] == snapshot["account"] and f["order_ref"] in own_refs]
    known_costs = [commissions[f["execution_id"]] for f in fills if f["execution_id"] in commissions]
    exposure = None
    if quotes:
        exposure = {p["symbol"]:number(p["quantity"])*(quotes[p["symbol"]]["bid"]+quotes[p["symbol"]]["ask"])/2 for p in snapshot["positions"] if number(p["quantity"]) != 0}
    elif "portfolio" in snapshot:
        exposure = {p["symbol"]:number(p["market_value"]) for p in snapshot["portfolio"]}
    symbol_pnl = None
    if "portfolio" in baseline and "portfolio" in snapshot:
        initial_marks = {p["symbol"]:number(p["market_value"]) for p in baseline["portfolio"]}
        current_marks = {p["symbol"]:number(p["market_value"]) for p in snapshot["portfolio"]}
        symbol_pnl = {s:current_marks.get(s,0)-initial_marks.get(s,0) for s in set(initial_marks)|set(current_marks)|{f["symbol"] for f in fills}}
        for f in fills:
            symbol_pnl[f["symbol"]] += number(f["quantity"])*number(f["price"])*(1 if f["side"]=="SELL" else -1)
            cost = commissions.get(f["execution_id"])
            if cost and cost["currency"] == "USD":
                symbol_pnl[f["symbol"]] -= number(cost["commission"])
    value = {"at":snapshot["observed_at"],"initial_observed_nav":baseline["nav"],"initial_observed_cash":baseline["cash"],
             "initial_observed_at":baseline["observed_at"],"initial_positions":baseline["positions"],"equity":snapshot["nav"],
             "cash":snapshot["cash"],"equity_change_since_initial":snapshot["nav"]-baseline["nav"],"broker_pnl":snapshot["pnl"],
             "equity_change_note":"Includes external cash flows if any; not a claim of strategy P&L",
             "gross_exposure":sum(abs(v) for v in exposure.values())/snapshot["nav"] if exposure is not None else None,
             "net_exposure":sum(exposure.values())/snapshot["nav"] if exposure is not None else None,
             "max_concentration":max([abs(v)/snapshot["nav"] for v in exposure.values()],default=0) if exposure is not None else None,
             "cumulative_traded_notional":sum(number(f["quantity"])*number(f["price"]) for f in fills),
             "commissions_received":sum(number(c["commission"]) for c in known_costs if c["currency"] == "USD"),
             "commissions_pending":len(fills)-len(known_costs),"active_book":execution_policy()["active_model"],
             "active_symbol_marked_pnl":symbol_pnl,
             "attribution_note":"Broker marks plus owned execution cash flows minus received commissions; excludes unattributed dividends, interest, external flows and missing commissions.",
             "shadow_performance":"state/forward_evaluations and state/phase2_forward_evaluations; hypothetical, separate from fills",
             "benchmark":"SPY frozen forward shadow; actual marked return pending observed outcome"}
    value["turnover_vs_initial_nav"] = value["cumulative_traded_notional"]/baseline["nav"]
    if symbol_pnl is not None:
        value["active_marked_contribution"] = sum(symbol_pnl.values())
        value["unattributed_equity_change"] = value["equity_change_since_initial"]-sum(symbol_pnl.values())
    ledger.observe("accounting",value)
    summaries = sorted((ROOT/"state/forward_evaluations").glob("*/summary.csv"))
    value["daily_shadow_performance"] = pd.read_csv(summaries[-1]).to_dict("records") if summaries else []
    save_json(ROOT/"state/paper_checks/competition_accounting.json",redacted(value))
    return value


def report(status, *, snapshot=None, batch=None, plan=None, reconciliation=None, accounts=None, warning=None):
    lines = ["# First PAPER execution report", "", f"Observed: {now_utc()}", "", f"Status: **{status}**", "",
             f"Frozen active policy: {execution_policy()['active_model']}, version `{execution_policy()['research_policy_version']}`. Only this active book can transmit; all other models remain shadows.", ""]
    if snapshot:
        lines += [f"Account: `{masked(snapshot['account'])}`; exact independently configured allowlist matched. Environment assertion: {snapshot['environment']}. API account type: {snapshot['account_type']}. Client ID: {snapshot['client_id']}.","",
                  f"Connection: {snapshot['connection_at']}. NAV: ${snapshot['nav']:,.2f}; cash: ${snapshot['cash']:,.2f}; available funds: ${snapshot['available_funds']:,.2f}; buying power: ${snapshot['buying_power']:,.2f}.",""]
    else:
        lines += ["No account has been connected or verified. No broker NAV, cash, positions, orders, executions or commissions can be claimed.",""]
    if batch:
        lines += [f"Forecast: `{batch['id']}`. Decision: {batch['issued_at_utc']}. Intended opening execution: {batch['entry_at']}.",""]
    if plan:
        lines += ["Generated delta orders (generation does not imply submission or fill):", "",
                  "| Symbol | Side | Quantity | Type/TIF | Limit | Reference | Notional | Target weight | Current weight | Result weight | Reason |",
                  "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|"]
        for o in plan["orders"]:
            lines += [f"| {o['symbol']} | {o['side']} | {o['quantity']} | LMT/OPG | {o['limit_price']:.2f} | {o['reference_price']:.2f} | {o['estimated_notional']:.2f} | {o['target_weight']:.4%} | {o['current_weight']:.4%} | {o['resulting_estimated_weight']:.4%} | {o['reason']} |"]
        lines += ["",f"All rows use strategy `{plan['strategy_version']}` and forecast `{plan['batch_id']}`. Opening limits bound prices; an unfilled opening order is not chased intraday.",""]
    if reconciliation is not None:
        lines += ["Broker lifecycle evidence:", "", "```json",json.dumps(redacted(reconciliation),indent=2),"```",""]
    if snapshot:
        positions = snapshot["positions"]
        deviations = []
        if plan:
            for s,t in plan["targets"].items():
                actual = sum(number(p["quantity"]) for p in positions if p["symbol"] == s)
                deviations.append({"symbol":s,"desired":t["desired_quantity"],"actual":actual,"difference":actual-t["desired_quantity"]})
        lines += ["Resulting observed positions, open orders, recent executions and deviations:","","```json",json.dumps(redacted({"positions":positions,"open_orders":snapshot["open_orders"],"executions":snapshot["executions"],"deviations":deviations}),indent=2),"```",""]
    if accounts:
        lines += ["Accounting:","","```json",json.dumps(redacted(accounts),indent=2),"```",""]
    if warning:
        lines += ["Warnings / unresolved issues:","",str(redacted(warning)),""]
    target = ROOT/"FIRST_PAPER_EXECUTION_REPORT.md"
    # Update the FIRST report through this batch's final reconciliation; later
    # sessions and read-only probes get the separate latest report.
    prior = target.read_text(encoding="utf-8") if target.exists() else ""
    first_marker = next((line for line in prior.splitlines() if line.startswith("First transmitted forecast: ")),None)
    if first_marker:
        if not batch or first_marker != "First transmitted forecast: " + batch["id"]:
            target = ROOT/"state/paper_checks/LATEST_PAPER_EXECUTION_REPORT.md"
        else:
            lines += [first_marker,""]
    elif status.startswith("PAPER orders submitted") and batch:
        lines += ["First transmitted forecast: " + batch["id"],""]
    text = "\n".join(lines)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(text,encoding="utf-8")
    return target


def commission(action, batch_id=None, enable=False, observe_seconds=60):
    from .tws import PaperTWS
    cfg, policy = connection_config(), execution_policy()
    require(not enable, "--enable-paper retired: use the explicit one-batch PAPER arm command")
    if action == "execute":
        require_tested()
    ledger = PaperLedger()
    broker, snapshot, batch, plan, reconciled = None,None,None,None,None
    try:
        if action == "execute":
            from .paper_autonomy import require_active_arm
            require(bool(batch_id), "Execution requires an explicitly armed batch ID")
            batch = load_forecast(batch_id)
            require_active_arm(ledger,batch,cfg,policy)
            cfg = {**cfg,"execution_enabled":True}
        broker = PaperTWS(cfg,policy,ledger)
        broker.start()
        snapshot = broker.snapshot()
        validate_snapshot(snapshot,cfg,policy,now_utc(),allow_open=True)
        reconciled = ledger.reconcile(snapshot)
        position_reconciliation = ledger.reconcile_positions(snapshot)
        print(json.dumps(redacted(snapshot),indent=2),flush=True)
        if action in {"inspect","reconcile"}:
            report("Read-only account reconciliation",snapshot=snapshot,reconciliation=reconciled,accounts=accounting(ledger,snapshot))
            return
        require(not any(r["state"] in {"UNKNOWN_RECONCILE","PARTIALLY_FILLED","ACKNOWLEDGED"} for r in reconciled), "Unresolved prior order; no further transmission until broker reconciliation is complete")
        require(not position_reconciliation["differences"],"Broker positions differ from baseline plus executions; reconcile manual activity/corporate actions")
        batch = load_forecast(batch_id)
        validate_forecast(batch,policy,now_utc(),submission=action in {"preview","execute"})
        needed = {r["symbol"] for r in batch["position_intents"] if r["role"]=="active" and r["target_weight"]>0}
        needed |= {p["symbol"] for p in snapshot["positions"] if number(p["quantity"])!=0}
        contracts = broker.qualify(needed)
        broker.subscribe_quotes()
        quotes = broker.current_quotes()
        snapshot = broker.snapshot()
        plan = build_plan(batch,snapshot,contracts,quotes,cfg,policy,now_utc())
        ledger.observe("dry_run",plan)
        path = save_evidence("dry_run",plan)
        print(json.dumps(redacted({"dry_run":str(path),"plan":plan}),indent=2),flush=True)
        report("DRY_RUN; no orders transmitted",snapshot=snapshot,batch=batch,plan=plan,accounts=accounting(ledger,snapshot,quotes))
        if action == "dry-run":
            return
        if plan["orders"]:
            cutoff = stamp(batch["entry_at"])-pd.Timedelta(minutes=policy["submit_minutes_before_open"][1])
            remaining = (cutoff-stamp(now_utc())).total_seconds()
            required = (len(plan["orders"])-1)*policy["what_if_min_interval_seconds"]+60
            require(remaining > required, "Insufficient frozen window for paced previews; no submission")
        previews = []
        for row in plan["orders"]:
            while True:
                with ledger.lock:
                    previous = ledger.db.execute("SELECT at FROM observations WHERE kind='what_if_requested' ORDER BY at DESC LIMIT 1").fetchone()
                wait = policy["what_if_min_interval_seconds"]-(stamp(now_utc())-stamp(previous[0])).total_seconds() if previous else 0
                if wait <= 0:
                    break
                broker.assert_session()
                validate_forecast(batch,policy,now_utc(),submission=True)
                print(f"What-if pacing: {wait:.0f}s remaining",flush=True)
                time.sleep(min(wait,15))
            snapshot = broker.snapshot()
            previews.append(broker.what_if(plan,batch,snapshot,row))
            print(f"What-if passed: {row['symbol']}",flush=True)
        if action == "preview":
            report("IBKR what-if validated; no actual orders transmitted",snapshot=snapshot,batch=batch,plan=plan)
            return
        require_tested()
        snapshot = broker.snapshot()
        broker.transmit_batch(plan,batch,snapshot,previews)
        deadline = time.monotonic()+observe_seconds
        while True:
            snapshot = broker.snapshot()
            reconciled = ledger.reconcile(snapshot)
            position_reconciliation = ledger.reconcile_positions(snapshot)
            report("PAPER orders submitted; consult fills for actual execution",snapshot=snapshot,batch=batch,plan=plan,reconciliation=reconciled,accounts=accounting(ledger,snapshot,broker.current_quotes()),warning={"note":"Submitted is not filled. Missing commissions remain pending. Unfilled opening orders are never automatically resubmitted.","position_reconciliation":position_reconciliation})
            if time.monotonic() >= deadline:
                break
            time.sleep(min(10,max(0,deadline-time.monotonic())))
    except BaseException as exc:
        ledger.observe("commissioning_blocked",{"action":action,"error":str(exc),"type":type(exc).__name__})
        report("BLOCKED; no further transmission",snapshot=snapshot,batch=batch,plan=plan,reconciliation=reconciled,warning=str(exc))
        raise
    finally:
        if broker:
            broker.stop()
        ledger.close()


def schedule(now):
    now = stamp(now)
    cal = calendar((now-pd.Timedelta(days=15)).date().isoformat(),(now+pd.Timedelta(days=30)).date().isoformat())
    sessions = cal.schedule
    decisions = sessions["close"]+pd.Timedelta(hours=1)
    openings = sessions["open"]-pd.Timedelta(minutes=20)
    return {"now":now.isoformat(),"latest_feature_session":str(latest_completed_session(now).date()),
            "next_decision_at":decisions[decisions>now].iloc[0].isoformat(),
            "next_commission_at":openings[openings>now].iloc[0].isoformat(),
            "market_open":bool(((sessions["open"]<=now)&(now<sessions["close"])).any())}


def watch(poll_seconds=15, research_only=False, batch_id=None, monitor_only=False, max_seconds=None):
    from .paper_autonomy import watch as armed_watch
    return armed_watch(batch_id=batch_id, poll_seconds=poll_seconds,
                       monitor_only=monitor_only or research_only, max_seconds=max_seconds)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["status","check","inspect","dry-run","preview","execute","reconcile","watch","arm","readiness"])
    parser.add_argument("--batch")
    parser.add_argument("--enable-paper",action="store_true",help="Retired and rejected; use the explicit one-batch arm command")
    parser.add_argument("--observe-seconds",type=int,default=1500)
    parser.add_argument("--research-only",action="store_true",help="Deprecated alias for --monitor-only; never performs research")
    parser.add_argument("--monitor-only",action="store_true",help="Read-only watcher; cannot preview or submit orders")
    parser.add_argument("--max-seconds",type=int,help="Bounded monitor-only startup check")
    parser.add_argument("--poll-seconds",type=int,default=15)
    args = parser.parse_args(argv)
    require(0 <= args.observe_seconds <= 3600,"Observation must be between 0 and 3600 seconds")
    if args.action == "status":
        result = local_status()
        result["schedule"] = schedule(now_utc())
        print(json.dumps(result,indent=2))
    elif args.action == "check":
        print(json.dumps(self_test(),indent=2))
    elif args.action == "watch":
        watch(args.poll_seconds,args.research_only,args.batch,args.monitor_only,args.max_seconds)
    elif args.action == "arm":
        from .paper_autonomy import arm
        print(json.dumps(arm(args.batch),indent=2))
    elif args.action == "readiness":
        from .paper_autonomy import readiness
        result = readiness(args.batch)
        print(json.dumps(result,indent=2))
        if not result["ready"]:
            sys.exit(2)
    elif args.action == "inspect":
        from .paper_autonomy import inspect_connection
        print(json.dumps(redacted(inspect_connection(args.batch)),indent=2))
    elif args.action == "execute":
        from .paper_autonomy import watcher_lock
        with watcher_lock():
            commission(args.action,args.batch,args.enable_paper,args.observe_seconds)
    else:
        commission(args.action,args.batch,args.enable_paper,args.observe_seconds)


if __name__ == "__main__":
    try:
        main()
    except (BrokerSafetyError, OSError, ValueError) as exc:
        print(str(redacted(str(exc))),file=sys.stderr)
        sys.exit(2)
