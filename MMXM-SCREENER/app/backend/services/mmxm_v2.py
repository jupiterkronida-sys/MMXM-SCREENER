"""MMXM v2 — Institutional Market Maker Model with 9-layer signal validation.

Builds on v1 sweep+MSS detection but adds:
  L1: HTF Institutional Bias (COT proxy via funding/OI)
  L2: HTF Range Position (discount/premium/equilibrium)
  L3: Mitigation Block Validation (mitigated order block)
  L4: Sweep Confirmation (wick sweep, not breakout)
  L5: MSS Confirmation (body close, not wick)
  L6: Structural SL Placement (minimum 1.5x ATR)
  L7: Dynamic TP Placement (next liquidity pool)
  L8: Dynamic Entry Zone (ATR-scaled width)
  L9: Rebuilt Confidence Scoring (factors with predictive validity)

Returns a dataclass or None. Every layer must pass — no partial signals.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .indicators import atr_from_candles, funding_rate_regime, oi_slope
from .mmxm import _closed_candles, _swings, _known_swings, INTERVAL_MS


# ── Layer outputs ────────────────────────────────────────────────────────────

@dataclass
class MMXMSignalV2:
    symbol: str
    timeframe: str
    side: str
    confidence: int
    entry: float
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: Optional[float]
    risk_reward_tp2: float
    swept_level: float
    mitigation_block: dict
    htf_bias: str
    htf_range_position: str
    atr: float
    sl_atr_multiple: float
    formed_at_bar: int
    source: str = "mmxm_v2"


# ── Layer 1: HTF Institutional Bias ─────────────────────────────────────────

def get_institutional_bias(
    funding_series: List[float],
    oi_series: List[float],
) -> Tuple[str, float]:
    """Crypto HTF bias proxy for COT alignment.

    persistent negative funding + rising OI = institutional short pressure (Sell Model).
    persistent positive funding + rising OI = institutional long pressure (Buy Model).
    Mixed/neutral = no institutional bias confirmed.

    Returns (bias, strength) where bias is 'long_bias' | 'short_bias' | 'neutral'.
    """
    regime, fr_strength = funding_rate_regime(funding_series, lookback=14)
    oi_trend = oi_slope(oi_series, lookback=14)

    if regime == "negative" and oi_trend > 0:
        return "short_bias", fr_strength
    elif regime == "positive" and oi_trend > 0:
        return "long_bias", fr_strength
    return "neutral", 0.0


# ── Layer 2: HTF Range Position ─────────────────────────────────────────────

def get_htf_range_position(
    daily_candles: List[Dict],
    current_price: float,
) -> Tuple[str, float]:
    """Determine position within the HTF dealing range.

    Returns (position, range_pct) where:
      position: 'discount' | 'premium' | 'equilibrium'
      range_pct: how deep in discount/premium [0-1], 0 at midpoint.
    """
    if len(daily_candles) < 20:
        return "equilibrium", 0.0

    highs = [c["high"] for c in daily_candles[-60:]]
    lows = [c["low"] for c in daily_candles[-60:]]

    range_high = max(highs)
    range_low = min(lows)
    range_size = range_high - range_low

    if range_size <= 0:
        return "equilibrium", 0.0

    pct_from_low = (current_price - range_low) / range_size
    discount_boundary = 0.45
    premium_boundary = 0.55

    if pct_from_low <= discount_boundary:
        depth = 1.0 - (pct_from_low / discount_boundary) if discount_boundary > 0 else 1.0
        return "discount", min(depth, 1.0)
    elif pct_from_low >= premium_boundary:
        depth = (pct_from_low - premium_boundary) / (1.0 - premium_boundary)
        return "premium", min(depth, 1.0)
    else:
        return "equilibrium", 0.0


# ── Layer 3: Mitigation Block Validation ────────────────────────────────────

def find_mitigation_block(
    candles: List[Dict],
    side: str,
    atr_value: float,
    min_move_multiple: float = 1.5,
) -> Optional[Dict]:
    """Find the most recent valid mitigation block for entry.

    Valid MB criteria:
    1. Identify order blocks: last opposing candle before a move >= min_move_multiple x ATR
    2. Filter to mitigated OBs: price has traded through the OB body since formation
    3. Filter to unmitigated return: price is re-entering the OB from the correct side
       for the first time since initial mitigation
    4. MB must be in discount (long) or premium (short) zone

    side: 'long' for buy model, 'short' for sell model.
    Returns MB dict {low, high, formed_at_bar, mitigation_count, size} or None.
    """
    if len(candles) < 20:
        return None

    direction = "bull" if side == "long" else "bear"
    impulse_dir = 1 if side == "long" else -1

    last_idx = len(candles) - 1
    min_move = atr_value * min_move_multiple

    for mss_idx in range(last_idx - 1, max(last_idx - 30, 10), -1):
        prev_close = candles[mss_idx - 1]["close"]
        curr_close = candles[mss_idx]["close"]
        move = (curr_close - prev_close) * impulse_dir

        if move < min_move:
            continue

        ob_found = None
        for i in range(mss_idx - 1, max(mss_idx - 10, 0), -1):
            ci = candles[i]
            body = abs(ci["close"] - ci["open"])
            candle_range = ci["high"] - ci["low"]
            if candle_range <= 0 or body < atr_value * 0.1 or body / candle_range < 0.25:
                continue
            if direction == "bull" and ci["close"] < ci["open"]:
                ob_found = {"low": ci["low"], "high": ci["high"], "idx": i}
                break
            if direction == "bear" and ci["close"] > ci["open"]:
                ob_found = {"low": ci["low"], "high": ci["high"], "idx": i}
                break

        if ob_found is None:
            continue

        mito_count = 0
        for j in range(ob_found["idx"] + 1, last_idx + 1):
            cj = candles[j]
            if side == "long" and cj["low"] <= ob_found["high"]:
                mito_count += 1
            elif side == "short" and cj["high"] >= ob_found["low"]:
                mito_count += 1

        if mito_count == 0:
            continue

        current_price = candles[-1]["close"]
        if side == "long" and current_price < ob_found["low"]:
            continue
        if side == "long" and current_price > ob_found["high"] + atr_value * 0.5:
            continue
        if side == "short" and current_price > ob_found["high"]:
            continue
        if side == "short" and current_price < ob_found["low"] - atr_value * 0.5:
            continue

        return {
            "low": ob_found["low"],
            "high": ob_found["high"],
            "formed_at_bar": ob_found["idx"],
            "mitigation_count": mito_count,
            "size": ob_found["high"] - ob_found["low"],
        }

    return None


# ── Layer 4: Sweep Confirmation ─────────────────────────────────────────────

def validate_sweep(
    candles: List[Dict],
    sweep_level: float,
    side: str,
    max_sweep_age_bars: int = 5,
    atr_value: Optional[float] = None,
) -> bool:
    """Confirm clean liquidity sweep occurred.

    1. A candle wick exceeded sweep_level
    2. That candle CLOSED back inside range
    3. Within max_sweep_age_bars of current bar
    4. If atr: penetration < 0.5 x ATR (clean grab, not breakout)
    """
    if len(candles) < max_sweep_age_bars:
        return False

    last_idx = len(candles) - 1
    for i in range(last_idx, last_idx - max_sweep_age_bars, -1):
        if i < 0:
            break
        ci = candles[i]
        if side == "long":
            if ci["low"] <= sweep_level < ci["close"]:
                if atr_value:
                    penetration = (sweep_level - ci["low"]) / atr_value
                    if penetration >= 0.5:
                        continue
                return True
        else:
            if ci["high"] >= sweep_level > ci["close"]:
                if atr_value:
                    penetration = (ci["high"] - sweep_level) / atr_value
                    if penetration >= 0.5:
                        continue
                return True

    return False


# ── Layer 5: MSS Confirmation ───────────────────────────────────────────────

def validate_mss(
    candles: List[Dict],
    sweep_bar_idx: int,
    swing_level: float,
    side: str,
    max_mss_bars: int = 10,
) -> Optional[Dict]:
    """Confirm Market Structure Shift after liquidity sweep.

    Returns MSS bar dict {idx, close_price} or None if invalid.

    Requirements:
    1. MSS bar index > sweep_bar_idx
    2. MSS candle BODY closes through swing_level (not wick)
    3. MSS occurs within max_mss_bars of sweep
    """
    if len(candles) - 1 <= sweep_bar_idx:
        return None

    last_idx = len(candles) - 1
    start = min(sweep_bar_idx + 1, last_idx)
    end = min(sweep_bar_idx + max_mss_bars, last_idx) + 1

    for i in range(start, end):
        ci = candles[i]
        if side == "long":
            prev_close = candles[i - 1]["close"]
            if prev_close <= swing_level < ci["close"] and ci["close"] > swing_level:
                body_low = min(ci["open"], ci["close"])
                if body_low > swing_level:
                    return {"idx": i, "close_price": ci["close"]}
        else:
            prev_close = candles[i - 1]["close"]
            if prev_close >= swing_level > ci["close"] and ci["close"] < swing_level:
                body_high = max(ci["open"], ci["close"])
                if body_high < swing_level:
                    return {"idx": i, "close_price": ci["close"]}

    return None


# ── Layer 6: Structural SL Placement (Fixes F1) ─────────────────────────────

def calculate_structural_sl(
    candles: List[Dict],
    side: str,
    entry_price: float,
    sweep_level: float,
    atr_value: float,
    atr_minimum_multiple: float = 1.5,
) -> Tuple[float, float]:
    """Structural SL placement outside noise band.

    Returns (sl_price, sl_atr_multiple).

    1. Start from sweep extreme + 0.1 x ATR buffer
    2. If < atr_minimum_multiple x ATR from entry: extend entry +/- (atr_min x ATR)
    3. SL must never be inside entry candle's body
    """
    if side == "long":
        raw_sl = sweep_level - atr_value * 0.1
        min_sl = entry_price - atr_value * atr_minimum_multiple
        sl_price = min(raw_sl, min_sl)
    else:
        raw_sl = sweep_level + atr_value * 0.1
        min_sl = entry_price + atr_value * atr_minimum_multiple
        sl_price = max(raw_sl, min_sl)

    sl_distance = abs(entry_price - sl_price)
    sl_atr_multiple = round(sl_distance / atr_value, 8) if atr_value > 0 else 0.0
    return sl_price, sl_atr_multiple


# ── Layer 7: Dynamic TP Placement (Fixes F3) ────────────────────────────────

def _find_liquidity_pools(
    candles: List[Dict],
    side: str,
    lookback: int = 60,
) -> List[float]:
    """Identify equal highs/lows (un-swept liquidity pools) in the candle series.

    Returns sorted list of price levels in the direction of trade.
    """
    pools = set()
    if len(candles) < lookback:
        lookback = len(candles)

    high_windows = {}
    low_windows = {}

    for i in range(len(candles) - lookback, len(candles)):
        if i < 2:
            continue
        h = candles[i]["high"]
        l = candles[i]["low"]

        key_h = round(h, 2)
        key_l = round(l, 2)

        if key_h not in high_windows:
            high_windows[key_h] = []
        high_windows[key_h].append(i)

        if key_l not in low_windows:
            low_windows[key_l] = []
        low_windows[key_l].append(i)

    for level, indices in high_windows.items():
        if len(indices) >= 2:
            pools.add(level)
    for level, indices in low_windows.items():
        if len(indices) >= 2:
            pools.add(level)

    last_candle = candles[-1]
    last_high = last_candle["high"]
    last_low = last_candle["low"]

    if side == "long":
        valid = sorted(p for p in pools if p > last_high)
    else:
        valid = sorted((p for p in pools if p < last_low), reverse=True)

    return valid


def calculate_tp_levels(
    candles: List[Dict],
    side: str,
    entry_price: float,
    sl_price: float,
    atr_value: float,
) -> Optional[Tuple[float, float, Optional[float], float]]:
    """Institutional TP placement.

    TP1 = 1:1 RR
    TP2 = next liquidity pool beyond 1:1, minimum 2.0 RR
    TP3 = next HTF pool beyond TP2 (optional)

    Returns (tp1, tp2, tp3, rr_to_tp2) or None if no valid TP2 at >= 2.0 RR.
    """
    risk = abs(entry_price - sl_price)
    if risk <= 0:
        return None
    rr_for_price = atr_value * 2.0

    if side == "long":
        tp1 = entry_price + risk
        pools = _find_liquidity_pools(candles, "long", lookback=60)

        tp2_candidate = None
        for p in pools:
            if p > tp1:
                rr_candidate = (p - entry_price) / risk if risk > 0 else 0.0
                if rr_candidate >= 2.0:
                    tp2_candidate = p
                    break

        if tp2_candidate is None:
            tp2_candidate = entry_price + rr_for_price * 2.0

        rr2 = (tp2_candidate - entry_price) / risk if risk > 0 else 0.0

        tp3_candidate = None
        for p in reversed(pools):
            if p > tp2_candidate + risk * 0.5:
                tp3_candidate = p
                break
    else:
        tp1 = entry_price - risk
        pools = _find_liquidity_pools(candles, "short", lookback=60)

        tp2_candidate = None
        for p in pools:
            if p < tp1:
                rr_candidate = (entry_price - p) / risk if risk > 0 else 0.0
                if rr_candidate >= 2.0:
                    tp2_candidate = p
                    break

        if tp2_candidate is None:
            tp2_candidate = entry_price - rr_for_price * 2.0

        rr2 = (entry_price - tp2_candidate) / risk if risk > 0 else 0.0

        tp3_candidate = None
        for p in reversed(pools):
            if p < tp2_candidate - risk * 0.5:
                tp3_candidate = p
                break

    if rr2 < 2.0:
        return None

    return tp1, tp2_candidate, tp3_candidate, rr2


# ── Layer 8: Dynamic Entry Zone (Fixes F4) ──────────────────────────────────

def calculate_entry_zone(
    mitigation_block: Dict,
    atr_value: float,
    side: str,
) -> Tuple[float, float]:
    """Entry zone = mitigation block body +/- 0.25 x ATR.

    For long:  zone_low = mb_low - (0.25 x atr), zone_high = mb_high + (0.10 x atr)
    For short: zone_high = mb_high + (0.25 x atr), zone_low = mb_low - (0.10 x atr)
    """
    if side == "long":
        zone_low = mitigation_block["low"] - 0.25 * atr_value
        zone_high = mitigation_block["high"] + 0.10 * atr_value
    else:
        zone_high = mitigation_block["high"] + 0.25 * atr_value
        zone_low = mitigation_block["low"] - 0.10 * atr_value

    return zone_low, zone_high


# ── Layer 9: Confidence Scoring (Fixes F2) ──────────────────────────────────

def _mss_body_pct(candles: List[Dict], mss_idx: int) -> float:
    """What % of MSS candle is body (not wick). Higher = cleaner."""
    if mss_idx < 0 or mss_idx >= len(candles):
        return 0.0
    ci = candles[mss_idx]
    body = abs(ci["close"] - ci["open"])
    total_range = ci["high"] - ci["low"]
    return body / total_range if total_range > 0 else 0.0


def _sweep_cleanliness(candles: List[Dict], sweep_level: float, side: str) -> float:
    """How clean is the sweep: 1-(penetration/atr ratio). Higher = cleaner."""
    for i in range(len(candles) - 1, max(len(candles) - 6, 0), -1):
        ci = candles[i]
        if side == "long" and ci["low"] <= sweep_level:
            penetration = (sweep_level - ci["low"])
            total_range = ci["high"] - ci["low"]
            return 1.0 - min(penetration / (total_range + 1e-12), 1.0) if total_range > 0 else 0.5
        if side == "short" and ci["high"] >= sweep_level:
            penetration = (ci["high"] - sweep_level)
            total_range = ci["high"] - ci["low"]
            return 1.0 - min(penetration / (total_range + 1e-12), 1.0) if total_range > 0 else 0.5
    return 0.5


def calculate_confidence(
    htf_bias_strength: float,
    range_position_pct: float,
    mb_mitigation_quality: float,
    sweep_cleanliness_score: float,
    mss_body_pct_score: float,
    rr_ratio: float,
    atr_sl_multiple: float,
) -> int:
    """Confidence score based on institutional alignment quality.

    Score 5: ALL 7 criteria met
    Score 4: 5 of 7
    Score 3: 4 of 7
    Score 2: 3 of 7
    Score 1: < 3 of 7 — signal suppressed (not emitted)
    """
    criteria = [
        htf_bias_strength >= 0.70,
        range_position_pct >= 0.70,
        mb_mitigation_quality >= 0.60,
        sweep_cleanliness_score >= 0.70,
        mss_body_pct_score >= 0.60,
        rr_ratio >= 3.0,
        atr_sl_multiple >= 2.0,
    ]
    met = sum(criteria)

    if met >= 7:
        return 5
    elif met >= 5:
        return 4
    elif met >= 4:
        return 3
    elif met >= 3:
        return 2
    return 1


# ── Main detector ───────────────────────────────────────────────────────────

def _candles_to_dicts(candles_list: List[List[float]]) -> List[Dict]:
    """Convert list-of-lists candles [t,o,h,l,c,v] to dict format."""
    return [
        {"timestamp": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]}
        for c in candles_list
    ]


def detect_mmxm_v2(
    candles: List[Dict],
    daily_candles: List[Dict],
    funding_series: List[float],
    oi_series: List[float],
    symbol: str,
    timeframe: str,
    min_candles: int = 100,
) -> Optional[MMXMSignalV2]:
    """Full 9-layer institutional MMXM detection.

    Args:
        candles: TF-level OHLCV dicts [{t,o,h,l,c,v}...], closed bars only.
        daily_candles: Daily OHLCV dicts for HTF range position.
        funding_series: Last 14+ 8h funding rate values.
        oi_series: Last 14+ OI values.
        symbol: Trading pair symbol.
        timeframe: '1h' or '4h'.

    Returns MMXMSignalV2 or None if ANY layer fails.
    """
    if len(candles) < min_candles:
        return None
    if len(daily_candles) < 20:
        return None

    last_idx = len(candles) - 1
    current_price = candles[-1]["close"]
    atr_value = atr_from_candles(candles, 14)
    if atr_value is None:
        return None

    # ── L1: Institutional Bias ──────────────────────────────────────────────
    htf_bias, htf_strength = get_institutional_bias(funding_series, oi_series)
    if htf_bias == "neutral":
        return None

    side = "long" if htf_bias == "long_bias" else "short"

    # ── L2: HTF Range Position ──────────────────────────────────────────────
    range_pos, range_pct = get_htf_range_position(daily_candles, current_price)
    if range_pos == "equilibrium":
        return None
    if side == "long" and range_pos != "discount":
        return None
    if side == "short" and range_pos != "premium":
        return None

    # ── Common: swing detection (from v1, kept unchanged) ───────────────────
    highs_list = [c["high"] for c in candles]
    lows_list = [c["low"] for c in candles]
    closes_list = [c["close"] for c in candles]
    swings = _swings(highs_list, lows_list, 2, 2)
    if len(swings) < 4:
        return None

    setup_swings = _known_swings(swings, last_idx)
    high_swings = [s for s in setup_swings if s[2] == "H"]
    low_swings = [s for s in setup_swings if s[2] == "L"]
    if not high_swings or not low_swings:
        return None

    # ── Sweep + MSS search (adapted from v1) ────────────────────────────────
    sweep_found = None
    mss_found = None
    swept_swing = None
    mss_idx = None
    sweep_idx = None

    start = max(0, last_idx - 10)
    for mss_idx_candidate in range(last_idx, start, -1):
        for sweep_candidate in range(mss_idx_candidate, max(0, mss_idx_candidate - 5), -1):
            sc = candles[sweep_candidate]
            if side == "long" and low_swings:
                candidate_swing = low_swings[-1]
                sl = candidate_swing[1]
                swept = sc["low"] < sl and sc["close"] > sl
                if not swept:
                    continue
                for hs in reversed(high_swings):
                    if candidate_swing[0] < hs[0] < mss_idx_candidate:
                        mss_level = hs[1]
                        prev_close = candles[mss_idx_candidate - 1]["close"]
                        crossed = prev_close <= mss_level < candles[mss_idx_candidate]["close"]
                        if crossed:
                            swept_swing = candidate_swing
                            mss_level_actual = mss_level
                            mss_idx = mss_idx_candidate
                            sweep_idx = sweep_candidate
                            sweep_found = swept
                            break
                if sweep_found:
                    break
            elif side == "short" and high_swings:
                candidate_swing = high_swings[-1]
                sh = candidate_swing[1]
                swept = sc["high"] > sh and sc["close"] < sh
                if not swept:
                    continue
                for ls in reversed(low_swings):
                    if candidate_swing[0] < ls[0] < mss_idx_candidate:
                        mss_level = ls[1]
                        prev_close = candles[mss_idx_candidate - 1]["close"]
                        crossed = prev_close >= mss_level > candles[mss_idx_candidate]["close"]
                        if crossed:
                            swept_swing = candidate_swing
                            mss_level_actual = mss_level
                            mss_idx = mss_idx_candidate
                            sweep_idx = sweep_candidate
                            sweep_found = swept
                            break
                if sweep_found:
                    break

        if sweep_found:
            break

    if not sweep_found or swept_swing is None or mss_idx is None or sweep_idx is None:
        return None

    # ── L4: Sweep Confirmation (v2 validation) ──────────────────────────────
    if not validate_sweep(candles, swept_swing[1], side, max_sweep_age_bars=5, atr_value=atr_value):
        return None

    # ── L5: MSS Confirmation (v2 validation) ────────────────────────────────
    mss_result = validate_mss(candles, sweep_idx, mss_level_actual, side, max_mss_bars=10)
    if mss_result is None:
        return None

    # ── L3: Mitigation Block ────────────────────────────────────────────────
    mb = find_mitigation_block(candles, side, atr_value, min_move_multiple=1.5)
    if mb is None:
        return None

    # ── L8: Entry Zone ──────────────────────────────────────────────────────
    zone_low, zone_high = calculate_entry_zone(mb, atr_value, side)
    entry_price = zone_high if side == "long" else zone_low

    # ── L6: Structural SL ───────────────────────────────────────────────────
    sl_price, sl_atr_multiple = calculate_structural_sl(
        candles, side, entry_price, swept_swing[1], atr_value, atr_minimum_multiple=1.5,
    )
    if sl_atr_multiple < 1.5:
        return None
    if side == "long" and sl_price >= entry_price:
        return None
    if side == "short" and sl_price <= entry_price:
        return None

    # ── L7: TP Levels ───────────────────────────────────────────────────────
    tp_result = calculate_tp_levels(candles, side, entry_price, sl_price, atr_value)
    if tp_result is None:
        return None
    tp1, tp2, tp3, rr_to_tp2 = tp_result

    # ── L9: Confidence Scoring ──────────────────────────────────────────────
    mb_quality = min(1.0, (mb.get("mitigation_count", 0) / 3.0))
    sweep_clean = _sweep_cleanliness(candles, swept_swing[1], side)
    mss_body = _mss_body_pct(candles, mss_idx)

    confidence = calculate_confidence(
        htf_bias_strength=htf_strength,
        range_position_pct=range_pct,
        mb_mitigation_quality=mb_quality,
        sweep_cleanliness_score=sweep_clean,
        mss_body_pct_score=mss_body,
        rr_ratio=rr_to_tp2,
        atr_sl_multiple=sl_atr_multiple,
    )
    if confidence < 2:
        return None

    return MMXMSignalV2(
        symbol=symbol,
        timeframe=timeframe,
        side=side,
        confidence=confidence,
        entry=round(entry_price, 8),
        entry_zone_low=round(zone_low, 8),
        entry_zone_high=round(zone_high, 8),
        stop_loss=round(sl_price, 8),
        take_profit_1=round(tp1, 8),
        take_profit_2=round(tp2, 8),
        take_profit_3=round(tp3, 8) if tp3 else None,
        risk_reward_tp2=round(rr_to_tp2, 2),
        swept_level=round(swept_swing[1], 8),
        mitigation_block={"low": mb["low"], "high": mb["high"], "formed_at_bar": mb["formed_at_bar"]},
        htf_bias=htf_bias,
        htf_range_position=range_pos,
        atr=round(atr_value, 8),
        sl_atr_multiple=round(sl_atr_multiple, 2),
        formed_at_bar=last_idx,
        source="mmxm_v2",
    )


def detect_mmxm_v2_from_lists(
    candles_list: List[List[float]],
    daily_candles_list: List[List[float]],
    funding_series: List[float],
    oi_series: List[float],
    symbol: str,
    timeframe: str,
    min_candles: int = 100,
) -> Optional[MMXMSignalV2]:
    """Convenience wrapper accepting list-of-lists candle format (backward compat)."""
    candles = _candles_to_dicts(candles_list)
    daily = _candles_to_dicts(daily_candles_list)
    candles = _closed_candles_list(candles, timeframe)
    daily = _closed_candles_list(daily, timeframe)
    return detect_mmxm_v2(candles, daily, funding_series, oi_series, symbol, timeframe, min_candles)


def _closed_candles_list(candles: List[Dict], timeframe: str) -> List[Dict]:
    """Return candles excluding the currently building candle.
    Accepts dict-format candles with 'timestamp' key.
    """
    if not candles:
        return []
    import time
    out = sorted(candles, key=lambda c: c["timestamp"])
    interval_ms = INTERVAL_MS.get(timeframe)
    if interval_ms and out[-1]["timestamp"] > 0 and int(time.time() * 1000) < out[-1]["timestamp"] + interval_ms:
        return out[:-1]
    return out
