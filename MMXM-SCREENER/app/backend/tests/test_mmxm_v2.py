"""Tests for mmxm_v2 — one per layer + integration test."""
import sys, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mmxm_v2 import (
    get_institutional_bias,
    get_htf_range_position,
    find_mitigation_block,
    validate_sweep,
    validate_mss,
    calculate_structural_sl,
    calculate_tp_levels,
    calculate_entry_zone,
    calculate_confidence,
    detect_mmxm_v2,
    MMXMSignalV2,
    _sweep_cleanliness,
    _mss_body_pct,
)
from services.indicators import atr_from_candles, funding_rate_regime, oi_slope


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_candle(open_, high, low, close, volume=1000.0):
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume, "timestamp": 0}


def _make_candle_list(prices, ranges=None):
    if ranges is None:
        ranges = [(p * 1.01, p * 0.99) for p in prices]
    candles = []
    for i, p in enumerate(prices):
        high = ranges[i][0] if i < len(ranges) else p * 1.01
        low = ranges[i][1] if i < len(ranges) else p * 0.99
        candles.append(_make_candle(p, high, low, p))
    return candles


# ── Indicator Tests ──────────────────────────────────────────────────────────

def test_atr_from_candles_basic():
    candles = [_make_candle(100, 102, 98, 101) for _ in range(15)]
    result = atr_from_candles(candles, 14)
    assert result is not None
    assert result > 0


def test_atr_from_candles_insufficient():
    candles = [_make_candle(100, 102, 98, 101) for _ in range(10)]
    result = atr_from_candles(candles, 14)
    assert result is None


def test_atr_known_values():
    candles = []
    prev_close = 100.0
    for i in range(16):
        high = prev_close + 2.0
        low = prev_close - 1.0
        close = prev_close + 1.0
        candles.append(_make_candle(prev_close, high, low, close))
        prev_close = close
    result = atr_from_candles(candles, 14)
    assert result is not None
    assert result > 0


def test_funding_rate_regime_negative():
    series = [-0.0002] * 14
    regime, strength = funding_rate_regime(series, 14)
    assert regime == "negative"
    assert strength > 0


def test_funding_rate_regime_positive():
    series = [0.0002] * 14
    regime, strength = funding_rate_regime(series, 14)
    assert regime == "positive"
    assert strength > 0


def test_funding_rate_regime_neutral():
    series = [0.0] * 14
    regime, strength = funding_rate_regime(series, 14)
    assert regime == "neutral"
    assert strength == 0.0


def test_funding_rate_regime_insufficient():
    regime, strength = funding_rate_regime([0.0] * 5, 14)
    assert regime == "neutral"
    assert strength == 0.0


def test_oi_slope_rising():
    series = [100 + i * 2 for i in range(14)]
    slope = oi_slope(series, 14)
    assert slope > 0


def test_oi_slope_falling():
    series = [100 - i * 2 for i in range(14)]
    slope = oi_slope(series, 14)
    assert slope < 0


def test_oi_slope_flat():
    series = [100.0] * 14
    slope = oi_slope(series, 14)
    assert abs(slope) < 1e-6


# ── Layer 1: Institutional Bias ──────────────────────────────────────────────

def test_l1_neutral_no_signal():
    """Neutral funding/neutral OI -> neutral bias."""
    funding = [0.0] * 14
    oi = [100.0] * 14
    bias, strength = get_institutional_bias(funding, oi)
    assert bias == "neutral"
    assert strength == 0.0


def test_l1_short_bias():
    """Negative funding + rising OI -> short bias."""
    funding = [-0.0002] * 14
    oi = [100 + i for i in range(14)]
    bias, strength = get_institutional_bias(funding, oi)
    assert bias == "short_bias"
    assert strength > 0


def test_l1_long_bias():
    """Positive funding + rising OI -> long bias."""
    funding = [0.0002] * 14
    oi = [100 + i for i in range(14)]
    bias, strength = get_institutional_bias(funding, oi)
    assert bias == "long_bias"
    assert strength > 0


# ── Layer 2: HTF Range Position ──────────────────────────────────────────────

def test_l2_discount():
    candles = _make_candle_list([100 + i for i in range(100)])
    pos, pct = get_htf_range_position(candles, 110.0)
    assert pos == "discount"
    assert pct >= 0


def test_l2_premium():
    candles = _make_candle_list([100 + i for i in range(100)])
    pos, pct = get_htf_range_position(candles, 195.0)
    assert pos == "premium"
    assert pct >= 0


def test_l2_equilibrium():
    candles = _make_candle_list([100] * 60, ranges=[(102, 98)] * 60)
    pos, pct = get_htf_range_position(candles, 100.0)
    assert pos == "equilibrium"
    assert pct == 0.0


def test_l2_insufficient_data():
    candles = _make_candle_list([100 + i for i in range(10)])
    pos, pct = get_htf_range_position(candles, 105.0)
    assert pos == "equilibrium"


# ── Layer 3: Mitigation Block ────────────────────────────────────────────────

def test_l3_no_prior_ob_returns_none():
    """No prior OB should return None."""
    candles = [_make_candle(100, 101, 99, 100) for _ in range(30)]
    result = find_mitigation_block(candles, "long", 1.5)
    assert result is None


# ── Layer 4: Sweep Confirmation ──────────────────────────────────────────────

def test_l4_sweep_wick_only():
    """Sweep candle closes back -> valid."""
    candles = [_make_candle(100, 101, 99, 100) for _ in range(10)]
    candles[-1] = _make_candle(100, 102, 98, 101)  # swept low 98 < 99, close 101 > 99
    assert validate_sweep(candles, 99.0, "long", max_sweep_age_bars=5) is True


def test_l4_sweep_not_closed_back():
    """Sweep candle closes outside -> invalid."""
    candles = [_make_candle(100, 101, 99, 100) for _ in range(10)]
    # Set previous candles to have higher lows so sweep_level isn't matched by normal candles
    for i in range(9):
        candles[i] = _make_candle(100, 101, 99.5, 100)
    candles[-1] = _make_candle(100, 102, 97, 98)  # low=97 < 99.0, close=98 < 99.0 -> fails
    assert validate_sweep(candles, 99.0, "long", max_sweep_age_bars=5) is False


# ── Layer 5: MSS Confirmation ────────────────────────────────────────────────

def test_l5_mss_body_closes_through():
    """MSS candle body closes through swing level -> valid."""
    candles = [_make_candle(100, 101, 99, 100) for _ in range(15)]
    candles[10] = _make_candle(100, 99, 98, 98.5)
    candles[11] = _make_candle(98, 100, 97, 99)
    # MSS candle: body (101.5, 102) fully ABOVE swing_level 101
    candles[12] = _make_candle(101.5, 103, 100.5, 102)
    result = validate_mss(candles, 10, 101.0, "long", max_mss_bars=10)
    assert result is not None


def test_l5_mss_wick_only():
    """MSS candle wick only through swing -> invalid."""
    candles = [_make_candle(100, 101, 99, 100) for _ in range(15)]
    candles[10] = _make_candle(100, 99, 98, 98.5)
    candles[11] = _make_candle(98, 100, 97, 99)
    candles[12] = _make_candle(99, 103, 100, 100.5)  # close 100.5 < swing 101, wick only
    result = validate_mss(candles, 10, 101.0, "long", max_mss_bars=10)
    assert result is None


# ── Layer 6: Structural SL ───────────────────────────────────────────────────

def test_l6_sl_outside_noise():
    """SL must be at least 1.5x ATR from entry."""
    candles = [_make_candle(100, 101, 99, 100) for _ in range(15)]
    sl, multiple = calculate_structural_sl(candles, "long", 100.0, 99.0, 1.0, atr_minimum_multiple=1.5)
    assert multiple >= 1.5
    assert sl < 100.0


def test_l6_sl_extended_when_too_close():
    """SL is extended to 1.5x ATR when raw SL is too tight."""
    sl, multiple = calculate_structural_sl([], "long", 100.0, 99.8, 1.0, atr_minimum_multiple=1.5)
    # raw_sl = 99.8 - 0.1*1.0 = 99.7, min_sl = 100 - 1.5 = 98.5
    # min(99.7, 98.5) = 98.5
    assert sl == 98.5
    assert multiple >= 1.5


# ── Layer 7: TP Levels ───────────────────────────────────────────────────────

def test_l7_fallback_tp_meets_min_rr():
    """Fallback TP must produce RR >= 2.0 when no liquidity pools exist."""
    candles = _make_candle_list([100] * 30)
    result = calculate_tp_levels(candles, "long", 100.0, 99.0, 1.0)
    assert result is not None
    _, tp2, _, rr = result
    assert rr >= 2.0
    assert tp2 > 100.0


# ── Layer 8: Entry Zone ──────────────────────────────────────────────────────

def test_l8_zone_width():
    """Entry zone width >= 0.5 x ATR for valid signal."""
    mb = {"low": 99.0, "high": 101.0, "formed_at_bar": 10, "mitigation_count": 1, "size": 2.0}
    zone_low, zone_high = calculate_entry_zone(mb, 1.0, "long")
    width = zone_high - zone_low
    assert width >= 0.5


# ── Layer 9: Confidence Scoring ──────────────────────────────────────────────

def test_l9_suppressed_when_too_few_criteria():
    """Signal with < 3 criteria -> confidence 1 -> suppressed."""
    conf = calculate_confidence(
        htf_bias_strength=0.3,
        range_position_pct=0.3,
        mb_mitigation_quality=0.2,
        sweep_cleanliness_score=0.3,
        mss_body_pct_score=0.3,
        rr_ratio=1.5,
        atr_sl_multiple=1.0,
    )
    assert conf < 2


def test_l9_full_criteria_5_star():
    """All 7 criteria met -> confidence 5."""
    conf = calculate_confidence(
        htf_bias_strength=0.80,
        range_position_pct=0.75,
        mb_mitigation_quality=0.65,
        sweep_cleanliness_score=0.75,
        mss_body_pct_score=0.65,
        rr_ratio=3.5,
        atr_sl_multiple=2.5,
    )
    assert conf == 5


# ── Integration Test ─────────────────────────────────────────────────────────

def _build_synthetic_setup():
    """Build a 200-bar synthetic series that satisfies all 9 layers.

    Design:
    - Bars 0-179: clean sine oscillation (100-110) producing unambiguous swings
    - Bars 180-184: flat zone at 106-107 (no swings)
    - Bar 185: swing LOW at 105.0 (last meaningful swing before sweep)
    - Bars 186-187: flat at 107
    - Bar 188: swing HIGH at 108.0 (MSS target)
    - Bars 189-194: flat at 106 (no swings, neighbors prevent 195 being a swing)
    - Bar 195: SWEEP (low=104.5 < 105.0, close=106.0 > 105.0)
    - Bar 196: RED ORDER BLOCK (open=107, close=105, low=105, high=108)
    - Bar 197: MSS (body 108-109 above swing high 108.0)
    - Bars 198-199: pullback near OB (current price ≈ 105.5, in OB range)
    """
    n = 200
    def mc(o, h, l, c):
        return _make_candle(o, h, l, c)

    candles = [mc(100, 101, 99, 100) for _ in range(n)]

    # Bars 0-179: sine oscillation (ATR ≈ 1.5)
    for i in range(180):
        phase = (i % 40) / 40.0
        angle = phase * 2 * 3.14159
        mid = 105.0
        amp = 5.0
        p = mid + amp * __import__('math').sin(angle)
        candles[i] = mc(p - 0.2, p + 0.8, p - 0.8, p + 0.3)

    # Bars 180-184: flat at 107 (no swings)
    for i in range(180, 185):
        candles[i] = mc(107.0, 107.5, 106.5, 107.0)

    # Bar 185: swing LOW at 105.0 (must be < neighbors at 183, 184, 186, 187)
    candles[185] = mc(105.5, 106.0, 105.0, 105.8)

    # Bars 186-187: flat at 107 (neighbors of swing low)
    candles[186] = mc(107.0, 107.5, 106.5, 107.2)
    candles[187] = mc(107.0, 107.5, 106.5, 107.0)

    # Bar 188: swing HIGH at 108.0 (must be > neighbors at 186, 187, 189, 190)
    candles[188] = mc(107.5, 108.0, 107.0, 107.8)

    # Bars 189-194: flat at 106 (no new swings)
    for i in range(189, 195):
        candles[i] = mc(106.0, 106.5, 105.5, 106.0)

    # Bar 195: SWEEP — low=104.5 < swing low 105.0, close=106.0 > 105.0
    # Candle 196 has same low (104.5) so 195 is NOT an isolated swing low
    candles[195] = mc(105.5, 106.5, 104.5, 106.0)

    # Bar 196: RED ORDER BLOCK (close < open, low=104.5, high=107.5)
    candles[196] = mc(107.0, 107.5, 104.5, 105.0)

    # Bar 197: MSS — body (108.2, 109.5) fully above swing high 108.0
    # Body/range = 1.3/1.6 = 0.81 >= 0.60 (needed for confidence #5)
    candles[197] = mc(108.2, 109.8, 108.2, 109.5)

    # Bars 198-199: pullback near OB (current price in OB range)
    candles[198] = mc(106.0, 106.5, 104.8, 105.5)
    candles[199] = mc(105.5, 106.0, 104.5, 105.0)

    # Daily candles: very wide range so current price (105) is deep in discount
    daily = []
    for p in range(80, 400):
        daily.append(mc(float(p), float(p) + 8.0, float(p) - 5.0, float(p)))

    funding = [0.0002] * 14
    oi = [100 + i for i in range(14)]

    return candles, daily, funding, oi


def test_integration_all_layers():
    """Feed a synthetic series satisfying all 9 layers -> valid signal."""
    candles, daily, funding, oi = _build_synthetic_setup()
    result = detect_mmxm_v2(
        candles=candles,
        daily_candles=daily,
        funding_series=funding,
        oi_series=oi,
        symbol="TESTUSDT",
        timeframe="1h",
        min_candles=60,
    )
    assert result is not None
    assert isinstance(result, MMXMSignalV2)
    assert result.confidence >= 2
    assert result.sl_atr_multiple >= 1.5
    assert result.risk_reward_tp2 >= 2.0


def test_integration_neutral_funding_no_signal():
    """Neutral funding -> layer 1 fails -> no signal."""
    candles, daily, _, oi = _build_synthetic_setup()
    neutral_funding = [0.0] * 14
    result = detect_mmxm_v2(
        candles=candles,
        daily_candles=daily,
        funding_series=neutral_funding,
        oi_series=[100.0] * 14,
        symbol="TESTUSDT",
        timeframe="1h",
        min_candles=60,
    )
    assert result is None


def test_integration_no_mitigation_block_no_signal():
    """No OB -> layer 3 fails -> no signal."""
    candles, daily, funding, oi = _build_synthetic_setup()
    result = detect_mmxm_v2(
        candles=candles[:100],
        daily_candles=daily,
        funding_series=funding,
        oi_series=oi,
        symbol="TESTUSDT",
        timeframe="1h",
        min_candles=60,
    )
    assert result is None
