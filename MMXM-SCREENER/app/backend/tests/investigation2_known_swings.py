"""INVESTIGATION 2 — _known_swings() completeness."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mmxm import _known_swings


def test_known_swings_filters_by_confirmed_at():
    """At bar index k, only swings with confirmed_at <= k should be returned.

    confirmed_at is s[3] in the 4-tuple (idx, price, kind, confirmed_at).
    """
    # Build swings with various confirmed_at values
    swings = [
        (0, 50.0, "L", 2),    # confirmed at bar 2
        (3, 60.0, "H", 5),    # confirmed at bar 5
        (4, 45.0, "L", 6),    # confirmed at bar 6
        (7, 70.0, "H", 9),    # confirmed at bar 9
        (8, 40.0, "L", 10),   # confirmed at bar 10
    ]

    # At bar index 4: only swing 0 is confirmed (confirmed_at=2 <= 4)
    known_4 = _known_swings(swings, 4)
    known_ids_4 = {s[0] for s in known_4}
    assert known_ids_4 == {0}, f"At bar 4 expected {{0}}, got {known_ids_4}"
    print(f"  PASS (bar 4): confirmed swings at indices {known_ids_4}")

    # At bar index 5: swings 0 and 3 confirmed
    known_5 = _known_swings(swings, 5)
    known_ids_5 = {s[0] for s in known_5}
    assert known_ids_5 == {0, 3}, f"At bar 5 expected {{0, 3}}, got {known_ids_5}"
    print(f"  PASS (bar 5): confirmed swings at indices {known_ids_5}")

    # At bar index 9: swings 0, 3, 4, 7 confirmed
    known_9 = _known_swings(swings, 9)
    known_ids_9 = {s[0] for s in known_9}
    assert known_ids_9 == {0, 3, 4, 7}, f"At bar 9 expected {{0, 3, 4, 7}}, got {known_ids_9}"
    print(f"  PASS (bar 9): confirmed swings at indices {known_ids_9}")

    # At bar index 1: no swings confirmed (confirmed_at_min=2 > 1)
    known_1 = _known_swings(swings, 1)
    assert len(known_1) == 0, f"At bar 1 expected 0 swings, got {len(known_1)}"
    print(f"  PASS (bar 1): no swings confirmed yet")

    return True


def test_known_swings_all_confirmed():
    """When at_idx >= max confirmed_at, all swings are returned."""
    swings = [
        (5, 55.0, "H", 7),
        (8, 44.0, "L", 10),
        (12, 66.0, "H", 14),
    ]
    known = _known_swings(swings, 20)
    assert len(known) == 3, f"At bar 20 expected all 3 swings, got {len(known)}"
    print(f"  PASS (bar 20): all {len(known)} swings confirmed")
    return True


if __name__ == "__main__":
    print("INVESTIGATION 2 — _known_swings() completeness")
    print("=" * 60)
    test_known_swings_filters_by_confirmed_at()
    test_known_swings_all_confirmed()
    print("\nAll Investigation 2 tests passed.")
