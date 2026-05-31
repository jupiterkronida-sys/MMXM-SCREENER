"""INVESTIGATION 4 -- Indicator function edge cases.

Tests that each indicator function handles boundary conditions correctly
instead of silently returning garbage.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.indicators import ema, rsi, atr, macd, volume_zscore


def test_ema_empty():
    result = ema([], 14)
    assert result == [], f"ema([]) should return [], got {result}"
    print("  PASS: ema([]) -> []")


def test_ema_single():
    result = ema([42.0], 14)
    assert result == [42.0], f"ema([42.0], 14) should return [42.0], got {result}"
    print("  PASS: ema([42.0], 14) -> [42.0]")


def test_ema_period_equals_length():
    result = ema([10.0, 11.0, 12.0, 13.0, 14.0], 5)
    assert len(result) == 5, f"ema with len==period should produce len==5, got {len(result)}"
    print(f"  PASS: ema(len=5, period=5) -> {result}")


def test_rsi_too_short():
    result = rsi([50.0] * 10, 14)
    assert result is None, f"rsi(10 values) should return None, got {result}"
    print(f"  PASS: rsi(10 values, period=14) -> None")


def test_rsi_exact_minimum():
    closes = [float(i) for i in range(16)]
    result = rsi(closes, 14)
    assert result is not None
    assert len(result) == 16, f"Expected 16 outputs, got {len(result)}"
    print(f"  PASS: rsi(16 values, period=14) -> len={len(result)}, last={result[-1]:.2f}")


def test_rsi_constant_input():
    closes = [100.0] * 30
    result = rsi(closes, 14)
    assert result is not None
    assert abs(result[-1] - 50.0) < 1e-6, f"Expected 50.0 for constant input, got {result[-1]}"
    print(f"  PASS: rsi(constant, 30 values) -> {result[-1]:.2f}")


def test_atr_too_short():
    result = atr([10.0]*10, [9.0]*10, [9.5]*10, 14)
    assert result is None, f"atr(10 values) should return None, got {result}"
    print(f"  PASS: atr(10 values, period=14) -> None")


def test_atr_single():
    result = atr([10.0], [9.0], [9.5], 14)
    assert result is None, f"atr(1 value) should return None, got {result}"
    print(f"  PASS: atr(1 value, period=14) -> None")


def test_atr_exact_minimum():
    highs = [10.0 + i * 0.5 for i in range(16)]
    lows = [9.0 + i * 0.3 for i in range(16)]
    closes = [9.5 + i * 0.4 for i in range(16)]
    result = atr(highs, lows, closes, 14)
    assert result is not None and result > 0, f"Expected positive ATR, got {result}"
    print(f"  PASS: atr(16 values, period=14) -> {result:.4f}")


def test_macd_too_short():
    closes = [float(i) for i in range(10)]
    try:
        macd_line, signal_line, hist = macd(closes, 12, 26, 9)
        print(f"  INFO: macd(10 values) -> lines len={len(macd_line)}")
    except Exception as e:
        print(f"  INFO: macd(10 values) raised: {e}")


def test_vz_too_short():
    result = volume_zscore([100.0] * 15, 20)
    assert result is None, f"vz(15 values) should return None, got {result}"
    print(f"  PASS: vz(15 values, lookback=20) -> None")


def test_vz_single():
    result = volume_zscore([100.0], 20)
    assert result is None, f"vz(1 value) should return None, got {result}"
    print(f"  PASS: vz(1 value, lookback=20) -> None")


def test_vz_constant():
    result = volume_zscore([50.0] * 30, 20)
    assert result is not None and abs(result) < 1e-6, f"Expected 0 for constant input, got {result}"
    print(f"  PASS: vz(constant=50.0, 30 values) -> {result:.4f}")


def run_all():
    failures = []
    print("INVESTIGATION 4 -- Indicator Function Edge Cases")
    print("=" * 60)

    for name, fn in [
        ("ema_empty", test_ema_empty),
        ("ema_single", test_ema_single),
        ("ema_period_eq_length", test_ema_period_equals_length),
        ("rsi_too_short", test_rsi_too_short),
        ("rsi_exact_minimum", test_rsi_exact_minimum),
        ("rsi_constant", test_rsi_constant_input),
        ("atr_too_short", test_atr_too_short),
        ("atr_single", test_atr_single),
        ("atr_exact_minimum", test_atr_exact_minimum),
        ("macd_too_short", test_macd_too_short),
        ("vz_too_short", test_vz_too_short),
        ("vz_single", test_vz_single),
        ("vz_constant", test_vz_constant),
    ]:
        print(f"\n--- {name} ---")
        try:
            result = fn()
            if result is False:
                failures.append(name)
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            failures.append(name)

    print(f"\n{'='*60}")
    if not failures:
        print("ALL PASSED")
    else:
        print(f"FAILURES ({len(failures)}): {', '.join(failures)}")
    return len(failures) == 0


if __name__ == "__main__":
    import sys
    ok = run_all()
    sys.exit(0 if ok else 1)
