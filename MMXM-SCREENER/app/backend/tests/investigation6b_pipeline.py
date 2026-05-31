"""INVESTIGATION 6b — Focused detect_mmxm() pipeline test with known-clean data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mmxm import detect_mmxm, _swings, _known_swings


def candle(idx, o, h, l, c, v=1000):
    return [idx * 60_000, o, h, l, c, v]


def build_bull_setup(length=70):
    """Build clean data guaranteed to produce a bull MMXM setup."""
    cs = []
    # 0..9: downtrend LOW swing at idx=10
    for i in range(10):
        p = 100.0 - i * 0.5
        cs.append(candle(i, p + 0.2, p + 0.5, p - 0.3, p))
    
    # 10: LOW swing (needs 2 lower neighbors on each side)
    # lows[8]=98.5, lows[9]=98.0, lows[10]=97.5, lows[11]=103.0, lows[12]=103.5
    cs.append(candle(10, 97.7, 98.0, 97.5, 97.8))
    
    # 11, 12: higher lows after the swing
    for i in range(11, 13):
        p = 103.0 + (i - 11) * 0.5
        cs.append(candle(i, p - 0.2, p + 0.5, p - 0.3, p))
    
    # 13: small pullback
    cs.append(candle(13, 103.0, 103.5, 102.5, 102.8))
    
    # 14: HIGH swing (needs 2 higher neighbors on each side)
    # highs[12]=104.0, highs[13]=103.5, highs[14]=105.0, highs[15]=104.5, highs[16]=104.0
    cs.append(candle(14, 103.0, 105.0, 102.8, 104.5))
    
    # 15,16: lower highs after peak
    cs.append(candle(15, 104.0, 104.5, 103.0, 103.5))
    cs.append(candle(16, 103.0, 104.0, 102.5, 103.0))
    
    # 17,18,19: more range / downtrend
    for i in range(17, 20):
        p = 103.0 - (i - 17) * 0.7
        cs.append(candle(i, p + 0.2, p + 0.5, p - 0.3, p))
    
    # 20..24: base building before sweep
    for i in range(20, 25):
        p = 98.0 + (i - 20) * 0.2
        cs.append(candle(i, p + 0.2, p + 0.5, p - 0.3, p))
    
    # 25: SWEEP — wick below swing low at 97.5, close back above
    cs.append(candle(25, 99.0, 99.5, 96.0, 99.2, v=5000))
    
    # 26,27: pullback
    for i in range(26, 28):
        p = 103.0 + (i - 26) * 0.3
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.3, p))
    
    # 28: MSS — close above the high swing level (105.0)
    cs.append(candle(28, 104.0, 107.0, 103.5, 106.5, v=8000))

    # 29..end: continuation above
    for i in range(29, length):
        p = 106.0 + (i - 29) * 0.1
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.3, p))
    
    return cs


def build_bear_setup(length=70):
    """Build data for a bearish MMXM setup."""
    cs = []
    # 0..9: uptrend, HIGH swing at idx=10
    for i in range(10):
        p = 100.0 + i * 0.5
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.5, p))
    
    # 10: HIGH swing
    cs.append(candle(10, 104.8, 105.5, 104.5, 105.0))
    
    # 11,12: lower highs
    for i in range(11, 13):
        p = 103.0 - (i - 11) * 0.5
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.5, p))
    
    # 13: bounce
    cs.append(candle(13, 102.0, 102.5, 101.0, 101.5))
    
    # 14: LOW swing (below neighbors)
    cs.append(candle(14, 102.0, 102.3, 100.0, 101.0))
    
    # 15,16: higher lows
    cs.append(candle(15, 101.5, 102.0, 101.0, 101.8))
    cs.append(candle(16, 102.0, 102.5, 101.5, 102.2))
    
    # 17..19: modest uptrend
    for i in range(17, 20):
        p = 102.0 + (i - 17) * 0.3
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.5, p))
    
    # 20..24: base
    for i in range(20, 25):
        p = 103.0 + (i - 20) * 0.1
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.3, p))
    
    # 25: SWEEP — wick above swing high at 105.5, close back below
    cs.append(candle(25, 104.0, 107.5, 103.5, 104.0, v=5000))
    
    # 26,27: pullback
    for i in range(26, 28):
        p = 104.0 + (i - 26) * 0.2
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.3, p))
    
    # 28: MSS — close below the low swing level (100.0)
    cs.append(candle(28, 103.0, 103.5, 98.0, 99.0, v=8000))
    
    # 29..end: continuation below
    for i in range(29, length):
        p = 99.0 - (i - 29) * 0.1
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.5, p))
    
    return cs


def debug_pattern(cs, label):
    """Check what swings exist and why no setup is found."""
    highs = [c[2] for c in cs]
    lows = [c[3] for c in cs]
    closes = [c[4] for c in cs]
    swings = _swings(highs, lows, 2, 2)
    last_idx = len(cs) - 1
    
    print(f"\n  DEBUG [{label}]: {len(cs)} candles, {len(swings)} swings")
    for s in swings:
        print(f"    swing idx={s[0]}, price={s[1]}, kind={s[2]}, confirmed={s[3]}")
    
    # Check what swings are known at the last few indices
    for mss_idx in range(last_idx, max(last_idx - 5, 0) - 1, -1):
        known = _known_swings(swings, mss_idx)
        high_known = [s for s in known if s[2] == "H"]
        low_known = [s for s in known if s[2] == "L"]
        print(f"    mss_idx={mss_idx}: {len(known)} known swings ({len(high_known)}H, {len(low_known)}L)")
        if high_known and low_known:
            print(f"      H={[s[1] for s in high_known[-3:]]} L={[s[1] for s in low_known[-3:]]}")


def test_bull_setup():
    cs = build_bull_setup()
    # debug_pattern(cs, "BULL")
    result = detect_mmxm(cs, "BULLUSDT", "1h")
    if result is not None:
        print(f"  PASS: bull setup: side={result['side']}, entry={result['entry']}, "
              f"sl={result['stop_loss']}, tp1={result['take_profit_1']}")
        assert result["side"] == "long"
    else:
        # Debug to understand why
        debug_pattern(cs, "BULL")
        print("  FAIL: expected bull setup but got None")


def test_bear_setup():
    cs = build_bear_setup()
    # debug_pattern(cs, "BEAR")
    result = detect_mmxm(cs, "BEARUSDT", "1h")
    if result is not None:
        print(f"  PASS: bear setup: side={result['side']}, entry={result['entry']}, "
              f"sl={result['stop_loss']}, tp1={result['take_profit_1']}")
        assert result["side"] == "short"
    else:
        debug_pattern(cs, "BEAR")
        print("  FAIL: expected bear setup but got None")


if __name__ == "__main__":
    print("INVESTIGATION 6b — detect_mmxm() Pipeline Tests")
    print("=" * 60)
    
    print("\n--- bull_setup ---")
    test_bull_setup()
    
    print("\n--- bear_setup ---")
    test_bear_setup()
