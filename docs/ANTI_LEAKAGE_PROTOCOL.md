# Anti-leakage protocol

All feature windows end at the completed feature bar. Daily bar source time is
the exchange close, assumed historical availability is close plus one hour, and
simulation decision time is one second later. Actual observed-at timestamps
remain the vendor download times. Historical availability is explicitly assumed,
not inferred from a date or misrepresented as observed. `require_available`
rejects a 2026 observation at a 2015 decision.

`feature_availability.json` in each V2 run enumerates base and technical feature
definitions, source/observation/knowledge/calculation/decision/execution timing,
and their uncertified data-vintage status. Membership, sector, and action facts
use both effective-at and known-at cutoffs in `asof_records`. Future revisions
cannot be projected backward. Missing sector and corporate-action histories
remain missing; the current dataset is not promoted to point-in-time quality.

RSI uses Wilder smoothing with an arithmetic initial seed and full warmup.
Bollinger uses trailing 20-session mean and population standard deviation.
Ichimoku uses 9/26/52 high-low midpoints computed today. Senkou A/B in model rows
are current computable values; display offsets are separate metadata. The Chikou
comparison is current close divided by the close 26 sessions earlier. Neither
negative shifts nor future prices may enter a feature row.

Model labels enter open t+1 and exit open t+11. Training removes every observation
whose exit reaches the evaluation boundary, plus ten additional exchange sessions
of embargo. Winsorization thresholds, means, scales and ridge coefficients fit
only those eligible training observations. Cross-sectional ranks use eligible
stocks in that same completed session. Refit independently for each inner and
outer fold. A fitted 2017 champion is never scored as out-of-sample in 2015-2017.

Adversarial tests mutate and delete all future price/volume rows, then compare
every technical feature, base feature, model transform, prediction, portfolio
target and intended order. Separate metadata tests mutate backdated-but-later-
known membership, sector, split, dividend, merger, symbol-change and spinoff facts.
Tests deliberately inject future-return features, backward Senkou/Chikou shifts,
whole-sample normalization, same-bar timing and current-membership projection;
the invariant assertions must detect them. These tests establish code properties,
not historical vendor-vintage completeness.
