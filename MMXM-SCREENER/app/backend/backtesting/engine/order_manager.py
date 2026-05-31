"""Order Manager — simulate order execution bar-by-bar with realistic fill logic.

Entry fills at open of the bar AFTER signal bar (no same-bar fills).
Stop-loss fills at SL price (not close). Take-profit fills at TP price.
Supports scale-out for multi-TP strategies (up to 3 targets).
Max holding period closes at bar close.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


@dataclass
class TradeResult:
    symbol: str
    entry_bar_index: int
    exit_bar_index: int
    side: str
    entry_price: float
    exit_price: float            # weighted average exit for scale-out
    gross_pnl_pct: float
    net_pnl_pct: float           # after total_cost_pct
    total_cost_pct: float
    exit_reason: str             # 'TP1' | 'TP2' | 'TP3' | 'SL' | 'TIMEOUT'
    holding_bars: int
    scale_out_fills: List[dict] = field(default_factory=list)
    # Each fill: {"bar_idx": int, "price": float, "reason": str, "portion": float,
    #             "pnl_pct": float}


def simulate_trade(
    symbol: str,
    side: str,
    signal_bar_index: int,
    entry_price: float,
    stop_loss: float,
    take_profit_levels: List[float],
    bars: List[List[float]],
    max_holding_bars: int = 20,
    order_size_usd: float = 10_000.0,
    adv_usd: float = 10_000_000.0,
) -> TradeResult:
    """Simulate a single trade bar-by-bar.

    Args:
        symbol: Trading pair symbol.
        side: 'long' or 'short'.
        signal_bar_index: Index of the signal candle. Entry fills on signal_bar+1.
        entry_price: Theoretically determined entry (used for PnL, but fill at bar open).
        stop_loss: Stop-loss price.
        take_profit_levels: Up to 3 TP levels (ordered by proximity to entry).
        bars: Full candle array [t, o, h, l, c, v] — must be sufficient bars beyond signal.
        max_holding_bars: Max bars to hold before forced exit.
        order_size_usd: Trade notional for cost calculation.
        adv_usd: Symbol ADV for cost calculation.

    Returns:
        TradeResult with fill details.
    """
    from backtesting.engine.cost_model import total_cost_pct

    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got '{side}'")

    n = len(bars)
    entry_bar_idx = signal_bar_index + 1
    if entry_bar_idx >= n:
        raise ValueError(f"Not enough bars: entry bar {entry_bar_idx} >= {n}")

    # Entry fill at market (open of entry bar).
    # If bar open is beyond the SL (means structure already invalidated), skip trade.
    entry_bar = bars[entry_bar_idx]
    actual_entry = entry_bar[1]  # open
    if side == "long" and actual_entry <= stop_loss:
        raise ValueError(f"Long entry {actual_entry} <= SL {stop_loss} -> skip")
    if side == "short" and actual_entry >= stop_loss:
        raise ValueError(f"Short entry {actual_entry} >= SL {stop_loss} -> skip")

    # Sort TP levels by proximity to entry for correct fill order
    if side == "long":
        pending_tps = sorted(tp for tp in take_profit_levels if tp > actual_entry)
    else:
        pending_tps = sorted((tp for tp in take_profit_levels if tp < actual_entry), reverse=True)

    num_tps = min(len(pending_tps), 3)
    portion_per_tp = 1.0 / num_tps if num_tps > 0 else 1.0

    remaining_portion = 1.0
    scale_out_fills = []
    final_exit_price = actual_entry
    final_exit_reason = "TIMEOUT"
    total_gross_pnl = 0.0

    for bar_idx in range(entry_bar_idx, n):
        bar = bars[bar_idx]
        o, h, l, c = bar[1], bar[2], bar[3], bar[4]
        holding_bars = bar_idx - entry_bar_idx + 1

        if remaining_portion > 0:
            sl_hit = False
            if side == "long" and l <= stop_loss:
                sl_hit = True
            elif side == "short" and h >= stop_loss:
                sl_hit = True

            if sl_hit:
                portion = remaining_portion
                if side == "long":
                    pnl = (stop_loss - actual_entry) / actual_entry
                else:
                    pnl = (actual_entry - stop_loss) / actual_entry
                total_gross_pnl += pnl * portion
                scale_out_fills.append({
                    "bar_idx": bar_idx,
                    "price": stop_loss,
                    "reason": "SL",
                    "portion": portion,
                    "pnl_pct": pnl,
                })
                final_exit_price = stop_loss
                final_exit_reason = "SL"
                remaining_portion = 0.0
                break

        still_pending = []
        for tp_price in pending_tps:
            if remaining_portion < 1e-12:
                break
            tp_hit = False
            if side == "long" and h >= tp_price:
                tp_hit = True
            elif side == "short" and l <= tp_price:
                tp_hit = True

            if tp_hit:
                portion = min(portion_per_tp, remaining_portion)
                if side == "long":
                    pnl = (tp_price - actual_entry) / actual_entry
                else:
                    pnl = (actual_entry - tp_price) / actual_entry
                total_gross_pnl += pnl * portion
                scale_out_fills.append({
                    "bar_idx": bar_idx,
                    "price": tp_price,
                    "reason": f"TP{len(scale_out_fills) + 1}",
                    "portion": portion,
                    "pnl_pct": pnl,
                })
                final_exit_price = tp_price
                final_exit_reason = scale_out_fills[-1]["reason"]
                remaining_portion -= portion
            else:
                still_pending.append(tp_price)

        pending_tps = still_pending

        if holding_bars >= max_holding_bars and remaining_portion > 0:
            portion = remaining_portion
            if side == "long":
                pnl = (c - actual_entry) / actual_entry
            else:
                pnl = (actual_entry - c) / actual_entry
            total_gross_pnl += pnl * portion
            scale_out_fills.append({
                "bar_idx": bar_idx,
                "price": c,
                "reason": "TIMEOUT",
                "portion": portion,
                "pnl_pct": pnl,
            })
            final_exit_price = c
            final_exit_reason = "TIMEOUT"
            remaining_portion = 0.0
            break

        if remaining_portion < 1e-12:
            break

    # Fallback if residual portion remains (no more bars)
    if remaining_portion > 1e-12:
        last_bar = bars[-1]
        portion = remaining_portion
        if side == "long":
            pnl = (last_bar[4] - actual_entry) / last_bar[4]
        else:
            pnl = (actual_entry - last_bar[4]) / actual_entry
        total_gross_pnl += pnl * portion
        scale_out_fills.append({
            "bar_idx": n - 1,
            "price": last_bar[4],
            "reason": "TIMEOUT",
            "portion": portion,
            "pnl_pct": pnl,
        })
        final_exit_price = last_bar[4]
        final_exit_reason = "TIMEOUT"

    # Compute holding bars (from entry bar to last fill bar)
    last_fill_bar = scale_out_fills[-1]["bar_idx"] if scale_out_fills else entry_bar_idx
    holding_bars = last_fill_bar - entry_bar_idx + 1

    # Compute weighted average exit price
    weighted_price = sum(f["price"] * f["portion"] for f in scale_out_fills) if scale_out_fills else final_exit_price

    # Apply costs (use the holding period as the max of all sub-holds)
    costs = total_cost_pct(side, order_size_usd, adv_usd, holding_bars * 1.0)  # 1 bar = 1h

    gross_pnl_pct = total_gross_pnl
    net_pnl_pct = gross_pnl_pct - costs

    return TradeResult(
        symbol=symbol,
        entry_bar_index=entry_bar_idx,
        exit_bar_index=last_fill_bar,
        side=side,
        entry_price=actual_entry,
        exit_price=round(weighted_price, 8),
        gross_pnl_pct=round(gross_pnl_pct, 6),
        net_pnl_pct=round(net_pnl_pct, 6),
        total_cost_pct=round(costs, 6),
        exit_reason=final_exit_reason,
        holding_bars=holding_bars,
        scale_out_fills=scale_out_fills,
    )
