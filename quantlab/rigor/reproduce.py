"""Two preregistered numerical replications from the archived executable source."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from quantlab.data import ROOT, save_json
from .governance import append_event
from .study import LEDGER, read_config


def run(parent):
    cfg = read_config()
    eid = cfg["study_id"]+"/REPRODUCIBILITY_01"
    spec = {"experiment_id": eid, "hypothesis": "Archived source and the same immutable inputs reproduce 2018 champion and Bollinger daily returns within CSV roundoff.",
            "rationale": "Numerical reproducibility attestation; not another strategy selection attempt.",
            "feature_family": "REPRODUCTION_ONLY", "parameters": {"year": 2018, "strategies": ["champion", "bb_reversal"], "atol": 1e-12},
            "parameter_search_space": "None", "entry_rule": "Exact archived rules", "exit_rule": "Exact archived rules",
            "position_sizing": "Exact archived sizing", "rebalance_frequency": 5, "benchmark": "Original archived daily results",
            "cost_assumptions": cfg["execution"], "train_period": "Exact archived fold fits",
            "validation_period": "2018 only, already observed", "holdout_status": cfg["holdout_status"],
            "maximum_trials_allowed": 1, "maximum_simulations": 2}
    append_event(LEDGER, "registered", spec)
    code = r'''
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd
root, parent = map(Path, sys.argv[1:])
sys.path.insert(0, str(parent/'source'))
from quantlab.data import digest
from quantlab.rigor.strategies import make_panel, score_fold, weights_for
from quantlab.rigor.execution import simulate
for rel, expected in json.loads((parent/'source_hashes.json').read_text()).items():
    assert digest((parent/'source'/rel).read_bytes()) == expected
cfg=json.loads((parent/'config.json').read_text())
data=root/'data/snapshots'/cfg['snapshot']
manifest=json.loads((data/'manifest.json').read_text())
assert digest((data/'bars.csv').read_bytes()) == manifest['bars_sha256']
for source in manifest['raw_sources']:
    assert digest((root/source['file']).read_bytes()) == source['sha256']
bars=pd.read_csv(data/'bars.csv',parse_dates=['date'])
bars=bars[bars.date<='2022-12-31']
champion=json.loads((root/cfg['universe']).read_text())
panel=make_panel(bars,champion['universe'],cfg)
scores,models=score_fold(panel,cfg,'2018-01-01',champion)
saved=json.loads((parent/'fold_models.json').read_text())['2018']
for block,m in models.items():
    for key in ('coef','mean','scale','lower','upper'):
        np.testing.assert_allclose(m[key],saved[block][key],rtol=0,atol=1e-12)
result={}
for name in ('champion','bb_reversal'):
    weights=weights_for(panel,scores,name,cfg)
    sim=simulate(panel,weights,'2018-01-01','2018-12-31',cfg['execution'])
    expected=pd.read_csv(parent/'validation'/name/'daily.csv',index_col=0,parse_dates=True).loc['2018']
    for key in ('nav_before','nav','net_return','turnover','cost_fraction','gross_exposure'):
        np.testing.assert_allclose(sim['daily'][key],expected[key],rtol=0,atol=1e-12)
    result[name]={'sessions':len(expected),'max_daily_return_absolute_error':float(np.max(np.abs(sim['daily'].net_return-expected.net_return)))}
print(json.dumps({'passed':True,'archived_source_verified':True,'raw_data_verified':True,'fits_reproduced':True,'simulations':2,'results':result}))
'''
    # -c imports use the explicitly inserted archive path before the working tree.
    completed = subprocess.run([sys.executable, "-c", code, str(ROOT), str(parent)], capture_output=True, text=True, cwd=parent)
    if completed.returncode:
        append_event(LEDGER, "reproduction_failed", {"experiment_id": eid, "error": completed.stderr[-4000:]})
        raise RuntimeError(completed.stderr)
    result = json.loads(completed.stdout)
    save_json(parent/"reproducibility.json", result)
    append_event(LEDGER, "reproduction_completed", {"experiment_id": eid, **result})
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    run(ROOT/"runs"/sys.argv[1])
