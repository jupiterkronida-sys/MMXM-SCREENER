"""Bar-by-bar event loop orchestrator for MMXM backtesting.

Iterates bars chronologically, calls the detector per symbol,
simulates trades via order_manager, and records results via portfolio_tracker.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List

import yaml

from backtesting.engine.order_manager import simulate_trade, TradeResult
from backtesting.engine.portfolio_tracker import PortfolioTracker

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

SignalList = List[dict]

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

@dataclass
class BacktestResult:
    portfolio: PortfolioTracker
    trade_log: List[TradeResult] = field(default_factory=list)
    total_bars: int = 0
    total_signals: int = 0

def run_backtest(
    data: Dict[str, List[List[float]]],
    detector_fn: Callable[[str, List[List[float]]], SignalList],
    portfolio: PortfolioTracker,
    max_holding_bars: int | None = None,
    order_size_usd: float | None = None,
    max_concurrent: int = 5,
) -> BacktestResult:
    """Run a full backtest over historical bar data.

    Args:
        data: {symbol: list of candles [t,o,h,l,c,v]} — all symbols must
              share the same number of bars.
        detector_fn: Callable(symbol, bars_up_to_index) returning a list of
                     signal dicts. Each dict::
                       - signal_bar_index: int
                       - entry_price: float
                       - stop_loss: float
                       - take_profit_levels: List[float]
                       - side: str ('long' | 'short')
        portfolio: PortfolioTracker to record trades and equity.
        max_holding_bars: Override for max holding period (default from config).
        order_size_usd: Notional per trade (default from config).
        max_concurrent: Max simultaneous open positions.

    Returns:
        BacktestResult with updated portfolio and trade log.
    """
    cfg = _load_config()
    exec_cfg = cfg.get("execution", {})
    mmxm_cfg = cfg.get("mmxm", {})

    if max_holding_bars is None:
        max_holding_bars = mmxm_cfg.get("max_holding_bars", 20)
    if order_size_usd is None:
        order_size_usd = exec_cfg.get("default_order_size_usd", 10_000.0)

    # Determine number of bars (all symbols should match)
    num_bars = min(len(bars) for bars in data.values()) if data else 0
    trade_log: List[TradeResult] = []
    total_signals = 0

    # Track currently open TradeResults for SL/TP/MTM by symbol
    open_results: Dict[str, List[TradeResult]] = {}

    for bar_idx in range(num_bars):
        current_prices: Dict[str, float] = {}

        for sym, sym_bars in data.items():
            if bar_idx >= len(sym_bars):
                continue
            bar = sym_bars[bar_idx]
            current_prices[sym] = bar[4]  # close

            signals = detector_fn(sym, sym_bars[: bar_idx + 1])
            for sig in signals:
                total_signals += 1
                sig_bar = sig.get("signal_bar_index", bar_idx)
                entry = sig["entry_price"]
                sl = sig["stop_loss"]
                tps = sig.get("take_profit_levels", [])
                side = sig.get("side", "long")

                current_open = sum(
                    1
                    for pos in portfolio.open_positions
                    if pos.trade.symbol == sym
                )
                if current_open >= max_concurrent:
                    logger.debug(
                        "Skipping signal %s bar %d: %d concurrent open",
                        sym, sig_bar, current_open,
                    )
                    continue

                try:
                    trade = simulate_trade(
                        symbol=sym,
                        side=side,
                        signal_bar_index=sig_bar,
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit_levels=tps,
                        bars=sym_bars,
                        max_holding_bars=max_holding_bars,
                        order_size_usd=order_size_usd,
                    )
                except (ValueError, IndexError) as e:
                    logger.warning("Trade simulation error: %s", e)
                    continue

                portfolio.open_trade(trade, order_size_usd)
                trade_log.append(trade)
                open_results.setdefault(sym, []).append(trade)

        portfolio.mark_to_market(bar_idx, current_prices)

    # Close any remaining open positions at last bar
    for pos in list(portfolio.open_positions):
        portfolio.close_trade(pos.trade)

    return BacktestResult(
        portfolio=portfolio,
        trade_log=trade_log,
        total_bars=num_bars,
        total_signals=total_signals,
    )
