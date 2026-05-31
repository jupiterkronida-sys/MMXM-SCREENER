"""Pump / dump heuristic screener — separate from MMXM."""
from typing import List, Dict, Optional
from .indicators import rsi, volume_zscore, ema


def detect_pump_dump(candles: List[List[float]], symbol: str) -> Optional[Dict]:
    """Look at last closed 1h candle vs lookback. candles 1h timeframe expected."""
    if len(candles) < 30:
        return None
    closes = [c[4] for c in candles]
    volumes = [c[5] for c in candles]

    last_close = closes[-1]
    prev_close = closes[-2]
    pct_change_1h = (last_close - prev_close) / prev_close * 100 if prev_close else 0

    vz = volume_zscore(volumes, 20)
    if vz is None:
        return None
    rsi_vals = rsi(closes, 14)
    if rsi_vals is None:
        return None
    rsi_val = rsi_vals[-1]
    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1] if len(closes) >= 50 else ema20

    kind = None
    if pct_change_1h >= 2 and vz >= 1.8 and rsi_val < 80 and last_close > ema20:
        kind = "pump"
    elif pct_change_1h <= -2 and vz >= 1.8 and rsi_val > 20 and last_close < ema20:
        kind = "dump"
    if not kind:
        return None

    # confidence 1..5 based on confluences
    score = 1
    if abs(pct_change_1h) >= 5:
        score += 1
    if vz >= 4:
        score += 1
    if (kind == "pump" and ema20 > ema50) or (kind == "dump" and ema20 < ema50):
        score += 1
    if (kind == "pump" and rsi_val < 65) or (kind == "dump" and rsi_val > 35):
        score += 1
    score = min(score, 5)

    return {
        "symbol": symbol,
        "kind": kind,
        "side": "long" if kind == "pump" else "short",
        "pct_change_1h": round(pct_change_1h, 2),
        "volume_zscore": round(vz, 2),
        "rsi": round(rsi_val, 1),
        "current_price": round(last_close, 8),
        "confidence": score,
    }


def update_candle_buffer(buffer: List[List[float]], candle: List[float], maxlen: int = 300) -> List[List[float]]:
    """Stream-native helper: append/replace latest candle and keep a bounded ring-like list."""
    if not candle:
        return buffer
    if buffer and buffer[-1][0] == candle[0]:
        buffer[-1] = candle
    else:
        buffer.append(candle)
    if len(buffer) > maxlen:
        del buffer[: len(buffer) - maxlen]
    return buffer
