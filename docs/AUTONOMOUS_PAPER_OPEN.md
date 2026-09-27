# PAPER opening readiness, September 28, 2026

COMPETITION_V1 / AGGRESSIVE_V1 / ridge10 remains frozen at version
`cbe43010220707651123`. Its five-session cadence is anchored September 22:
the next eligible rebalance is **Tuesday September 29**, using September 28
completed data. Monday is an exchange session but not a model rebalance.
A missed opening does not shift this schedule.

The September 25 snapshot is `bbca942a0c937e0d83a0` (86 symbols). The existing
prediction command rejects it with `Not a frozen five-session rebalance date`.
No September 28 forecast or executable batch was created. The September 22
batch is stale and must not be reused, re-armed, or executed. Model sources,
artifacts, registered policy and execution envelope are unchanged.

## September 26 inspection

The account-only probe authenticated the exact allowlisted PAPER account on
`127.0.0.1:7497` and passed the broker clock check. TWS reported no stock
positions, open orders, or executions. Its currency ledger contained
CAD 1,000,000 and no USD cash. Account identifiers and raw observations stay
local and ignored. The live USD.CAD request returned an invalid bid during
Saturday inspection, blocking account normalization, broker sizing and what-if.
No quote was substituted and no order, including an FX conversion, was sent.

The complete `paper_cli check` passed all 164 tests. Monday readiness is
**NOT READY**: the cadence and fresh-batch prerequisites fail; live FX, USD
funding, a valid arm and watcher startup evidence are absent. The Saturday test
attestation will expire before Monday, so the launcher refreshes it at startup.
The September 28 readiness-only task passed all 13 Windows settings checks.
Both dry-run and preview reject a missing explicit fresh batch before connecting
or selecting any prior forecast. The broker ledger contains zero orders,
execution batches, executions and what-if requests.

The adapter recognizes both `CashBalance` and `$LEDGER-CashBalance`. It divides
CAD valuation amounts by a fresh live USD.CAD ask (CAD per USD), retains the
original CAD amounts and revalidates conversion before sizing. This valuation
conversion does not exchange currency. Buy funding is additionally capped by
actual broker USD cash, with fees and the existing reserve. CAD-only cash cannot
silently fund USD buys through a currency loan. Missing, negative, conflicting
or unsupported currency balances fail closed. USD funding must be established
and observed before stock execution.

Fresh scoring of the unchanged fitted model is saved locally in
`state/paper_checks/sep25-frozen-target-diagnostic.json`. It has no batch ID and
is not entered in the forecast ledger. CHRW, CTSH, GRMN, HPQ, IBM, MSFT, NTAP,
NVDA, ORCL and VOD each have 9.95% reference weight, with 0.5% reference cash.
These are diagnostic weights, not Monday orders or a Tuesday forecast.

## Existing command sequence

After the required session's publication lag (September 28 at 14:00 Vancouver /
17:00 New York for the next rebalance), from the repository:

```powershell
.\.venv\Scripts\python.exe -m quantlab.competition fetch
.\.venv\Scripts\python.exe -m quantlab.competition predict
```

`predict` loads frozen fitted artifacts without training or retuning. It enforces
fresh completed data, the publication lag, prospective issuance, the registered
cadence and one immutable forecast per policy/opening. Do not run `freeze`,
model fitting, legacy sector `quantlab predict`, or clock/anchor overrides.
`competition evaluate` is separate outcome bookkeeping, not needed for targets.

After a valid batch exists, select its exact ID and session in
`config/paper_schedule.json` before commissioning. Currently this configuration
has session September 28, `batch=null`, and `mode=readiness`.

```powershell
.\.venv\Scripts\python.exe -m quantlab.paper_cli check
.\.venv\Scripts\python.exe -m quantlab.paper_cli inspect
.\.venv\Scripts\python.exe -m quantlab.paper_cli scheduled-opening
.\.venv\Scripts\python.exe -m quantlab.paper_cli readiness --session 2026-09-28
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/verify_paper_task.ps1
```

`inspect` without a batch checks the account and current holdings; it never
selects the latest forecast. `dry-run` and `preview` require an explicit fresh
batch. Preview also requires the frozen 06:10 inclusive to 06:25 exclusive
Vancouver window and live quotes. No weekend what-if bypass exists.
Readiness reports separate failures for invalid cadence, missing batch,
unavailable FX, absent arm, expiring tests and a readiness-only task in
`state/paper_checks/TOMORROW_PREOPEN_READINESS.json`.

## One-time startup and authorization

Installer and verifier read the requested date and mode through
`paper_cli scheduled-opening`. The September 28 task starts at 05:45 Vancouver,
expires at 07:00 and runs **readiness only**. It cannot trade or generate targets.
The launcher refreshes the complete test attestation, writes private timestamped
logs, propagates blocked readiness exits and rejects expired sessions. Existing
tasks are not overwritten silently. The runner has no hardcoded September 22 batch.

For a later eligible session, `mode=watch` requires a valid exact batch matching
the opening. The complete test attestation lasts 24 hours. The existing local
`paper_cli arm --batch <exact-id>` still requires the operator's Read-Only API
confirmation. No arm was created on September 26. Source, private config,
script, SDK or dependency changes invalidate tests and prior arms.

All existing exact-account, live-quote, LMT/OPG, concentration, cash, liquidity,
what-if pacing, stale-batch and replay guards remain. Authorization is consumed
before reservation and wire calls. No LIVE fallback, catch-up, automatic retry
after ambiguous submission, or funding from expected sales is permitted.
Windows must remain logged in and TWS PAPER authenticated.

References: [NYSE 2026 calendar](https://www.nyse.com/publicdocs/nyse/ICE_NYSE_2026_Yearly_Trading_Calendar.pdf),
[IBKR currency callback prefix](https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/per-currency-account-value-prefix),
[IBKR what-if margin checks](https://interactivebrokers.github.io/tws-api/margin.html).
