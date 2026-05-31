"""Execution capacity estimation and slippage stress testing.

Simulates the impact of order size on execution quality and estimates
maximum sustainable position size per symbol (GR-7 guardrail).
"""
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

from backtesting.engine.cost_model import slippage_cost, LiquidityCapWarning

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


@dataclass
class CapacityPoint:
    order_size_usd: float
    adv_participation_pct: float
    slippage_cost_pct: float
    flagged: bool  # True if exceeds guardrail


@dataclass
class CapacityResult:
    symbol: str
    adv_usd: float
    points: List[CapacityPoint] = field(default_factory=list)
    max_safe_position: float = 0.0       # largest order before slippage > threshold
    gr7_violation: bool = False           # True if 5% ADV exceeded
    gr7_threshold: float = 0.05           # 5% ADV cap


def estimate_capacity(
    symbol: str,
    adv_usd: float,
    baseline_slippage_pct: float = 0.0001,
    max_slippage_threshold: float = 0.001,
    gr7_adv_cap: float = 0.05,
) -> CapacityResult:
    """Estimate execution capacity for a single symbol.

    Sweeps order sizes from 0.1% to 50% of ADV and computes slippage cost.
    Flags the largest order that stays within max_slippage_threshold.

    Args:
        symbol: Trading pair.
        adv_usd: Average daily volume in USD.
        baseline_slippage_pct: Baseline slippage cost (fraction).
        max_slippage_threshold: Max acceptable slippage cost (fraction).
        gr7_adv_cap: Max % of ADV per position (GR-7 guardrail).

    Returns:
        CapacityResult with sweep points and guardrail status.
    """
    if adv_usd <= 0:
        return CapacityResult(symbol=symbol, adv_usd=0.0, max_safe_position=0.0)

    # Sweep order sizes from 0.1% to 50% of ADV
    participation_levels = [0.001, 0.005, 0.01, 0.02, 0.05, 0.10, 0.20, 0.50]
    points: List[CapacityPoint] = []
    max_safe = 0.0
    gr7_violation = False

    for part in participation_levels:
        order_size = adv_usd * part
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LiquidityCapWarning)
            slip = slippage_cost(
                order_size, adv_usd, baseline_slippage_pct, stress_multiple=1.0
            )

        flagged = part > gr7_adv_cap
        if flagged:
            gr7_violation = True

        if slip <= max_slippage_threshold and not flagged:
            max_safe = order_size

        points.append(CapacityPoint(
            order_size_usd=order_size,
            adv_participation_pct=part * 100,  # store as %
            slippage_cost_pct=slip,
            flagged=flagged,
        ))

    return CapacityResult(
        symbol=symbol,
        adv_usd=adv_usd,
        points=points,
        max_safe_position=max_safe,
        gr7_violation=gr7_violation,
        gr7_threshold=gr7_adv_cap * 100,  # store as %
    )


def capacity_stress_test(
    symbols_adv: Dict[str, float],
    baseline_slippage_pct: float = 0.0001,
    max_slippage_threshold: float = 0.001,
) -> Dict[str, CapacityResult]:
    """Run capacity estimation across multiple symbols.

    Args:
        symbols_adv: {symbol: adv_usd} mapping.
        baseline_slippage_pct: Baseline slippage per symbol.
        max_slippage_threshold: Max acceptable slippage per symbol.

    Returns:
        {symbol: CapacityResult} for each input symbol.
    """
    results = {}
    for sym, adv in symbols_adv.items():
        results[sym] = estimate_capacity(
            sym, adv, baseline_slippage_pct, max_slippage_threshold,
        )
    return results


def find_max_position_size(
    capacity_results: Dict[str, CapacityResult],
) -> float:
    """Find the maximum safe position across all symbols.

    Returns the minimum max_safe_position across all symbols
    (ensures every symbol can handle the position).
    """
    if not capacity_results:
        return 0.0
    safe_sizes = [r.max_safe_position for r in capacity_results.values()]
    return min(safe_sizes) if safe_sizes else 0.0
