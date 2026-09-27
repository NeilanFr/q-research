"""The finite V2 hypothesis vocabulary; no dynamic strategy evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantlab.working_model import features, target_weights
from quantlab.data import wide
from .indicators import technical
from .execution import decision_times
from .validation import fit_ridge, predict


TA_SETS = {"rsi": ["rsi_14", "rsi_14_percentile", "rsi_14_change", "rsi_14_slope", "rsi_14_acceleration"],
           "bb": ["bb_pct_b", "bb_bandwidth_percentile", "bb_expansion", "bb_distance_center"],
           "ichimoku": ["cloud_distance", "cloud_thickness", "cloud_slope", "tenkan_kijun_atr", "trend_persistence", "chikou_comparison"]}


RULES = {
    "rsi_reversal": "Rank -RSI; no hard oversold threshold; top k among eligible stocks.",
    "rsi_trend": "Rank -RSI where price > trailing 200-session mean and RSI < 50.",
    "rsi_continuation": "Rank RSI where RSI > 70.",
    "bb_reversal": "Rank negative Bollinger %B.",
    "bb_trend_reversal": "Rank negative %B where price > trailing 200-session mean.",
    "bb_breakout": "Rank %B where %B > 1.",
    "bb_squeeze": "Rank %B where prior bandwidth percentile < .2, bandwidth expands, and %B > 1.",
    "ichimoku": "Rank ATR-normalized distance above causal cloud where Tenkan > Kijun and price > cloud top.",
    "ichimoku_market": "Ichimoku rule with SPY > its trailing 200-session mean.",
    "ichimoku_rsi": "Rank -RSI14 inside positive causal Ichimoku trend, RSI < 50.",
    "bb_ichimoku": "Rank %B above upper band inside positive causal Ichimoku trend.",
    "ridge_rsi_veto": "Past-only refitted ridge ranking; veto RSI14 >= 70.",
    "ridge_ichimoku_veto": "Past-only refitted ridge ranking; require positive causal Ichimoku trend.",
    "ridge_bb_veto": "Past-only refitted ridge ranking; veto bandwidth percentile >= .8.",
    "base_plus_rsi": "Training-only standardized ridge on base + RSI feature block.",
    "base_plus_bb": "Training-only standardized ridge on base + Bollinger block.",
    "base_plus_ichimoku": "Training-only standardized ridge on base + causal Ichimoku block.",
    "base_plus_all": "Training-only standardized ridge on base + all three technical blocks."
}


def make_panel(bars, symbols, cfg, perturb=False):
    panel = features(bars, symbols, cfg)
    c, raw = panel["closes"], panel["raw"]
    high, low = [wide(bars, f, symbols)*c/raw for f in ("high", "low")]
    if perturb:
        p = cfg["perturbation"]
        ta = technical(c, high, low, tuple(p["rsi_periods"]), *p["bollinger"], tuple(p["ichimoku"]))
        for old, new in zip((7, 14, 21), p["rsi_periods"]):
            for key in list(ta):
                if key == f"rsi_{new}" or key.startswith(f"rsi_{new}_"):
                    ta[key.replace(f"rsi_{new}", f"rsi_{old}", 1)] = ta[key]
    else:
        ta = technical(c, high, low)
    # Historical roster was public by March 2013. Availability screening remains biased.
    panel["eligible"] &= pd.DataFrame(np.broadcast_to((c.index >= pd.Timestamp("2013-03-01"))[:, None], c.shape), index=c.index, columns=c.columns)
    # Require complete indicator warmup for every strategy, including baselines.
    ready = np.isfinite(np.stack([ta[n].to_numpy() for names in TA_SETS.values() for n in names], axis=2)).all(axis=2)
    panel["eligible"] &= ready
    panel["ta"] = ta
    panel["clock"] = decision_times(c.index)
    spy = wide(bars, "adj_close", ["SPY"])
    panel["market_trend"] = spy.SPY/spy.SPY.rolling(200).mean()-1
    panel["market_vol"] = spy.SPY.pct_change(fill_method=None).rolling(20).std()
    panel["spy"] = {"opens": wide(bars, "adj_open", ["SPY"]), "closes": spy,
                    "adv": (wide(bars, "close", ["SPY"])*wide(bars, "volume", ["SPY"])).rolling(20).mean(), "clock": panel["clock"]}
    panel["perturb"] = perturb
    return panel


def model_inputs(panel, block="base"):
    if block == "base":
        return panel["x"]
    names = [n for group in TA_SETS.values() for n in group] if block == "all" else TA_SETS[block]
    extra = np.stack([panel["ta"][n].to_numpy() for n in names], axis=2)
    # Eligibility masks missing warmup before any fit; no future-aware imputation.
    return np.concatenate([panel["x"], extra], axis=2)


def score_fold(panel, cfg, evaluation_start, champion, blocks=("base", "rsi", "bb", "ichimoku", "all")):
    e, c, ta = panel["eligible"], panel["closes"], panel["ta"]
    scores, models = {}, {}
    for block in blocks:
        x = model_inputs(panel, block)
        model = fit_ridge(x, panel["opens"], e, cfg["train_start"], evaluation_start,
                          cfg["horizon"], cfg["embargo_sessions"], cfg["perturbation"]["ridge_penalty"] if panel["perturb"] else cfg["ridge_penalty"])
        scores["refit_ridge" if block == "base" else f"base_plus_{block}"] = pd.DataFrame(predict(x, model), index=c.index, columns=c.columns)
        models[block] = model
    ridge = scores["refit_ridge"]
    scores.update(stock_equal=c*0, momentum=panel["features"]["momentum"], reversal=panel["features"]["reversal"], no_trade=c*0)
    # Deterministic per-session RNG makes deletion/append of future dates invariant.
    random, permuted, jittered = [], [], []
    x = panel["x"]
    for i, date in enumerate(c.index):
        rng = np.random.default_rng(cfg["seed"]+int(date.value//86400000000000))
        random.append(rng.random(c.shape[1]))
        permuted.append(predict(x[i, rng.permutation(c.shape[1])], models["base"]))
        lag = int(rng.integers(1, 21))
        jittered.append(ridge.iloc[max(0, i-lag)].to_numpy())
    scores.update(random=pd.DataFrame(random, index=c.index, columns=c.columns),
                  permuted_features=pd.DataFrame(permuted, index=c.index, columns=c.columns),
                  randomized_timestamps=pd.DataFrame(jittered, index=c.index, columns=c.columns),
                  ridge_delay1=ridge.shift(1), ridge_delay5=ridge.shift(5))
    # Frozen champion uses its ORIGINAL eligibility semantics and coefficients.
    coef = np.asarray(champion["training"]["ridge"])
    scores["champion"] = pd.DataFrame(coef[0]+panel["x"]@coef[1:], index=c.index, columns=c.columns)
    trend = (c > ta["cloud_top"]) & (ta["tenkan"] > ta["kijun"])
    for spec in cfg["challengers"]:
        rule, p = spec["rule"], spec.get("period", 14)
        rsi = ta[f"rsi_{p}"]
        b = ta["bb_pct_b"]
        rules = {
            "rsi_reversal": -rsi,
            "rsi_trend": (-rsi).where((ta["trend_200"] > 0) & (rsi < 50)),
            "rsi_continuation": rsi.where(rsi > 70),
            "bb_reversal": -b,
            "bb_trend_reversal": (-b).where(ta["trend_200"] > 0),
            "bb_breakout": b.where(b > 1),
            "bb_squeeze": b.where((ta["bb_bandwidth_percentile"].shift(1) < .2) & (ta["bb_expansion"] > 0) & (b > 1)),
            "ichimoku": ta["cloud_distance"].where(trend),
            "ichimoku_market": ta["cloud_distance"].where(trend).where(pd.DataFrame(np.broadcast_to((panel["market_trend"] > 0).to_numpy()[:, None], c.shape), index=c.index, columns=c.columns)),
            "ichimoku_rsi": (-ta["rsi_14"]).where(trend & (ta["rsi_14"] < 50)),
            "bb_ichimoku": b.where(trend & (b > 1)),
            "ridge_rsi_veto": ridge.where(ta["rsi_14"] < 70),
            "ridge_ichimoku_veto": ridge.where(trend),
            "ridge_bb_veto": ridge.where(ta["bb_bandwidth_percentile"] < .8)
        }
        if rule in rules:
            scores[spec["id"]] = rules[rule]
    return scores, models


def weights_for(panel, scores, name, cfg, k=None, excluded=()):
    if name == "spy_buy_hold":
        return panel["spy"]["closes"]*0+cfg["gross"]
    eligible = panel["eligible"].copy()
    if excluded:
        eligible.loc[:, list(excluded)] = False
    if name == "no_trade":
        return panel["closes"]*0
    return target_weights(scores[name], eligible, panel["features"]["volatility"],
                          None if name == "stock_equal" else k or cfg["top_k"], "equal", cfg["gross"], cfg["max_weight"])
