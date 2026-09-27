"""Trailing technical features. Chart display coordinates never enter features."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close, period=14):
    """Wilder RSI with arithmetic seed of the first n price changes."""
    if type(period) is not int or period < 2:
        raise ValueError("RSI period must be >=2")
    delta = close.diff().to_numpy()
    result = np.full(delta.shape, np.nan)
    for j in range(delta.shape[1]):
        gain = loss = np.nan
        consecutive = 0
        gains, losses = [], []
        for i, d in enumerate(delta[:, j]):
            if not np.isfinite(d):
                gain = loss = np.nan
                consecutive, gains, losses = 0, [], []
                continue
            up, down = max(d, 0), max(-d, 0)
            consecutive += 1
            if consecutive <= period:
                gains.append(up)
                losses.append(down)
                if consecutive < period:
                    continue
                gain, loss = np.mean(gains), np.mean(losses)
            else:
                gain = (gain * (period - 1) + up) / period
                loss = (loss * (period - 1) + down) / period
            result[i, j] = 50 if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return pd.DataFrame(result, index=close.index, columns=close.columns)


def trailing_percentile(frame, window=126):
    return frame.rolling(window, min_periods=window).rank(pct=True)


def technical(close, high, low, rsi_periods=(7, 14, 21), bb_period=20, bb_sigma=2., ichi=(9, 26, 52)):
    for frame in (high, low):
        if not frame.index.equals(close.index) or not frame.columns.equals(close.columns):
            raise ValueError("OHLC alignment")
    if close.index.has_duplicates or not close.index.is_monotonic_increasing:
        raise ValueError("Chronological unique bars required")
    if not (0 < ichi[0] < ichi[1] < ichi[2]) or bb_period < 2 or bb_sigma <= 0:
        raise ValueError("Invalid technical parameters")
    out = {}
    for p in rsi_periods:
        v = rsi(close, p)
        out.update({f"rsi_{p}": v, f"rsi_{p}_percentile": trailing_percentile(v),
                    f"rsi_{p}_change": v.diff(), f"rsi_{p}_slope": (v-v.shift(5))/5,
                    f"rsi_{p}_acceleration": v.diff().diff(), f"rsi_{p}_rank": v.rank(axis=1, pct=True)})
        for level in (30, 50, 70):
            out[f"rsi_{p}_distance_{level}"] = v - level
    center = close.rolling(bb_period).mean()
    sigma = close.rolling(bb_period).std(ddof=0)
    upper, lower = center + bb_sigma*sigma, center - bb_sigma*sigma
    width = (upper-lower)/center
    out.update(bb_center=center, bb_sigma=sigma, bb_upper=upper, bb_lower=lower,
               bb_pct_b=(close-lower)/(upper-lower).replace(0, np.nan), bb_bandwidth=width,
               bb_bandwidth_percentile=trailing_percentile(width), bb_expansion=width.diff(),
               bb_contraction=-width.diff(), bb_distance_center=(close-center)/sigma.replace(0, np.nan),
               bb_distance_upper=(close-upper)/sigma.replace(0, np.nan),
               bb_distance_lower=(close-lower)/sigma.replace(0, np.nan))
    tenkan = (high.rolling(ichi[0]).max()+low.rolling(ichi[0]).min())/2
    kijun = (high.rolling(ichi[1]).max()+low.rolling(ichi[1]).min())/2
    a = (tenkan+kijun)/2
    b = (high.rolling(ichi[2]).max()+low.rolling(ichi[2]).min())/2
    top, bottom = a.where(a >= b, b), a.where(a <= b, b)
    tr = np.maximum(high-low, np.maximum((high-close.shift()).abs(), (low-close.shift()).abs()))
    atr = tr.rolling(14).mean().replace(0, np.nan)
    above = (close > top).where(top.notna()).astype(float)
    out.update(tenkan=tenkan, kijun=kijun, senkou_a=a, senkou_b=b, cloud_top=top, cloud_bottom=bottom,
               cloud_distance=(close-top)/atr, cloud_thickness=(top-bottom)/atr,
               cloud_thickness_percentile=trailing_percentile((top-bottom)/atr),
               cloud_slope=((a+b)/2).diff(5)/atr, tenkan_kijun=tenkan-kijun,
               tenkan_kijun_atr=(tenkan-kijun)/atr, trend_persistence=above.rolling(20).mean(),
               breakout_persistence=above.rolling(5).mean(),
               chikou_comparison=close/close.shift(ichi[1])-1, atr=atr,
               trend_200=close/close.rolling(200).mean()-1)
    return out


def display_metadata(sessions, calculated_position, displacement=26):
    """Display-only helper. Chikou is still calculated now, never known in the past."""
    i = calculated_position
    return {"calculated_at": str(sessions[i]),
            "senkou_displayed_at": str(sessions[i+displacement]) if i+displacement < len(sessions) else None,
            "chikou_displayed_at": str(sessions[i-displacement]) if i >= displacement else None}
