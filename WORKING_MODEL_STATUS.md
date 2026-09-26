# Working model status

Updated 2026-09-21. New-model course correction accepted. Old sector equal-weight
execution is suspended. Existing PAPER adapter and all previous evidence are retained.

- Execution: 35 paper tests passed. Full-suite run encountered Windows sandbox
  permissions for temporary files; moving the new fixtures into the workspace.
- Data: cached Phase 2 snapshot `291acec28122cd2ee6f8`, 85 previously screened stocks.
- Research: preparing compact rank, ridge and gradient-boosting comparison;
  chronological training through 2017, reused validation 2018–2022.
- Final holdout: 2023 onward remains unopened for scoring. Exact candidate,
  allocation, sizing and acceptance rules must be frozen before the single audit.
- Forward execution: no account allowlist, no local TWS listener, no orders sent.

Next: complete execution tests, run compact model screen, inspect residuals,
freeze finalist, perform one holdout audit, integrate generic immutable targets.

- 2026-09-21T21:07:08.726993+00:00: Registered 20260921T210708_working_model; train ends 2017, validation ends 2022, final holdout sealed.

- 2026-09-21T22:03:23.182647+00:00: Registered 20260921T210708_working_model; train ends 2017, validation ends 2022, final holdout sealed.

- 2026-09-21T22:03:31.079626+00:00: Fitted rank/rule, ridge and 100-tree boosting models on 95928 purged training labels.

- 2026-09-21T22:03:32.172917+00:00: Validation stock_equal: CAGR 11.27%; stress 11.16%; mean 20d 0.98%; P(+10%) 3.6%.

- 2026-09-21T22:03:32.893241+00:00: Validation momentum: CAGR 4.83%; stress 3.28%; mean 20d 0.50%; P(+10%) 4.0%.

- 2026-09-21T22:03:35.371482+00:00: Validation old_sector_baseline: CAGR 10.10%; stress 9.99%; mean 20d 0.90%; P(+10%) 2.6%.

- 2026-09-21T22:03:36.433864+00:00: Validation quality_rule: CAGR 7.64%; stress 5.96%; mean 20d 0.68%; P(+10%) 2.9%.

- 2026-09-21T22:03:37.576520+00:00: Validation ridge10: CAGR 16.84%; stress 13.49%; mean 20d 1.62%; P(+10%) 12.1%.

- 2026-09-21T22:03:38.450569+00:00: Validation boost10: CAGR 13.48%; stress 10.37%; mean 20d 1.36%; P(+10%) 12.8%.

- 2026-09-21T22:03:38.453079+00:00: Family selected on reused validation: ridge10. Testing only top3/5/10 and three sizing rules.

- 2026-09-21T22:03:39.956036+00:00: Validation ridge10_top3_equal: CAGR 2.93%; stress -0.91%; mean 20d 0.93%; P(+10%) 14.4%.

- 2026-09-21T22:03:41.631102+00:00: Validation ridge10_top3_signal: CAGR -1.08%; stress -4.85%; mean 20d 0.75%; P(+10%) 14.1%.

- 2026-09-21T22:03:43.427889+00:00: Validation ridge10_top3_inverse_vol: CAGR 0.69%; stress -3.18%; mean 20d 0.71%; P(+10%) 14.0%.

- 2026-09-21T22:03:45.828626+00:00: Validation ridge10_top5_equal: CAGR 8.92%; stress 5.22%; mean 20d 1.16%; P(+10%) 13.2%.

- 2026-09-21T22:03:47.952338+00:00: Validation ridge10_top5_signal: CAGR 4.89%; stress 1.13%; mean 20d 0.97%; P(+10%) 13.5%.

- 2026-09-21T22:03:49.889188+00:00: Validation ridge10_top5_inverse_vol: CAGR 8.56%; stress 4.73%; mean 20d 1.12%; P(+10%) 13.0%.

- 2026-09-21T22:03:51.768472+00:00: Validation ridge10_top10_signal: CAGR 14.42%; stress 10.91%; mean 20d 1.49%; P(+10%) 12.7%.

- 2026-09-21T22:03:53.509560+00:00: Validation ridge10_top10_inverse_vol: CAGR 16.47%; stress 12.91%; mean 20d 1.58%; P(+10%) 10.4%.

- 2026-09-21T22:03:55.352846+00:00: Validation stability_cadence10: CAGR 9.64%; stress 7.94%; mean 20d 1.10%; P(+10%) 12.3%.

- 2026-09-21T22:03:57.191508+00:00: Validation overnight_ridge_selective: CAGR 0.00%; stress 0.00%; mean 20d 0.00%; P(+10%) 0.0%.

- 2026-09-21T22:03:58.172705+00:00: Validation overnight_ridge_weekly: CAGR 0.00%; stress 0.00%; mean 20d 0.00%; P(+10%) 0.0%.

- 2026-09-21T22:03:59.437791+00:00: Validation overnight_momentum_regime: CAGR 0.00%; stress 0.00%; mean 20d 0.00%; P(+10%) 0.0%.

- 2026-09-21T22:04:01.067799+00:00: FINALIST FROZEN: ridge10; overnight REJECTED; allocation {'aggressive': 1.0, 'overnight': 0.0}. Holdout still sealed.

- 2026-09-21T22:05:31.398025+00:00: Opening final holdout ONCE under the already frozen protocol.

- 2026-09-21T22:05:46.378138+00:00: Holdout ridge10: CAGR 46.98%, stress 42.84%, max DD -23.22%.

- 2026-09-21T22:05:47.477195+00:00: Holdout stock_equal: CAGR 24.38%, stress 24.24%, max DD -18.48%.

- 2026-09-21T22:05:48.431088+00:00: Holdout momentum: CAGR 27.79%, stress 25.95%, max DD -31.06%.

- 2026-09-21T22:05:51.227756+00:00: Holdout old_sector_baseline: CAGR 14.92%, stress 14.80%, max DD -15.55%.

- 2026-09-21T22:05:51.264397+00:00: Final decision: ridge10; candidate passed=True; overnight=None; allocation={'aggressive': 1.0, 'overnight': 0.0}. No more model search.

- 2026-09-22T01:47:52.677255+00:00: COMPETITION_V1 frozen as cbe43010220707651123: ridge10, 100% aggressive book / 0% overnight; five-session opening schedule anchored 2026-09-22.

- 2026-09-22T01:50:12.631846+00:00: Prospective model_3de7b630030340cd frozen for 2026-09-22T13:30:00+00:00; 10 stock targets, 0.50% reference cash. No broker orders.

- 2026-09-22T01:53:17.866210+00:00: REQUIRED MODEL OUTPUTS COMPLETE. New model/holdout/current targets/generic integration done; 129 tests pass. Report: WORKING_MODEL_REPORT.md. Broker commissioning still requires local TWS and independent exact paper account ID. Old sector execution remains suspended.
