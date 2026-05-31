"""INVESTIGATION 6c — detect_mmxm() with MSS in last 2 bars."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mmxm import detect_mmxm, _swings, _known_swings, _find_recent_setup


def candle(idx, o, h, l, c, v=1000):
    return [idx * 60_000, o, h, l, c, v]


def build_bull(length=70):
    """MSS at idx=68 (last_idx-1)."""
    cs = []
    # 0..57: neutral / slight downtrend
    for i in range(50):
        p = 100.0 - i * 0.05
        cs.append(candle(i, p + 0.2, p + 0.4, p - 0.3, p))
    
    # 50: LOW swing — needs 2 lower on each side
    # lows[48] ~ 98.77, lows[49] ~ 98.72, lows[50]=98.50, lows[51]=98.80, lows[52]=98.85
    cs.append(candle(48, 99.0, 99.2, 98.75, 99.0))
    cs.append(candle(49, 98.8, 99.0, 98.7, 98.9))
    cs.append(candle(50, 98.6, 98.8, 98.5, 98.7))  # LOW swing
    cs.append(candle(51, 98.9, 99.1, 98.8, 99.0))
    cs.append(candle(52, 99.0, 99.2, 98.85, 99.1))
    
    # 53..55: rise to HIGH swing
    cs.append(candle(53, 99.5, 99.8, 99.3, 99.6))
    cs.append(candle(54, 99.8, 100.5, 99.6, 100.2))
    cs.append(candle(55, 100.5, 101.0, 100.3, 100.8))  # HIGH swing
    
    # 56,57: lower highs
    cs.append(candle(56, 100.5, 100.8, 100.2, 100.4))
    cs.append(candle(57, 100.0, 100.3, 99.8, 100.0))
    
    # 58..62: decline
    for i in range(58, 62):
        p = 99.5 - (i - 57) * 0.2
        cs.append(candle(i, p + 0.2, p + 0.4, p - 0.3, p))
    
    # 63,64: base
    cs.append(candle(62, 98.5, 98.7, 98.3, 98.5))
    cs.append(candle(63, 98.6, 98.8, 98.4, 98.6))
    
    # 64: SWEEP — wick below 98.5, close above
    cs.append(candle(64, 98.6, 98.9, 97.8, 98.8, v=5000))
    
    # 65: pullback
    cs.append(candle(65, 98.6, 98.8, 98.4, 98.6))
    
    # 66: small up
    cs.append(candle(66, 98.8, 100.0, 98.7, 99.8))
    
    # 67: MSS — close above H swing at 55 (101.0)
    cs.append(candle(67, 100.0, 102.0, 99.8, 101.5, v=8000))
    
    # 68,69: continuation
    cs.append(candle(68, 101.5, 102.0, 101.2, 101.8))
    cs.append(candle(69, 101.8, 102.2, 101.5, 102.0))
    
    return cs


def build_bear(length=70):
    """Bearish setup with MSS at the end."""
    cs = []
    # 0..47: neutral / slight uptrend
    for i in range(48):
        p = 100.0 + i * 0.05
        cs.append(candle(i, p - 0.3, p + 0.4, p - 0.5, p))
    
    # 48: HIGH swing
    cs.append(candle(46, 102.0, 102.5, 101.8, 102.3))
    cs.append(candle(47, 102.5, 102.8, 102.2, 102.6))
    cs.append(candle(48, 102.8, 103.5, 102.6, 103.2))  # HIGH
    cs.append(candle(49, 103.0, 103.3, 102.7, 103.0))
    cs.append(candle(50, 102.5, 102.8, 102.3, 102.5))
    
    # 51..53: decline to LOW swing
    cs.append(candle(51, 102.0, 102.3, 101.5, 101.8))
    cs.append(candle(52, 101.5, 101.8, 101.0, 101.3))
    cs.append(candle(53, 101.0, 101.3, 100.0, 100.5))  # LOW
    
    # 54,55: higher lows
    cs.append(candle(54, 100.5, 100.8, 100.2, 100.6))
    cs.append(candle(55, 100.8, 101.2, 100.5, 101.0))
    
    # 56..62: rise
    for i in range(56, 62):
        p = 101.0 + (i - 55) * 0.15
        cs.append(candle(i, p - 0.2, p + 0.3, p - 0.4, p))
    
    # 63: high
    cs.append(candle(62, 101.5, 101.8, 101.3, 101.5))
    cs.append(candle(63, 101.5, 101.7, 101.2, 101.4))
    
    # 64: SWEEP — wick above 103.5, close below
    cs.append(candle(64, 102.0, 104.5, 101.5, 102.8, v=5000))
    
    # 65: pullback
    cs.append(candle(65, 102.0, 102.3, 101.8, 102.0))
    
    # 66: small down
    cs.append(candle(66, 101.5, 101.8, 101.0, 101.2))
    
    # 67: MSS — close below LOW swing at 53 (100.0)
    cs.append(candle(67, 101.0, 101.3, 99.0, 99.5, v=8000))
    
    # 68,69: continuation
    cs.append(candle(68, 99.5, 99.8, 99.0, 99.2))
    cs.append(candle(69, 99.2, 99.5, 98.8, 99.0))
    
    return cs


def test_bull():
    cs = build_bull()
    result = detect_mmxm(cs, "BULLUSDT", "1h")
    if result is not None:
        print(f"  PASS: bull setup: side={result['side']}, entry={result['entry']}, "
              f"sl={result['stop_loss']}, tp1={result['take_profit_1']}")
        assert result["side"] == "long"
    else:
        # Debug
        highs = [c[2] for c in cs]
        lows = [c[3] for c in cs]
        closes = [c[4] for c in cs]
        swings = _swings(highs, lows, 2, 2)
        print(f"DEBUG: {len(cs)} candles, {len(swings)} swings")
        for s in swings:
            print(f"  swing idx={s[0]} price={s[1]} kind={s[2]} conf={s[3]}")
        for mss_idx in (69, 68, 67):
            known = _known_swings(swings, mss_idx)
            print(f"  mss={mss_idx}: {len(known)} known, H={[s for s in known if s[2]=='H']}, L={[s for s in known if s[2]=='L']}")
        print(f"  closes[66]={closes[66]:.1f}, closes[67]={closes[67]:.1f}, closes[68]={closes[68]:.1f}")
        print("  FAIL: expected bull setup")


def test_bear():
    cs = build_bear()
    result = detect_mmxm(cs, "BEARUSDT", "1h")
    if result is not None:
        print(f"  PASS: bear setup: side={result['side']}, entry={result['entry']}, "
              f"sl={result['stop_loss']}, tp1={result['take_profit_1']}")
        assert result["side"] == "short"
    else:
        highs = [c[2] for c in cs]
        lows = [c[3] for c in cs]
        closes = [c[4] for c in cs]
        swings = _swings(highs, lows, 2, 2)
        print(f"DEBUG: {len(cs)} candles, {len(swings)} swings")
        for s in swings:
            print(f"  swing idx={s[0]} price={s[1]} kind={s[2]} conf={s[3]}")
        for mss_idx in (69, 68, 67):
            known = _known_swings(swings, mss_idx)
            print(f"  mss={mss_idx}: {len(known)} known, H={[s for s in known if s[2]=='H']}, L={[s for s in known if s[2]=='L']}")
        print(f"  closes[66]={closes[66]:.1f}, closes[67]={closes[67]:.1f}, closes[68]={closes[68]:.1f}")
        print("  FAIL: expected bear setup")


if __name__ == "__main__":
    print("INVESTIGATION 6c — detect_mmxm() Pipeline Tests (MSS at end)")
    print("=" * 60)
    
    print("\n--- bull_setup ---")
    test_bull()
    
    print("\n--- bear_setup ---")
    test_bear()
