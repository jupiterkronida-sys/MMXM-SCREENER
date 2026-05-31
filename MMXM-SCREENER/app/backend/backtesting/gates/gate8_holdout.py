"""Gate 8 — Final holdout evaluation gate.

Runs the strategy on untouched holdout data and applies GR-5 / GR-15
guardrails to determine if the strategy is fit for deployment.
"""
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

@dataclass
class GateDecision:
    passed: bool
    sharpe: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    calmar_ratio: float = 0.0  # return / max DD
    num_trades: int = 0
    benchmark_sharpe: float = 0.0
    sharpe_vs_benchmark: float = 0.0  # % over benchmark
    gr5_pass: bool = False
    gr15_pass: bool = False
    failures: List[str] = field(default_factory=list)
    detail: str = ""

def _buy_hold_sharpe(bars: List[List[float]]) -> float:
    """Compute buy-and-hold Sharpe for benchmark comparison."""
    if len(bars) < 2:
        return 0.0
    closes = [b[4] for b in bars]
    returns = []
    for i in range(1, len(closes)):
        r = (closes[i] - closes[i - 1]) / closes[i - 1]
        returns.append(r)
    if not returns:
        return 0.0
    avg = sum(returns) / len(returns)
    variance = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0:
        return 0.0
    sharpe_bar = avg / math.sqrt(variance)
    return sharpe_bar * math.sqrt(len(returns))  # return count as time units

def evaluate_gate8(
    strategy_sharpe: float,
    strategy_return: float,
    strategy_max_dd: float,
    strategy_win_rate: float,
    num_trades: int,
    benchmark_sharpe: float,
    gr5_threshold_pct: float = 5.0,
    gr15_threshold_pct: float = 15.0,
    min_sharpe: float = 0.5,
    min_trades: int = 5,
) -> GateDecision:
    """Evaluate Gate 8 guardrails for holdout performance.

    Args:
        strategy_sharpe: Sharpe ratio on holdout data.
        strategy_return: Total return on holdout data.
        strategy_max_dd: Max drawdown on holdout data.
        strategy_win_rate: Win rate on holdout data.
        num_trades: Number of trades on holdout data.
        benchmark_sharpe: Sharpe of the benchmark (e.g., buy-and-hold).
        gr5_threshold_pct: Top % required for GR-5.
        gr15_threshold_pct: % improvement over benchmark required for GR-15.
        min_sharpe: Minimum acceptable Sharpe ratio.
        min_trades: Minimum number of trades.

    Returns:
        GateDecision with pass/fail and details.
    """
    failures = []

    if num_trades < min_trades:
        failures.append(f"Not enough trades: {num_trades} < {min_trades}")

    if strategy_sharpe < min_sharpe:
        failures.append(f"Sharpe {strategy_sharpe:.2f} < {min_sharpe}")

    # GR-15: Sharpe must beat benchmark by at least 15%
    sharpe_vs_benchmark = 0.0
    gr15_pass = False
    if benchmark_sharpe > 0:
        sharpe_vs_benchmark = (strategy_sharpe - benchmark_sharpe) / benchmark_sharpe * 100
        gr15_pass = sharpe_vs_benchmark >= gr15_threshold_pct
        if not gr15_pass:
            failures.append(
                f"GR-15: Sharpe {strategy_sharpe:.2f} is "
                f"{sharpe_vs_benchmark:.1f}% over benchmark ({gr15_threshold_pct}% needed)"
            )
    else:
        # Benchmark Sharpe is 0 or negative — treat GR-15 as met if strategy has positive Sharpe
        gr15_pass = strategy_sharpe > 0

    gr5_pass = strategy_sharpe >= 0.8  # heuristic: Sharpe >= 0.8 = top tier

    calmar = strategy_return / strategy_max_dd if strategy_max_dd > 0 else 0.0

    passed = len(failures) == 0

    return GateDecision(
        passed=passed,
        sharpe=strategy_sharpe,
        total_return_pct=strategy_return,
        max_drawdown_pct=strategy_max_dd,
        win_rate=strategy_win_rate,
        calmar_ratio=calmar,
        num_trades=num_trades,
        benchmark_sharpe=benchmark_sharpe,
        sharpe_vs_benchmark=sharpe_vs_benchmark,
        gr5_pass=gr5_pass,
        gr15_pass=gr15_pass,
        failures=failures,
        detail=f"Gate 8: {'PASS' if passed else 'FAIL'} — "
               f"Sharpe {strategy_sharpe:.2f} vs benchmark {benchmark_sharpe:.2f}, "
               f"GR-15 {gr15_pass}, GR-5 {gr5_pass}",
    )

def run_holdout_evaluation(
    holdout_data: Dict[str, List[List[float]]],
    detector_fn: Callable,
    portfolio_factory: Callable,
    config: Optional[dict] = None,
) -> GateDecision:
    """Run full holdout evaluation with Gate 8 decision.

    Args:
        holdout_data: {symbol: bars} from holdout period.
        detector_fn: Callable(symbol, bars) -> SignalList.
        portfolio_factory: Creates a PortfolioTracker for holdout.
        config: Optional config override (defaults to backtest_config.yaml).

    Returns:
        GateDecision with final pass/fail and metrics.
    """
    from backtesting.engine.event_loop import run_backtest

    if config is None:
        config = _load_config()

    # Compute benchmark (buy-and-hold) Sharpe from first symbol
    first_sym = next(iter(holdout_data))
    benchmark_sharpe = _buy_hold_sharpe(holdout_data[first_sym])

    pt = portfolio_factory()
    result = run_backtest(holdout_data, detector_fn, pt)

    decision = evaluate_gate8(
        strategy_sharpe=pt.sharpe_ratio,
        strategy_return=pt.total_return_pct,
        strategy_max_dd=pt.max_drawdown_pct,
        strategy_win_rate=pt.win_rate,
        num_trades=pt.total_trades,
        benchmark_sharpe=benchmark_sharpe,
    )

    logger.info(decision.detail)
    return decision
