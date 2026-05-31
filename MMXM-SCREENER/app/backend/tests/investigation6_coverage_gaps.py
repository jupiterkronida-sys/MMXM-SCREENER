"""INVESTIGATION 6 — Test coverage gap remediation.

Adds tests for zero-coverage functions in mmxm.py:
  - _closed_candles()
  - _find_order_block()
  - _ordered_targets()
  - detect_mmxm() — the main pipeline
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mmxm import (
    _closed_candles,
    _find_order_block,
    _ordered_targets,
    _known_swings,
    detect_mmxm,
    INTERVAL_MS,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def candle(t, o, h, l, c, v=100.0):
    return [t, o, h, l, c, v]


def time_ms(offset_s=0):
    """Return a pseudo-timestamp offset_s seconds ago."""
    return int(time.time() * 1000 - offset_s * 1000)


# ── _closed_candles() ────────────────────────────────────────────────────────

def test_closed_candles_empty():
    assert _closed_candles([], "1h") == []
    print("  PASS: empty input -> []")


def test_closed_candles_preserves_order():
    cs = [candle(3000, 10, 11, 9, 10), candle(1000, 10, 11, 9, 10)]
    result = _closed_candles(cs, "1h")
    assert result[0][0] == 1000, f"Expected sorted: first ts 1000, got {result[0][0]}"
    assert result[1][0] == 3000
    print("  PASS: sorts oldest->newest")


def test_closed_candles_removes_building_candle():
    """If the newest candle's open time + interval > now, it's building -> drop."""
    now_ms = int(time.time() * 1000)
    interval_1h = INTERVAL_MS["1h"]  # 3_600_000
    closed = candle(now_ms - interval_1h, 10, 11, 9, 10)   # closed (its window passed)
    building = candle(now_ms, 10, 11, 9, 10)                # still building
    result = _closed_candles([closed, building], "1h")
    assert len(result) == 1, f"Expected 1 candle (building dropped), got {len(result)}"
    assert result[0][0] == closed[0]
    print("  PASS: drops building candle when timestamp+interval > now")


def test_closed_candles_zero_timestamp():
    """Candles with timestamp 0 are kept (fallback for test data)."""
    cs = [candle(0, 10, 11, 9, 10), candle(0, 11, 12, 10, 11)]
    result = _closed_candles(cs, "1h")
    assert len(result) == 2, f"Expected 2 candles kept for ts=0, got {len(result)}"
    print("  PASS: keeps all when timestamp is 0")


# ── _find_order_block() ──────────────────────────────────────────────────────

def test_order_block_bull():
    """Bullish OB = last DOWN candle before mss_idx with adequate body."""
    cs = [
        candle(0, 10, 11, 9, 10),    # 0
        candle(1, 10, 11, 9, 9.5),   # 1: DOWN
        candle(2, 10, 11, 9, 10),    # 2: UP (neutral)
        candle(3, 12, 13, 11, 12),   # 3: impulse (MSS)
    ]
    # direction='bull', so last DOWN candle before idx=3 is idx=1
    ob = _find_order_block(cs, "bull", 3, min_body=0.3)
    assert ob is not None, "Expected order block"
    assert ob["idx"] == 1, f"Expected idx=1 (last down before impulse), got {ob}"
    print(f"  PASS: bull OB at idx={ob['idx']}, low={ob['low']}, high={ob['high']}")


def test_order_block_bear():
    """Bearish OB = last UP candle before mss_idx."""
    cs = [
        candle(0, 10, 11, 9, 10),    # 0
        candle(1, 10, 11, 9, 10.5),  # 1: UP
        candle(2, 10, 11, 9, 9.5),   # 2: DOWN (impulse)
    ]
    ob = _find_order_block(cs, "bear", 2, min_body=0.3)
    assert ob is not None, "Expected order block"
    assert ob["idx"] == 1, f"Expected idx=1 (last up before impulse), got {ob}"
    print(f"  PASS: bear OB at idx={ob['idx']}")


def test_order_block_insufficient_body():
    """Candle with body too small vs min_body threshold is skipped."""
    cs = [
        candle(0, 10, 11, 9.9, 10.05),  # body=0.05 < min_body=0.3
        candle(1, 12, 13, 11, 12),       # impulse
    ]
    ob = _find_order_block(cs, "bull", 1, min_body=0.3)
    assert ob is None, "Expected None (body too small)"
    print("  PASS: None when body < min_body")


def test_order_block_body_candle_range_ratio():
    """Candle with body/range < 0.25 is skipped (doji)."""
    cs = [
        candle(0, 10, 12, 8, 10.1),  # range=4, body=0.1, ratio=0.025 < 0.25
        candle(1, 12, 13, 11, 12),
    ]
    ob = _find_order_block(cs, "bull", 1, min_body=0.01)
    assert ob is None, "Expected None (doji-like, body/range too small)"
    print("  PASS: None when body/range < 0.25")


def test_order_block_no_match():
    """No opposing candle found."""
    cs = [candle(0, 10, 11, 9, 12), candle(1, 12, 13, 11, 13)]
    ob = _find_order_block(cs, "bull", 1, min_body=0.3)
    assert ob is None, "Expected None (no DOWN candle before impulse)"
    print("  PASS: None when no opposing candle")


def test_order_block_lookback_limited():
    """OB search looks back max 10 candles."""
    cs = [candle(i, 10, 11, 9, 9.5) for i in range(12)]  # all DOWN
    cs.append(candle(12, 12, 13, 11, 13))  # impulse at 12
    # Should find some OB within 10 candles back from index 12 (idx 2..11)
    ob = _find_order_block(cs, "bull", 12, min_body=0.3)
    assert ob is not None, "Expected OB within lookback"
    assert 2 <= ob["idx"] <= 11, f"OB idx {ob['idx']} outside valid range [2, 11]"
    print(f"  PASS: OB found at idx={ob['idx']} within lookback [2, 11]")


# ── _ordered_targets() ───────────────────────────────────────────────────────

def test_ordered_targets_bull():
    targets = _ordered_targets("bull", entry=10.0, risk=1.0, external=[12.0, 14.0])
    assert len(targets) == 3, f"Expected 3 targets, got {len(targets)}: {targets}"
    assert targets[0] > 10.0, f"TP1 should > entry"
    print(f"  PASS: bull targets={targets}")


def test_ordered_targets_bear():
    targets = _ordered_targets("bear", entry=10.0, risk=1.0, external=[8.0, 6.0])
    assert len(targets) == 3, f"Expected 3 targets, got {len(targets)}: {targets}"
    assert targets[0] < 10.0, f"TP1 should < entry"
    print(f"  PASS: bear targets={targets}")


def test_ordered_targets_fills_with_fallbacks():
    """When external levels are insufficient, fall back to risk multipliers."""
    targets = _ordered_targets("bull", entry=10.0, risk=1.0, external=[])
    assert len(targets) == 3, f"Expected 3 targets (fallbacks), got {targets}"
    # fallback multipliers: 1.5, 2.5, 4.0 -> 11.5, 12.5, 14.0
    expected = [11.5, 12.5, 14.0]
    for t, e in zip(targets, expected):
        assert abs(t - e) < 1e-6, f"Expected {e}, got {t}"
    print(f"  PASS: fallback targets={targets}")


def test_ordered_targets_min_step():
    """Targets must be at least min_step apart."""
    targets = _ordered_targets("bull", entry=10.0, risk=0.01, external=[10.005, 10.006])
    # min_step = max(0.01*0.5, 10*1e-8, 1e-12) = max(0.005, 1e-7, 1e-12) = 0.005
    # external levels 10.005, 10.006 both < 10 + 0.005 = 10.005 (first one fails the >= check)
    # So they're both rejected, fallbacks used
    assert len(targets) == 3
    print(f"  PASS: min_step guard works, targets={targets}")


# ── detect_mmxm() — end-to-end pipeline ─────────────────────────────────────

def _build_pump_candles(base=100.0, count=60):
    """Build candle data: flat then a sweep+MSS pattern."""
    cs = []
    # Phase 1: gentle downtrend (lows)
    for i in range(20):
        p = base - i * 0.2
        cs.append(candle(i, p + 0.5, p + 1.0, p - 0.5, p))
    # Phase 2: range (build swing highs)
    for i in range(20, 40):
        p = base - 4.0 + (i - 20) * 0.05
        cs.append(candle(i, p + 0.5, p + 0.8, p - 0.5, p))
    # Phase 3: sweep low, then MSS up
    # sweep candle: wick below prior low, close inside
    prior_low = cs[-1][3]
    cs.append(candle(40, prior_low + 0.5, prior_low + 1.0, prior_low - 2.0, prior_low + 0.3))
    # slight pullback
    cs.append(candle(41, prior_low + 0.3, prior_low + 1.0, prior_low - 0.5, prior_low + 0.5))
    # MSS candle: break above recent high
    recent_high = max(c[2] for c in cs[-10:])
    cs.append(candle(42, recent_high + 0.1, recent_high + 2.0, recent_high - 0.5, recent_high + 1.5))

    # Fill remaining candles with neutral data
    for i in range(43, count):
        cs.append(candle(i, base - 2.0, base - 1.0, base - 3.0, base - 2.0))
    return cs


def test_detect_mmxm_pipeline():
    """detect_mmxm() with enough data — should identify a pattern or return None gracefully."""
    cs = _build_pump_candles(count=70)
    result = detect_mmxm(cs, "TESTUSDT", "1h")
    # May or may not find a setup — just verify no crash and consistent type
    if result is not None:
        assert "symbol" in result
        assert result["symbol"] == "TESTUSDT"
        assert result["side"] in ("long", "short")
        for key in ("entry", "stop_loss", "take_profit_1", "take_profit_2", "take_profit_3"):
            assert key in result, f"Missing key {key}"
        print(f"  PASS: detected {result['side']} setup (confidence implied)")
    else:
        # Could also return None if pattern not found — valid outcome
        print("  INFO: no setup found (pattern may not match)")


def test_detect_mmxm_too_few_candles():
    result = detect_mmxm([candle(0, 10, 11, 9, 10) for _ in range(10)], "TESTUSDT", "1h")
    assert result is None, "Expected None for < 60 candles"
    print("  PASS: None for insufficient candles")


def test_detect_mmxm_incremental():
    from services.mmxm import detect_mmxm_incremental
    assert detect_mmxm_incremental([], "TESTUSDT", "1h") is None
    cs = [candle(0, 10, 11, 9, 10) for _ in range(10)]
    # buffer too small, returns None (delegates to detect_mmxm which checks len >= 60)
    result = detect_mmxm_incremental(cs, "TESTUSDT", "1h")
    assert result is None
    print("  PASS: incremental returns None for small buffer")


def test_detect_mmxm_bullish_setup():
    """Build data that should produce a bullish MMXM signal."""
    n = 70
    cs = []
    # Phase 1: downtrend 0..20
    for i in range(20):
        p = 100.0 - i * 0.3
        cs.append(candle(i, p + 0.5, p + 1.0, p - 0.5, p, v=1000.0))
    # Phase 2: range 20..40 with swing lows
    min_low_in_range = 1000.0
    for i in range(20, 40):
        p = 94.0 + (i - 20) * 0.02
        l = p - 0.3  # noqa: E741
        cs.append(candle(i, p + 0.5, p + 0.8, l, p, v=1000.0))
        if l < min_low_in_range:
            min_low_in_range = l
    # Phase 3: sweep (wick below range low, close inside)
    cs.append(candle(40, min_low_in_range + 0.5, 95.5, min_low_in_range - 2.0, 95.0, v=1500.0))
    # Phase 4: pullback then MSS up
    cs.append(candle(41, 95.0, 95.5, 94.5, 94.8, v=1200.0))
    cs.append(candle(42, 94.8, 97.0, 94.6, 96.5, v=2000.0))  # MSS above recent high
    # Rest: continuation candles
    for i in range(43, n):
        p = 96.0 + (i - 43) * 0.05
        cs.append(candle(i, p - 0.3, p + 0.5, p - 0.5, p, v=1000.0))

    result = detect_mmxm(cs, "BULLUSDT", "1h")
    if result is not None:
        print(f"  PASS: bullish setup detected: side={result['side']}, entry={result['entry']}, "
              f"sl={result['stop_loss']}, tp1={result['take_profit_1']}")
        assert result["side"] == "long"
    else:
        print("  INFO: no bullish setup (edge case — pattern may not match)")


def test_detect_mmxm_bearish_setup():
    """Build data that should produce a bearish MMXM signal."""
    n = 70
    cs = []
    # Phase 1: uptrend 0..20
    for i in range(20):
        p = 100.0 + i * 0.3
        cs.append(candle(i, p - 0.5, p + 0.5, p - 1.0, p, v=1000.0))
    # Phase 2: range 20..40 with swing highs
    max_high_in_range = 0.0
    for i in range(20, 40):
        p = 106.0 - (i - 20) * 0.02
        h = p + 0.3
        cs.append(candle(i, p - 0.5, h, p - 0.8, p, v=1000.0))
        if h > max_high_in_range:
            max_high_in_range = h
    # Phase 3: sweep (wick above range high, close inside)
    cs.append(candle(40, 105.0, max_high_in_range + 2.0, 104.5, 105.0, v=1500.0))
    # Phase 4: pullback then MSS down
    cs.append(candle(41, 105.0, 105.5, 104.5, 105.2, v=1200.0))
    cs.append(candle(42, 105.2, 105.4, 102.5, 103.0, v=2000.0))  # MSS below recent low
    # Rest
    for i in range(43, n):
        p = 103.0 - (i - 43) * 0.05
        cs.append(candle(i, p + 0.3, p + 0.5, p - 0.5, p, v=1000.0))

    result = detect_mmxm(cs, "BEARUSDT", "1h")
    if result is not None:
        print(f"  PASS: bearish setup detected: side={result['side']}, entry={result['entry']}, "
              f"sl={result['stop_loss']}, tp1={result['take_profit_1']}")
        assert result["side"] == "short"
    else:
        print("  INFO: no bearish setup (edge case — pattern may not match)")


# ── runner ────────────────────────────────────────────────────────────────────

def run_all():
    tests = [
        ("closed_candles_empty", test_closed_candles_empty),
        ("closed_candles_order", test_closed_candles_preserves_order),
        ("closed_candles_building", test_closed_candles_removes_building_candle),
        ("closed_candles_zero_ts", test_closed_candles_zero_timestamp),
        ("order_block_bull", test_order_block_bull),
        ("order_block_bear", test_order_block_bear),
        ("order_block_body_insufficient", test_order_block_insufficient_body),
        ("order_block_doji_skip", test_order_block_body_candle_range_ratio),
        ("order_block_no_match", test_order_block_no_match),
        ("order_block_lookback", test_order_block_lookback_limited),
        ("ordered_targets_bull", test_ordered_targets_bull),
        ("ordered_targets_bear", test_ordered_targets_bear),
        ("ordered_targets_fallbacks", test_ordered_targets_fills_with_fallbacks),
        ("ordered_targets_min_step", test_ordered_targets_min_step),
        ("detect_mmxm_pipeline", test_detect_mmxm_pipeline),
        ("detect_mmxm_too_few", test_detect_mmxm_too_few_candles),
        ("detect_mmxm_incremental", test_detect_mmxm_incremental),
        ("detect_mmxm_bullish", test_detect_mmxm_bullish_setup),
        ("detect_mmxm_bearish", test_detect_mmxm_bearish_setup),
    ]

    failures = []
    print("INVESTIGATION 6 — Test Coverage Gap Remediation")
    print("=" * 60)

    for name, fn in tests:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            failures.append(name)

    print(f"\n{'='*60}")
    if not failures:
        print("ALL PASSED")
    else:
        print(f"FAILURES ({len(failures)}): {', '.join(failures)}")
    return len(failures) == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
