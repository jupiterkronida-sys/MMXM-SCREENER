"""Screener v2 — emits MMXM v2 signals using the 9-layer institutional detector.

Integrates with the existing scanner loop, replacing detect_mmxm() calls
with detect_mmxm_v2() when funding+OI data is available.
"""
import logging
from typing import Dict, List

from .mmxm_v2 import detect_mmxm_v2, MMXMSignalV2

logger = logging.getLogger(__name__)


def signal_quality_gate(signal: MMXMSignalV2) -> tuple:
    """Final pre-save validation. Returns (passes, rejection_reason)."""
    if signal.sl_atr_multiple < 1.5:
        return False, "SL inside noise band"
    if signal.risk_reward_tp2 < 2.0:
        return False, "Insufficient RR"
    if signal.confidence < 2:
        return False, "Low confidence"
    if signal.htf_bias == "neutral":
        return False, "No institutional bias"
    if signal.htf_range_position == "equilibrium":
        return False, "Price at midrange"
    return True, "pass"


def run_mmxm_v2_screener(
    symbol: str,
    candles_1h: List[Dict],
    candles_4h: List[Dict],
    daily_candles: List[Dict],
    funding_series: List[float],
    oi_series: List[float],
) -> List[MMXMSignalV2]:
    """Run v2 detector on both 1h and 4h timeframes.

    Deduplicate: if both timeframes generate a signal on the same symbol within 4h,
    keep the higher-confidence one.

    Returns list of valid signals (may be empty).
    """
    signals = []

    for tf_name, tf_candles in [("1h", candles_1h), ("4h", candles_4h)]:
        if not tf_candles or len(tf_candles) < 100:
            continue

        sig = detect_mmxm_v2(
            candles=tf_candles,
            daily_candles=daily_candles,
            funding_series=funding_series,
            oi_series=oi_series,
            symbol=symbol,
            timeframe=tf_name,
            min_candles=100,
        )
        if sig is None:
            continue

        passes, reason = signal_quality_gate(sig)
        if not passes:
            logger.info("Signal %s %s rejected by quality gate: %s", symbol, tf_name, reason)
            continue

        signals.append(sig)

    if len(signals) <= 1:
        return signals

    sigs = sorted(signals, key=lambda s: s.confidence, reverse=True)
    kept = [sigs[0]]
    for s in sigs[1:]:
        if abs(len(candles_1h) - len(candles_4h)) * 3_600_000 > 14_400_000:
            kept.append(s)

    return kept
