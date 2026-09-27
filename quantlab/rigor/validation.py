"""Purged past-only fits, training-only transforms, date-block inference."""
from __future__ import annotations

from statistics import NormalDist
import numpy as np
import pandas as pd


def training_mask(index, start, evaluation_start, horizon=10, embargo=10):
    if horizon < 1 or embargo < horizon:
        raise ValueError("Embargo must cover the forward horizon")
    boundary = int(index.searchsorted(pd.Timestamp(evaluation_start)))
    exits = np.arange(len(index)) + horizon + 1
    # Last label exit precedes the embargo gap, not merely the feature cutoff.
    return (index >= pd.Timestamp(start)) & (exits < boundary-embargo)


def fit_ridge(x, opens, eligible, start, evaluation_start, horizon=10, embargo=10, penalty=.03):
    if x.shape[:2] != opens.shape or eligible.shape != opens.shape:
        raise ValueError("Model input alignment")
    labels = opens.shift(-(horizon+1))/opens.shift(-1)-1
    labels = labels.sub(labels.where(eligible).mean(axis=1), axis=0).clip(-.5, .5).to_numpy()
    dates = training_mask(opens.index, start, evaluation_start, horizon, embargo)
    valid = dates[:, None] & eligible.to_numpy() & np.isfinite(labels) & np.isfinite(x).all(axis=2)
    xx, yy = x[valid], labels[valid]
    if len(yy) < 100:
        raise ValueError("Insufficient purged training observations")
    lower, upper = np.quantile(xx, [.01, .99], axis=0)
    xx = np.clip(xx, lower, upper)
    mean, scale = xx.mean(axis=0), xx.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.)
    matrix = np.column_stack([np.ones(len(xx)), (xx-mean)/scale])
    reg = np.eye(matrix.shape[1])*penalty
    reg[0, 0] = 0
    coef = np.linalg.solve(matrix.T@matrix/len(yy)+reg, matrix.T@yy/len(yy))
    last = np.flatnonzero(valid.any(axis=1))[-1]
    return {"lower": lower, "upper": upper, "mean": mean, "scale": scale, "coef": coef,
            "rows": len(yy), "last_feature": str(opens.index[last].date()),
            "last_label_exit": str(opens.index[last+horizon+1].date()),
            "evaluation_start": evaluation_start, "horizon": horizon, "embargo": embargo}


def predict(x, model):
    z = (np.clip(x, model["lower"], model["upper"])-model["mean"])/model["scale"]
    return model["coef"][0]+z@model["coef"][1:]


def assert_invariant(original, changed):
    """Used on features, predictions, eligibility, targets and intended orders."""
    np.testing.assert_allclose(np.asarray(original), np.asarray(changed), rtol=0, atol=0, equal_nan=True)


def block_inference(excess, samples=1000, block=20, seed=260926):
    """Paired circular date blocks; max centered mean is Reality-Check-style, not SPA."""
    a = np.asarray(excess, dtype=float)
    if a.ndim == 1:
        a = a[:, None]
    if len(a) < block or not np.isfinite(a).all():
        raise ValueError("Aligned finite daily excess returns required")
    rng = np.random.default_rng(seed)
    means = np.empty((samples, a.shape[1]))
    for b in range(samples):
        starts = rng.integers(0, len(a), size=int(np.ceil(len(a)/block)))
        idx = ((starts[:, None]+np.arange(block)) % len(a)).ravel()[:len(a)]
        means[b] = a[idx].mean(axis=0)
    observed = a.mean(axis=0)
    maximum_null = np.maximum((means-observed).max(axis=1), 0)
    p = (1+(maximum_null[:, None] >= observed).sum(axis=0))/(samples+1)
    return {"annual_mean": observed*252, "lower": np.quantile(means, .025, axis=0)*252,
            "upper": np.quantile(means, .975, axis=0)*252, "familywise_p": p,
            "global_p": float((1+(maximum_null >= max(observed.max(), 0)).sum())/(samples+1))}


def sharpe_evidence(returns, trial_sharpes, number_trials):
    """IID moment approximation; serial dependence and unknown prior search limit it."""
    r = np.asarray(returns, dtype=float)
    sd = r.std(ddof=1)
    if len(r) < 20 or sd <= 0:
        return {"psr_iid": None, "dsr_iid": None}
    sr = r.mean()/sd
    z = (r-r.mean())/sd
    skew, kurt = np.mean(z**3), np.mean(z**4)
    denom = np.sqrt(max(1-skew*sr+(kurt-1)*sr**2/4, 1e-12))
    normal = NormalDist()
    n = max(2, int(number_trials))
    dispersion = float(np.std(trial_sharpes, ddof=1)) if len(trial_sharpes) > 1 else 0.
    expected = dispersion*((1-.5772156649)*normal.inv_cdf(1-1/n)+.5772156649*normal.inv_cdf(1-1/(n*np.e)))
    return {"psr_iid": normal.cdf(sr*np.sqrt(len(r)-1)/denom),
            "dsr_iid": normal.cdf((sr-expected)*np.sqrt(len(r)-1)/denom),
            "dsr_daily_hurdle": expected, "assumed_trials": n}
