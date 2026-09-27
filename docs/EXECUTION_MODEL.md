# Execution model

V2 uses the same shares-and-cash simulator for every strategy and reference.
It is independent of the existing broker stack. Dollar reference notional is
USD 1,000,000 per independent annual fold. No assumptions about the PAPER account's
CAD balance or USD funding enter these research curves.

A completed daily bar is assumed known one hour after the exchange close.
The target portfolio and intended adjusted-unit quantity are computed then using
the prior close, existing holdings, cash and prior 20-session raw dollar ADV.
The earliest fill is the next exchange session's open. A favourable opening gap
cannot enlarge an order. Buys can be reduced for actual cash affordability;
participation can reduce actual fills. Residual cash and holdings are retained.
This corrects an optimistic execution assumption in the historical stock
simulators, which sized exact target weights using the eventual opening NAV.
Those historical outputs remain archived and are not silently recalculated.

Base cost per side is 2 bp half-spread, 0.5 bp commission proxy, 2.5 bp slippage,
plus square-root participation impact of 5 bp at 1% ADV. These are assumptions,
not measured spreads or an IBKR commission schedule. Intended trades are capped
at 1% prior ADV. Terminal liquidation is predeclared for each annual fold's last
close, charged the same costs and impact, and currently assumes full terminal
liquidity. This terminal-liquidity simplification is disclosed, not a broker fill
guarantee. Full cash and attributed P&L must reconcile after every session.

NYSE calendar sessions and early closes determine timing. Missing bars or invalid
prices fail the run; there is no forward-filled price, zero delisting return,
or silent shortened terminal period. Daily bars do not identify every halt or
auction nonfill. Predeclared falsifications include 2x/3x costs, extra slippage,
one/three-session delay, missed rebalances, filling at the close after a missed
opening auction, higher impact and alternate cadence. The fill model remains
an approximation requiring prospective observation.

All OHLC values use the same adjusted-price basis. Adjusted units proxy total
return; they are not raw shares, dividend cash flows or an audited distribution
ledger. Changing a future corporate-action record must not rewrite old decision
inputs. The available revised vintage does not establish that property at the
vendor level. Mergers, bankruptcies, spinoffs and delisted terminal proceeds remain
material limitations.

The legacy PAPER adapter retains exact-account checks, no live fallback, stale
forecast rejection, explicit single-batch arming, idempotency/replay protection,
reconciliation, actual USD funding and CAD/USD normalization. No V2 path imports
or calls broker transmission code. An exchange close is not an order submission
cutoff; [NYSE auction rules](https://www.nyse.com/trade/auctions) describe separate
auction processes. The strategy never uses a completed close to enter that same
closing auction.
