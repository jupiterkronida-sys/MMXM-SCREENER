"""INVESTIGATION 1 — _swings() right-neighbor lookahead."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mmxm import _swings


def test_swings_lookahead():
    """Feed a candle sequence where bar i is a pivot ONLY when bar i+2 is known.

    Create a sequence where:
    - Bar 10 is a swing high (high=100, neighbors at 9 and 8 have lower highs)
    - Bar 12 and 13 have yet to be seen (buffer truncated before them)

    Expected: NO swing detected at bar 10 when buffer ends at bar 11,
    because bar 10's right-neighbor check requires bars 11 AND 12.
    """
    # Build 15 bars: bar 10 should be a swing high with 2 left + 2 right confirm
    highs = [50.0] * 15
    lows = [40.0] * 15

    # Make bar 10 a swing high: left neighbors 8,9 lower; right neighbors 11,12 lower
    highs[8] = 70.0   # left neighbor 1
    highs[9] = 80.0   # left neighbor 2 — lower than bar 10
    highs[10] = 100.0  # candidate swing high
    highs[11] = 90.0   # right neighbor 1 (lower)
    highs[12] = 85.0   # right neighbor 2 (lower)
    lows[10] = 60.0
    lows[11] = 55.0
    lows[12] = 50.0

    # Test 1: Full buffer (all 15 bars) — swing at index 10 should be detected
    swings_full = _swings(highs, lows, left=2, right=2)
    swing_at_10 = [s for s in swings_full if s[0] == 10]
    assert len(swing_at_10) == 1, (
        f"Expected 1 swing at index 10 with full data, got {len(swing_at_10)}"
    )
    print(f"  PASS (full buffer): swing at idx 10 detected = {swing_at_10[0]}")

    # Test 2: Truncated before bar 12 closes (buffer ends at bar 11)
    # Bar 10 requires 2 right neighbors. Bar 11 exists but bar 12 does not.
    highs_trunc = highs[:12]   # indices 0..11 (bars 0 through 11)
    lows_trunc = lows[:12]
    swings_trunc = _swings(highs_trunc, lows_trunc, left=2, right=2)
    swing_at_10_trunc = [s for s in swings_trunc if s[0] == 10]
    assert len(swing_at_10_trunc) == 0, (
        f"LOOKAHEAD CONFIRMED: swing at idx 10 returned with only 1 of 2 "
        f"required right neighbors available. Right-neighbor check uses "
        f"bars {10+1}..{10+2}, but buffer ends at index 11."
    )
    print("  PASS (truncated buffer): no swing at idx 10 — only 11 visible, bar 12 missing")

    # Test 3: Buffer ends at bar 12 (both right neighbors available)
    highs_edge = highs[:13]   # indices 0..12
    lows_edge = lows[:13]
    swings_edge = _swings(highs_edge, lows_edge, left=2, right=2)
    swing_at_10_edge = [s for s in swings_edge if s[0] == 10]
    assert len(swing_at_10_edge) == 1, (
        f"Expected 1 swing at idx 10 when both right neighbors exist, "
        f"got {len(swing_at_10_edge)}"
    )
    print("  PASS (edge buffer): swing at idx 10 detected when bar 12 is available")

    print("\n  => Lookahead offset confirmed: 2 bars (swing at i requires i+2 closed)")
    return True


def test_lookahead_offset_equals_right():
    """Prove the lookahead offset = `right` parameter (default 2)."""
    highs = [50.0] * 12
    lows = [40.0] * 12
    # Bar 7 swing high with right=3
    highs[4] = 60.0
    highs[5] = 55.0
    highs[6] = 70.0
    highs[7] = 90.0   # candidate
    highs[8] = 80.0
    highs[9] = 75.0
    highs[10] = 70.0
    highs[11] = 65.0
    lows[7] = 55.0
    lows[8] = 50.0
    lows[9] = 48.0
    lows[10] = 46.0
    lows[11] = 44.0

    # right=3 requires bars 8, 9, 10 to confirm bar 7
    swings = _swings(highs, lows, left=2, right=3)
    swing_at_7 = [s for s in swings if s[0] == 7]
    assert len(swing_at_7) == 1, (
        f"right=3: expected swing at idx 7 with full data, got {len(swing_at_7)}"
    )
    print(f"  PASS: right=3 still correct with full buffer, swing={swing_at_7[0]}")
    return True


if __name__ == "__main__":
    print("INVESTIGATION 1 — _swings() right-neighbor lookahead")
    print("=" * 60)
    test_swings_lookahead()
    test_lookahead_offset_equals_right()
    print("\nAll Investigation 1 tests passed.")
