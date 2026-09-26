# September 22, 2026 PAPER opening

COMPETITION_V1 is frozen. The sole executable book is AGGRESSIVE_V1 / ridge10,
version `cbe43010220707651123`. Overnight is rejected. This commissioning path
does not fetch data, fit models, select strategies or generate forecasts.

The immutable forecast is `model_3de7b630030340cd`. Its decision was recorded at
2026-09-22 01:50:11.589605 UTC. Entry is September 22 at 09:30 New York / 06:30
Vancouver / 13:30 UTC. The frozen submission interval is 06:10 inclusive through
06:25 exclusive Vancouver time. Orders remain LMT/OPG with the existing quote,
spread, collar, cash, concentration, capacity and what-if checks. Limits can
remain unfilled; there is no intraday chase or retroactive execution.

## Local commissioning

Run these commands from `C:\Users\Neila\quant-research`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/configure_paper.ps1
.\.venv\Scripts\python.exe -m quantlab.paper_cli check
.\.venv\Scripts\python.exe -m quantlab.paper_cli inspect --batch model_3de7b630030340cd
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start_paper_bot.ps1 -MonitorOnly -MaxSeconds 30
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_paper_task.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/verify_paper_task.ps1
```

The configuration helper refuses to overwrite an existing configuration. Enter
the independently verified DU PAPER account and actual TWS API port locally.
It fixes localhost and client ID 92226, allowlists exactly that account and
keeps `execution_enabled=false`. The API account must match exactly. A port
number, DU prefix or broker AccountType alone does not prove PAPER status.

After tests and inspection pass, uncheck **TWS > Global Configuration > API >
Settings > Read-Only API**. Then explicitly run:

```powershell
.\.venv\Scripts\python.exe -m quantlab.paper_cli arm --batch model_3de7b630030340cd
```

Read the local confirmation and type `ARM PAPER`. This command connects only to
inspect balances, positions, orders, executions, contracts and the server clock;
it never invokes order transport, including what-if. It then records an immutable
single-batch authorization. The persistent execution flag stays false. The
operator's Read-Only confirmation is recorded as such, not presented as an API
observation; successful what-if responses are still required tomorrow.

```powershell
.\.venv\Scripts\python.exe -m quantlab.paper_cli readiness --batch model_3de7b630030340cd
```

Readiness performs fresh non-transmitting checks and writes
`state/paper_checks/TOMORROW_PREOPEN_READINESS.json`. Every prerequisite has a
PASS/FAIL result. Missing connection, config, tests, startup evidence, task or
arm yields NOT READY. Changing source, configuration, scripts, SDK or dependency
versions invalidates the test attestation and the arm. An expired or consumed
batch cannot be armed again by this interface.

## Autostart and safety

Task `QuantResearch-PAPER-20260922` starts at 05:45 Vancouver with the logged-in
Windows user's interactive token and limited privileges. It does not depend on
an open terminal. It starts when available after a missed trigger, wakes the
machine if Windows permits, rejects parallel instances and expires at 07:00;
Windows deletes it one hour after expiry. The watcher exits after 06:35. Its
05:45 trigger is not a trading permission: time checks still reject early/late
orders. Windows must remain logged in and TWS PAPER authenticated. Credentials
are never stored or automated here.

`scripts/start_paper_bot.ps1` launches the repository virtualenv and writes
timestamped startup/stdout/stderr logs under `state/private/logs/`. A launcher
file lock and a separate watcher OS lock prevent duplicate processes. Nonzero
startup failures are logged and propagated.

The watcher uses only the specified armed batch, checks connectivity repeatedly,
reconciles before action, obtains fresh live quotes and qualified contracts,
sizes target-minus-current-position deltas, validates a dry run and obtains
every exact IBKR what-if preview with the frozen 61-second pacing. It requires
enough remaining time for the previews; it does not relax pacing or the window.

Authorization is consumed durably before batch reservation and before any
actual wire call. Source/config, account and timing checks also run at the wire
boundary. A crash, rejected or ambiguous transmission can never cause automatic
resending. After consumption the watcher only reconciles acknowledgements,
executions and resulting positions. Expiry also disarms the batch. Before any
possible transmission, a fresh connection and complete reconciliation allow a
safe retry of a transient connectivity/quote failure. LIVE accounts are rejected
before connecting and at transmission.

Official references: [IBKR API configuration and Read-Only API](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/)
and [Windows StartWhenAvailable](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-startwhenavailable-settingstype-element).
