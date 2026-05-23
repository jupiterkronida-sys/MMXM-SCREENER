"""MMXM (Market Maker eXternal/internal range Models) detector - ICT-flavored.

Detects:
- swing highs/lows (fractal: 2 left + 2 right)
- liquidity sweep (latest candle wicks past last swing low/high then closes back inside)
- market structure shift (MSS / CHoCH) on the leg AFTER the sweep
- order block (last opposing candle before the impulsive MSS leg)
- fair value gap (3-candle imbalance)
- builds a trade plan: entry, SL, TP1/2/3, R:R

Returns a dict per symbol/timeframe or None.
"""
import time
from typing import Dict, List, Optional

from .indicators import atr

INTERVAL_MS = {"15m": 15 * 60_000, "1h": 60 * 60_000, "4h": 4 * 60 * 60_000, "1d": 24 * 60 * 60_000}
MAX_SWEEP_MSS_CANDLES = 5
MAX_MSS_AGE_CANDLES = 2
MAX_MSS_ALERT_DELAY_MS = 15 * 60_000


def _closed_candles(candles: List[List[float]], timeframe: str) -> List[List[float]]:
    """Return candles oldest->newest, excluding the currently building candle if present."""
    if not candles:
        return []
    out = sorted(candles, key=lambda c: c[0])
    interval_ms = INTERVAL_MS.get(timeframe)
    if interval_ms and out[-1][0] > 0 and int(time.time() * 1000) < out[-1][0] + interval_ms:
        return out[:-1]
    return out


def _swings(highs: List[float], lows: List[float], left: int = 2, right: int = 2):
    """Return list of (idx, price, kind) where kind in {'H','L'}."""
    swings = []
    for i in range(left, len(highs) - right):
        if all(highs[i] > highs[i - j] for j in range(1, left + 1)) and all(
            highs[i] > highs[i + j] for j in range(1, right + 1)
        ):
            swings.append((i, highs[i], "H"))
        if all(lows[i] < lows[i - j] for j in range(1, left + 1)) and all(
            lows[i] < lows[i + j] for j in range(1, right + 1)
        ):
            swings.append((i, lows[i], "L"))
    swings.sort(key=lambda s: s[0])
    return swings


def _find_fvg(candles: List[List[float]], direction: str, end_idx: int, min_gap: float):
    """Look for 3-candle FVG ending near end_idx. Returns (low, high) of the gap or None.
    direction='bull' -> candle[i-2].high < candle[i].low (gap up).
    direction='bear' -> candle[i-2].low > candle[i].high (gap down)."""
    start = max(2, end_idx - 8)
    for i in range(end_idx, start - 1, -1):
        c2 = candles[i - 2]
        c0 = candles[i]
        if direction == "bull" and c0[3] - c2[2] >= min_gap:
            return (c2[2], c0[3])
        if direction == "bear" and c2[3] - c0[2] >= min_gap:
            return (c0[2], c2[3])
    return None


def _find_order_block(candles: List[List[float]], direction: str, mss_idx: int, min_body: float):
    """The last opposing-color candle before the impulse that caused MSS.
    direction='bull' -> last DOWN candle before mss_idx.
    direction='bear' -> last UP candle before mss_idx."""
    for i in range(mss_idx - 1, max(mss_idx - 10, 0) - 1, -1):
        o, h, l, c = candles[i][1], candles[i][2], candles[i][3], candles[i][4]
        body = abs(c - o)
        candle_range = h - l
        if candle_range <= 0 or body < min_body or body / candle_range < 0.25:
            continue
        if direction == "bull" and c < o:
            return {"low": l, "high": h, "idx": i}
        if direction == "bear" and c > o:
            return {"low": l, "high": h, "idx": i}
    return None


def _ordered_targets(direction: str, entry: float, risk: float, external: List[float]):
    min_step = max(risk * 0.5, abs(entry) * 1e-8, 1e-12)
    targets = []
    levels = sorted(external, reverse=direction == "bear")

    for price in levels:
        anchor = targets[-1] if targets else entry
        if direction == "bull" and price >= anchor + min_step:
            targets.append(price)
        if direction == "bear" and price <= anchor - min_step:
            targets.append(price)
        if len(targets) == 3:
            return targets

    for multiplier in (1.5, 2.5, 4.0):
        fallback = entry + risk * multiplier if direction == "bull" else entry - risk * multiplier
        anchor = targets[-1] if targets else entry
        if direction == "bull" and fallback >= anchor + min_step:
            targets.append(fallback)
        if direction == "bear" and fallback <= anchor - min_step:
            targets.append(fallback)
        if len(targets) == 3:
            return targets

    while len(targets) < 3:
        anchor = targets[-1] if targets else entry
        targets.append(anchor + risk if direction == "bull" else anchor - risk)
    return targets


def _known_swings(swings, at_idx: int):
    """Swings confirmed by the time candle at_idx has closed."""
    return [s for s in swings if s[0] <= at_idx - 2]


def _find_recent_setup(candles: List[List[float]], swings, closes: List[float]):
    """Find the freshest sweep + MSS setup, searching newest candles first."""
    last_idx = len(candles) - 1
    earliest_mss_idx = max(0, last_idx - MAX_MSS_AGE_CANDLES + 1)
    candidates = []

    for mss_idx in range(last_idx, earliest_mss_idx - 1, -1):
        setup_swings = _known_swings(swings, mss_idx)
        high_swings = [s for s in setup_swings if s[2] == "H"]
        low_swings = [s for s in setup_swings if s[2] == "L"]
        if not high_swings or not low_swings:
            continue

        sweep_start = max(0, mss_idx - MAX_SWEEP_MSS_CANDLES)
        for sweep_idx in range(mss_idx, sweep_start - 1, -1):
            sweep_swings = _known_swings(swings, sweep_idx)
            sweep_highs = [s for s in sweep_swings if s[2] == "H"]
            sweep_lows = [s for s in sweep_swings if s[2] == "L"]
            sweep = candles[sweep_idx]

            if sweep_lows:
                swept_low = sweep_lows[-1]
                higher_after = [s for s in high_swings if swept_low[0] < s[0] < mss_idx]
                if higher_after:
                    mss_level = higher_after[-1][1]
                    prev_close = closes[mss_idx - 1] if mss_idx > 0 else closes[mss_idx]
                    swept = sweep[3] < swept_low[1] and sweep[4] > swept_low[1]
                    crossed = prev_close <= mss_level < closes[mss_idx]
                    if swept and crossed:
                        break_strength = abs(closes[mss_idx] - mss_level) / max(abs(mss_level), 1e-12)
                        candidates.append({
                            "bias": "bull",
                            "mss_idx": mss_idx,
                            "sweep_idx": sweep_idx,
                            "swept_swing": swept_low,
                            "mss_level": mss_level,
                            "break_strength": break_strength,
                        })

            if sweep_highs:
                swept_high = sweep_highs[-1]
                lower_after = [s for s in low_swings if swept_high[0] < s[0] < mss_idx]
                if lower_after:
                    mss_level = lower_after[-1][1]
                    prev_close = closes[mss_idx - 1] if mss_idx > 0 else closes[mss_idx]
                    swept = sweep[2] > swept_high[1] and sweep[4] < swept_high[1]
                    crossed = prev_close >= mss_level > closes[mss_idx]
                    if swept and crossed:
                        break_strength = abs(closes[mss_idx] - mss_level) / max(abs(mss_level), 1e-12)
                        candidates.append({
                            "bias": "bear",
                            "mss_idx": mss_idx,
                            "sweep_idx": sweep_idx,
                            "swept_swing": swept_high,
                            "mss_level": mss_level,
                            "break_strength": break_strength,
                        })

    if not candidates:
        return None
    candidates.sort(
        key=lambda c: (c["mss_idx"], c["sweep_idx"], c["break_strength"]),
        reverse=True,
    )
    return candidates[0]


def detect_mmxm(candles: List[List[float]], symbol: str, timeframe: str) -> Optional[Dict]:
    """candles: oldest->newest [t, o, h, l, c, v]."""
    candles = _closed_candles(candles, timeframe)
    if len(candles) < 60:
        return None

    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    current_price = closes[-1]
    atr_value = atr(highs, lows, closes, 14)
    swings = _swings(highs, lows, 2, 2)
    if len(swings) < 4:
        return None

    n = len(candles)
    last_idx = n - 1
    setup = _find_recent_setup(candles, swings, closes)
    if not setup:
        return None

    bias = setup["bias"]
    sweep_idx = setup["sweep_idx"]
    mss_idx = setup["mss_idx"]
    swept_swing = setup["swept_swing"]
    interval_ms = INTERVAL_MS.get(timeframe)
    if interval_ms:
        mss_close_time = int(candles[mss_idx][0]) + interval_ms
        if int(time.time() * 1000) - mss_close_time > MAX_MSS_ALERT_DELAY_MS:
            return None

    setup_swings = _known_swings(swings, mss_idx)
    recent_high_swings = [s for s in setup_swings if s[2] == "H"]
    recent_low_swings = [s for s in setup_swings if s[2] == "L"]
    if not recent_high_swings or not recent_low_swings:
        return None

    min_imbalance = max(current_price * 0.0001, atr_value * 0.02, 1e-12)
    min_ob_body = max(current_price * 0.0001, atr_value * 0.05, 1e-12)
    ob = _find_order_block(candles, bias, mss_idx, min_ob_body)
    fvg = _find_fvg(candles, bias, mss_idx, min_imbalance)
    if not ob and not fvg:
        return None

    if ob and fvg:
        zone_low = min(ob["low"], fvg[0])
        zone_high = max(ob["high"], fvg[1])
    elif ob:
        zone_low, zone_high = ob["low"], ob["high"]
    else:
        zone_low, zone_high = fvg

    actual_entry = zone_high if bias == "bull" else zone_low
    entry = actual_entry
    min_risk = max(current_price * 0.0002, atr_value * 0.05, 1e-12)
    if bias == "bull":
        sl = swept_swing[1] - atr_value * 0.5  # just under swept low
        if sl >= actual_entry:
            return None
        risk = actual_entry - sl
        if risk <= min_risk:
            return None
        external = sorted({s[1] for s in recent_high_swings if s[1] > actual_entry})
        tp1, tp2, tp3 = _ordered_targets(bias, actual_entry, risk, external)
        if current_price <= sl or current_price >= tp1:
            return None
    else:
        sl = swept_swing[1] + atr_value * 0.5  # just above swept high
        if sl <= actual_entry:
            return None
        risk = sl - actual_entry
        if risk <= min_risk:
            return None
        external = sorted({s[1] for s in recent_low_swings if s[1] < actual_entry}, reverse=True)
        tp1, tp2, tp3 = _ordered_targets(bias, actual_entry, risk, external)
        if current_price >= sl or current_price <= tp1:
            return None

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bias": bias,
        "side": "long" if bias == "bull" else "short",
        "entry_zone_low": round(zone_low, 8),
        "entry_zone_high": round(zone_high, 8),
        "entry": round(entry, 8),
        "stop_loss": round(sl, 8),
        "take_profit_1": round(tp1, 8),
        "take_profit_2": round(tp2, 8),
        "take_profit_3": round(tp3, 8),
        "risk_reward_tp2": round(abs(tp2 - actual_entry) / risk, 2),
        "swept_level": round(swept_swing[1], 8),
        "current_price": round(current_price, 8),
        "ob_used": ob is not None,
        "fvg_used": fvg is not None,
        "sweep_time": int(candles[sweep_idx][0]),
        "mss_time": int(candles[mss_idx][0]),
        "setup_age_candles": last_idx - mss_idx,
    }


def detect_mmxm_incremental(buffer: List[List[float]], symbol: str, timeframe: str) -> Optional[Dict]:
    """Stream-native wrapper for incremental execution on in-memory candle buffers."""
    if not buffer:
        return None
    return detect_mmxm(buffer, symbol, timeframe)
