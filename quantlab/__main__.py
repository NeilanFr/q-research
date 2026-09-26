"""Run `python -m quantlab --help`. All research is offline after fetch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch", help="Download immutable real daily ETF data")
    fetch_cmd.add_argument("--config", default="config/first_study.json")
    fetch_cmd.add_argument("--end", help="Inclusive final completed session; defaults to latest")
    run_cmd = sub.add_parser("run", help="Run all recorded historical baseline and candidate trials")
    run_cmd.add_argument("--config", default="config/first_study.json")
    run_cmd.add_argument("--snapshot", help="Immutable data ID; defaults to latest")
    predict_cmd = sub.add_parser("predict", help="Freeze current prospective predictions; never submits orders")
    predict_cmd.add_argument("--policy", default="config/competition_v1.json")
    predict_cmd.add_argument("--snapshot")
    eval_cmd = sub.add_parser("evaluate-forward", help="Freeze realized shadow outcomes; no hindsight forecasts")
    eval_cmd.add_argument("--snapshot")
    phase2_cmd = sub.add_parser("phase2", help="Phase 2 discovery; --parent validates frozen discovery choices")
    phase2_cmd.add_argument("--config", default="config/phase2_study.json")
    phase2_cmd.add_argument("--snapshot")
    phase2_cmd.add_argument("--parent", help="Completed phase2 discovery run ID")
    phase2_cmd.add_argument("--hypothesis", help="Registered adaptive hypothesis JSON")
    phase2_fetch = sub.add_parser("phase2-fetch", help="Fetch public phase-two equity/ETF snapshots")
    phase2_fetch.add_argument("--end")
    phase2_predict = sub.add_parser("phase2-predict", help="Freeze phase-two shadow targets; no orders")
    phase2_predict.add_argument("--policy", default="config/phase2_shadows_v1.json")
    phase2_predict.add_argument("--snapshot")
    phase2_predict.add_argument("--sleeve", choices=["aggressive","overnight"], required=True)
    phase2_eval = sub.add_parser("phase2-evaluate", help="Freeze first-observed phase-two batch outcomes")
    phase2_eval.add_argument("--snapshot")
    phase2_audit = sub.add_parser("phase2-attribution", help="Reconcile exact sleeve dollars for a completed Phase 2 run")
    phase2_audit.add_argument("--run", help="Defaults to the most recently completed Phase 2 run")
    sub.add_parser("history", help="Show all serious historical attempts, including failures")
    args = parser.parse_args()
    if args.command == "fetch":
        from .data import fetch
        print(fetch(json.loads((ROOT / args.config).read_text(encoding="utf-8")), args.end))
    elif args.command == "run":
        from .experiment import run
        print(run(json.loads((ROOT / args.config).read_text(encoding="utf-8")), args.snapshot))
    elif args.command == "predict":
        from .forward import predict
        print(predict(ROOT / args.policy, args.snapshot))
    elif args.command == "evaluate-forward":
        from .forward import evaluate
        print(evaluate(args.snapshot))
    elif args.command == "history":
        from .experiment import history
        print(history().to_string(index=False))
    elif args.command == "phase2":
        from .phase2 import run_phase2
        print(run_phase2(json.loads((ROOT / args.config).read_text(encoding="utf-8")), args.snapshot, args.parent, args.hypothesis))
    elif args.command == "phase2-fetch":
        from .phase2_data import fetch_phase2
        print(fetch_phase2(json.loads((ROOT/'config/phase2_study.json').read_text()),args.end))
    elif args.command == "phase2-predict":
        from .phase2_forward import predict
        print(predict(ROOT/args.policy,args.snapshot,args.sleeve))
    elif args.command == "phase2-evaluate":
        from .phase2_forward import evaluate
        print(json.dumps(evaluate(args.snapshot),indent=2))
    elif args.command == "phase2-attribution":
        from .phase2_attribution import audit_sleeves
        print(audit_sleeves(args.run or (ROOT/'state/PHASE2_COMPLETE').read_text().strip()))


if __name__ == "__main__":
    main()
