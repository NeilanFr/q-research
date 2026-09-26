# IBKR boundary: paper only

Current implementation: the official SDK 10.50.2 and generic PAPER execution
modules `paper.py`, `tws.py`, `paper_cli.py` are now installed/implemented.
The old `broker.py` remains read-only. The new frozen model, 129 passing tests,
current account-configuration blockers and commissioning commands are documented
in [WORKING_MODEL_REPORT.md](../WORKING_MODEL_REPORT.md). No broker connection or
order transmission has occurred. The inspection history below is preserved.

Inspected on 2026-09-19. Local Python is 3.14.2. `ibapi`, `ib_async`, and
`ib_insync` were absent. `C:\ntws\ntws.exe` was running; its file metadata identifies
**IBKR Desktop 3.6c**, not Trader Workstation. No listener was found on localhost
7496, 7497, 4001, or 4002. The usual TWS/API installation directories were absent.
This does not exclude a custom installation or port. No IBKR/TWS configuration
environment-variable names were found; no credential values were printed.

The research loop subsequently created a separate Python 3.13.15 environment.
It still does not contain the official IBKR SDK and has not connected to an account.

IBKR's current documentation requires **Trader Workstation or IB Gateway** for
the socket API. Python's documented minimum is 3.11; 3.14 compatibility with a
specific installed SDK remains untested. Get matching TWS/API versions from the
[official API download](https://www.interactivebrokers.com/docs/tws-api/doc/download-the-tws-api/introduction).
IBKR does not support the package-index `ibapi` distribution as its official
release. The research environment does not install or depend on the broker SDK.
See [platform requirement](https://www.interactivebrokers.com/docs/tws-api/doc/download-tws-or-ib-gateway/download-tws-or-ib-gateway)
and [language requirements](https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/requirements).

## What exists

`quantlab/broker.py` has a tested, fail-closed account policy and an optional
read-only snapshot adapter. The adapter uses IBKR's `managedAccounts` response
and waits for `positionEnd` before accepting the positions snapshot.
It makes no market-data request, submits no orders, and disconnects afterward.
Broker connectivity is independent of every historical experiment.

Order transmission is absent. `submit_order()` always raises, including after a
successful policy check. A future transport must check the actual connected
account immediately before each submission and after each reconnection;
a previously passing check is not reusable authorization.

## Connecting when the official SDK is available

1. Log in to the competition **paper** account in TWS or IB Gateway. Keep the
   broker's **Read-Only API** setting enabled and enable socket API access.
2. Independently verify the actual paper account ID in the broker's paper-account
   administration/UI. Record that exact ID in a private allowlist. Do not derive
   the allowlist from whatever the API happens to report. An account prefix,
   port number, or account nickname is not verification.
3. Install the Python source from IBKR's official downloaded SDK into the project
   environment, e.g. `.\.venv\Scripts\python.exe -m pip install "C:\TWS API\source\pythonclient"`.
4. Use this read-only example with the independently verified ID and the port
   configured in TWS. The illustrative ID below is not a usable account:

```python
from quantlab.broker import PaperOnlyPolicy, read_only_snapshot

policy = PaperOnlyPolicy(
    expected_account="DU123456",
    paper_account_allowlist=frozenset({"DU123456"}),
    execution_enabled=False,
)
snapshot = read_only_snapshot(policy, port=7497)
# snapshot["positions"] is a broker observation, not a desired portfolio.
```

This adapter rejects sessions exposing more than one account, including two
allowlisted accounts. Unknown accounts, incomplete callbacks, disconnections,
missing configuration, and positions from another account are errors. Keep the
snapshot private because it contains account IDs and positions. The optional
SDK adapter has not been exercised against a broker in this environment; the
policy and no-transmission behavior have automated tests.

The allowlist records the operator's independent verification; the API account
list alone does not prove paper status. [IBKR's configuration documentation](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/)
explains that conventional paper/live ports can be changed, and that the
Read-Only setting blocks API orders. Relevant callbacks are documented in
[managed accounts](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/managed-accounts/receive-managed-accounts)
and [positions](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/positions/receive-positions).

## Records for forward use

Keep these concepts separate: a saved prediction; desired portfolio weights;
an order intent derived from desired minus observed holdings; an actual submitted
order with broker ID; fills with execution IDs, quantities, prices and fees;
and broker-observed resulting positions. Research backtest trades are simulated
accounting records and must never be labelled broker submissions or fills.

At this milestone the research process produces predictions/targets, and this
boundary can observe positions once connected. The submission and fill ledger
must remain empty until a real paper adapter is explicitly implemented and
tested. No broker access, subscription entitlement, account balance, or paper
account ID has been verified yet. [IBKR notes](https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/limitations/paper-trading)
that paper execution is simulated and may differ from live execution.
