"""Lightweight technical indicators (no pandas-ta dependency)."""
from typing import List
import math


def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: List[float], period: int = 14) -> List[float] | None:
    """Return RSI values or None if insufficient data."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out = [50.0] * (period + 1)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_val = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100 - 100 / (1 + rs)
        out.append(rsi_val)
    while len(out) < len(closes):
        out.append(out[-1])
    return out[: len(closes)]


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float | None:
    """Return ATR or None if insufficient data."""
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [a - b for a, b in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, signal)
    hist = [m - s for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, hist


def volume_zscore(volumes: List[float], lookback: int = 20) -> float | None:
    """How many std-devs the latest volume is above its lookback mean. None if insufficient data."""
    if len(volumes) < lookback + 1:
        return None
    sample = volumes[-lookback - 1 : -1]
    mean = sum(sample) / len(sample)
    var = sum((v - mean) ** 2 for v in sample) / len(sample)
    std = math.sqrt(var) if var > 0 else 1.0
    return (volumes[-1] - mean) / std


def atr_from_candles(candles: List[dict], period: int = 14) -> float | None:
    """ATR from dict-format candles with 'high','low','close' keys.
    Returns None if len(candles) < period + 1."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        tr = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"] - candles[i - 1]["close"]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def funding_rate_regime(funding_series: List[float], lookback: int = 14) -> tuple:
    """Classify funding rate regime from last N 8h funding rates.

    Returns (regime, strength) where:
      regime: 'negative' | 'positive' | 'neutral'
      strength: abs(median) / 0.0003 clamped to [0, 1]
    """
    if len(funding_series) < lookback:
        return "neutral", 0.0
    recent = funding_series[-lookback:]
    sorted_vals = sorted(recent)
    n = len(sorted_vals)
    median = sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    last3 = sum(recent[-3:]) / 3

    strength = min(abs(median) / 0.0003, 1.0)

    if median < -0.0001 and last3 < -0.0001:
        return "negative", strength
    elif median > 0.0001 and last3 > 0.0001:
        return "positive", strength
    return "neutral", strength


def oi_slope(oi_series: List[float], lookback: int = 14) -> float:
    """Linear regression slope of OI over lookback periods.
    Normalised by mean(OI) for scale-independence.
    Positive = rising OI. Negative = falling OI.
    """
    if len(oi_series) < lookback:
        return 0.0
    y = oi_series[-lookback:]
    x = list(range(lookback))
    n = lookback
    mean_x = (n - 1) / 2.0
    mean_y = sum(y) / n
    num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den = sum((x[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0.0
    mean_y_abs = abs(mean_y) if mean_y != 0 else 1.0
    return slope / mean_y_abs
