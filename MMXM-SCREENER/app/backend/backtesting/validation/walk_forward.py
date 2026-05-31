"""Rolling walk-forward analysis with expanding/sliding windows.

Simulates a sequential backtest where the strategy is tested on unseen
out-of-sample data following each in-sample training period.
"""
import math
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

@dataclass
class WalkForwardWindow:
    window_id: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    sharpe: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0

@dataclass
class WalkForwardResult:
    windows: List[WalkForwardWindow] = field(default_factory=list)
    sharpe_mean: float = 0.0
    sharpe_std: float = 0.0
    sharpe_min: float = 0.0
    sharpe_trend: float = 0.0  # slope of Sharpe across windows (positive = improving)
    return_mean: float = 0.0
    mdd_mean: float = 0.0
    win_rate_avg: float = 0.0
    total_oos_bars: int = 0

def run_walk_forward(
    data: Dict[str, List[List[float]]],
    detector_fn: Callable,
    portfolio_factory: Callable[[], "PortfolioTracker"],
    train_size: int = 500,
    test_size: int = 100,
    step_size: int | None = None,
    expanding: bool = True,
) -> WalkForwardResult:
    """Run walk-forward analysis over sequential windows.

    Args:
        data: {symbol: bars}.
        detector_fn: Callable(symbol, bars) -> SignalList.
        portfolio_factory: Creates fresh PortfolioTracker per window.
        train_size: Number of bars per training period.
        test_size: Number of bars per test period.
        step_size: Number of bars to slide forward (default = test_size).
        expanding: If True, train_start = 0 (expanding window).
                   If False, train window slides forward (walk-forward).

    Returns:
        WalkForwardResult with per-window and aggregate metrics.
    """
    from backtesting.engine.event_loop import run_backtest

    if step_size is None:
        step_size = test_size

    num_bars = min(len(bars) for bars in data.values()) if data else 0
    if num_bars < train_size + test_size:
        return WalkForwardResult()

    windows: List[WalkForwardWindow] = []
    window_id = 0

    while True:
        offset = window_id * step_size
        if expanding:
            tr_start = 0
            tr_end = train_size + offset   # expanding train window
        else:
            tr_start = offset
            tr_end = tr_start + train_size

        te_start = tr_end
        te_end = te_start + test_size

        if te_end > num_bars:
            break

        train_data = {}
        test_data = {}
        for sym, bars in data.items():
            if len(bars) >= te_end:
                train_data[sym] = bars[tr_start:tr_end]
                test_data[sym] = bars[te_start:te_end]

        if not train_data or not test_data:
            break

        train_pt = portfolio_factory()
        run_backtest(train_data, detector_fn, train_pt)

        test_pt = portfolio_factory()
        run_backtest(test_data, detector_fn, test_pt)

        windows.append(WalkForwardWindow(
            window_id=window_id,
            train_start=tr_start,
            train_end=tr_end,
            test_start=te_start,
            test_end=te_end,
            sharpe=test_pt.sharpe_ratio,
            total_return_pct=test_pt.total_return_pct,
            max_drawdown_pct=test_pt.max_drawdown_pct,
            win_rate=test_pt.win_rate,
            num_trades=test_pt.total_trades,
        ))

        window_id += 1

    if not windows:
        return WalkForwardResult()

    sharpes = [w.sharpe for w in windows]
    returns = [w.total_return_pct for w in windows]
    mdds = [w.max_drawdown_pct for w in windows]
    wrs = [w.win_rate for w in windows]

    # Sharpe trend (linear regression slope)
    n = len(windows)
    if n >= 2:
        x_mean = (n - 1) / 2.0
        y_mean = sum(sharpes) / n
        numerator = sum((i - x_mean) * (s - y_mean) for i, s in enumerate(sharpes))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        sharpe_trend = numerator / denominator if denominator > 0 else 0.0
    else:
        sharpe_trend = 0.0

    return WalkForwardResult(
        windows=windows,
        sharpe_mean=sum(sharpes) / n,
        sharpe_std=math.sqrt(
            sum((s - sum(sharpes) / n) ** 2 for s in sharpes) / n
        ) if n > 1 else 0.0,
        sharpe_min=min(sharpes),
        sharpe_trend=sharpe_trend,
        return_mean=sum(returns) / n,
        mdd_mean=sum(mdds) / n,
        win_rate_avg=sum(wrs) / n,
        total_oos_bars=test_size * n,
    )
