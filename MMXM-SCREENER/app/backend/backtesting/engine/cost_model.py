"""Cost Model — realistic all-in trade cost components.

All parameters loaded from backtest_config.yaml.
Implements commission, spread, slippage (linear market impact),
and funding cost for Bybit USDT perpetuals.
"""
import logging
import yaml
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"


class LiquidityCapWarning(UserWarning):
    """Raised when order size exceeds ADV participation cap."""


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def commission_cost(side: str, fee_pct: float) -> float:
    """Taker fee applied to notional value.

    Args:
        side: 'long' or 'short' (not used differently for Bybit — same taker fee).
        fee_pct: Fee as a fraction (e.g., 0.00055 for 0.055%).

    Returns:
        Cost as a fraction of notional (same for both sides).
    """
    return fee_pct


def spread_cost(spread_pct: float) -> float:
    """Half-spread cost: distance from mid to best ask/bid.

    For a round trip: enter at ask (half-spread above mid), exit at bid
    (half-spread below mid) → total cost = spread (one full spread).

    Args:
        spread_pct: Half-spread as fraction (e.g., 0.0001 for 0.01%).

    Returns:
        Round-trip spread cost as fraction of notional.
    """
    return 2.0 * spread_pct  # one half on entry, one half on exit


def slippage_cost(
    order_size_usd: float,
    adv_usd: float,
    baseline_slippage_pct: float,
    stress_multiple: float = 1.0,
) -> float:
    """Linear market impact model.

    slippage = baseline_slippage_pct * (order_size_usd / adv_usd) * stress_multiple

    References:
        Almgren-Chriss market impact model (linear simplification).

    Args:
        order_size_usd: Trade notional in USD.
        adv_usd: Average Daily Volume in USD for the symbol.
        baseline_slippage_pct: Baseline slippage fraction (e.g., 0.0001).
        stress_multiple: Stress test multiplier (1.0 = normal, 3.0 = stressed).

    Returns:
        Slippage cost as fraction of notional.

    Raises:
        LiquidityCapWarning: If order_size_usd / adv_usd > 0.05 (5% ADV cap).
    """
    if adv_usd <= 0:
        logger.warning(f"ADV is {adv_usd}, using baseline slippage only")
        return baseline_slippage_pct * stress_multiple

    participation_rate = order_size_usd / adv_usd
    config = _load_config()
    exec_config = config.get("execution", {})
    adv_cap = exec_config.get("adv_participation_cap", 0.05)

    if participation_rate > adv_cap:
        import warnings
        warnings.warn(
            f"Order ${order_size_usd:,.0f} exceeds {adv_cap:.1%} of ADV "
            f"(${adv_usd:,.0f}): participation rate = {participation_rate:.2%}",
            LiquidityCapWarning,
        )

    return baseline_slippage_pct * participation_rate * stress_multiple


def funding_cost(
    holding_period_hours: float,
    funding_rate_8h: float = 0.0001,
) -> float:
    """Bybit funding every 8 hours — pro-rate for actual holding period.

    funding_cost = (holding_period_hours / 8) * funding_rate_8h

    Bybit perpetuals settle funding every 8h (00:00, 08:00, 16:00 UTC).
    Typical funding rate is ~0.01% per 8h for neutral markets.

    Args:
        holding_period_hours: Duration the position is held in hours.
        funding_rate_8h: Funding rate per 8h interval as fraction.

    Returns:
        Funding cost as fraction of notional.
    """
    intervals = holding_period_hours / 8.0
    return intervals * funding_rate_8h


def total_cost_pct(
    side: str,
    order_size_usd: float,
    adv_usd: float,
    holding_period_hours: float,
    stress_multiple: float = 1.0,
    spread_pct: Optional[float] = None,
    funding_rate_8h: Optional[float] = None,
) -> float:
    """Total round-trip cost as a fraction of notional.

    Sums:
        commission (entry) + commission (exit)
        + spread (entry half-spread + exit half-spread)
        + slippage (entry + exit) × 2
        + funding

    Args:
        side: Trade direction ('long' or 'short').
        order_size_usd: Trade notional in USD.
        adv_usd: Symbol ADV in USD.
        holding_period_hours: Position holding duration in hours.
        stress_multiple: Slippage stress multiplier (default 1.0).
        spread_pct: Half-spread fraction (default from config).
        funding_rate_8h: Funding per 8h interval (default from config).

    Returns:
        Total cost as fraction of notional.
    """
    config = _load_config()
    exec_cfg = config.get("execution", {})

    fee_pct = exec_cfg.get("bybit_taker_fee_pct", 0.00055)
    if spread_pct is None:
        spread_pct = exec_cfg.get("slippage_baseline_pct", 0.0001)
    if funding_rate_8h is None:
        funding_rate_8h = 0.0001

    baseline = exec_cfg.get("slippage_baseline_pct", 0.0001)

    comm = commission_cost(side, fee_pct) * 2  # entry + exit
    sprd = spread_cost(spread_pct)

    # Entry slippage uses the order_size_usd relative to ADV
    entry_slip = slippage_cost(order_size_usd, adv_usd, baseline, stress_multiple)
    # Exit slippage assumes same order size (liquidity may differ, but reasonable)
    exit_slip = entry_slip
    total_slip = entry_slip + exit_slip

    fund = funding_cost(holding_period_hours, funding_rate_8h)

    total = comm + sprd + total_slip + fund
    logger.debug(
        f"Total cost: comm={comm:.6f} sprd={sprd:.6f} slip={total_slip:.6f} fund={fund:.6f} "
        f"= {total:.6f}"
    )
    return total
