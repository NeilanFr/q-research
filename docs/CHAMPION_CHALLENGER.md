# Champion and challenger firewall

Current champion: **COMPETITION_V1 / ridge10**, version
`cbe43010220707651123`; 10 names, equal weighting, five-session opening cadence,
99.5% stock target, 0.5% cash, no overnight sleeve. Frozen files and historical
results remain unchanged. Existing source/artifact hash checks must pass before
every V2 run. A V2 matched-execution comparison is a new diagnostic of the frozen
model, not a replacement for its original historical record.

The model was trained through 2017. Its predictions in earlier years would be
in-sample; V2 excludes the frozen model from 2015-2017 discovery scoring and uses
an independently refitted ridge reference. For outer 2018-2022 diagnostics, both
the original frozen coefficients and the past-only refitted base model are
reported. Technical model combinations use the refitted base, explicitly creating
challengers rather than editing champion coefficients.

Promotion requires all machine-enforced gates: preregistration, discovery,
validation, execution stress, robustness, acceptable multiple-testing evidence,
credible point-in-time data, no detected leakage, an immutable candidate freeze,
and prospective evidence. Missing gates fail closed. This is a higher bar than
the original working-model acceptance rule and does not retroactively rewrite it.

A freeze includes candidate ID, source/config hashes, feature definitions,
parameters, universe, execution assumptions, model artifact and selection rules.
The candidate ID hashes that definition. Edits create a new candidate and require
new preregistration; prospective outcomes must never cause historical retuning.

The prospective protocol requires at least 126 sessions, 25 scheduled rebalances
and 100 closed lots, acceptable drawdown and incremental net returns. These are
minimum evidence requirements, not a promise that six months establishes an edge.
Forecast, decision, order intent, simulated fill, actual PAPER fill and attributed
P&L remain distinct records. Actual fills must originate from the existing PAPER
adapter. Historical backtest fills can never be copied into broker outcome tables.

The current data-vintage and survivorship gates are false. No technical or combined
challenger may bypass them to start a promotable prospective test. The champion's
existing immutable forecast and PAPER readiness workflow are preserved. Further
elapsed market observations cannot be fabricated or compressed into a backtest.
