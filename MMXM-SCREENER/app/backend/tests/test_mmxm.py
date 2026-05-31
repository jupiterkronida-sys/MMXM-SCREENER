import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mmxm import _find_fvg, _find_recent_setup


def _candle(idx, open_=10.0, high=10.5, low=9.5, close=10.0):
    return [idx * 60_000, open_, high, low, close, 100.0]


def test_find_fvg_prefers_newest_gap():
    candles = [_candle(i) for i in range(12)]
    candles[3][2] = 10.0
    candles[5][3] = 11.0
    candles[6][2] = 12.0
    candles[8][3] = 13.0

    assert _find_fvg(candles, "bull", 8, 0.1) == (12.0, 13.0)


def test_find_recent_setup_uses_newest_crossing_candidate():
    candles = [_candle(i) for i in range(30)]
    closes = [c[4] for c in candles]
    # (idx, price, kind, confirmed_at) — confirmed_at = idx + right(2)
    swings = [
        (5, 9.0, "L", 7),
        (7, 12.0, "H", 9),
        (9, 10.0, "L", 11),
        (13, 8.0, "L", 15),
        (15, 13.0, "H", 17),
        (17, 11.0, "L", 19),
        (20, 13.0, "H", 22),
    ]

    candles[27][3] = 10.8
    candles[27][4] = 11.2
    candles[28][2] = 13.6
    candles[28][3] = 12.8
    candles[28][4] = 12.9
    candles[29][2] = 14.0
    candles[29][3] = 13.0
    candles[29][4] = 13.8
    closes[28] = 12.9
    closes[29] = 13.8

    setup = _find_recent_setup(candles, swings, closes)

    assert setup["mss_idx"] == 29
    assert setup["sweep_idx"] == 27
    assert setup["swept_swing"] == (17, 11.0, "L", 19)
