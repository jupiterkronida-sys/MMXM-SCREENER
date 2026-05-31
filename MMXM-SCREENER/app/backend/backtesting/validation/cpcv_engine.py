"""CPCV (Combinatorial Purged Cross-Validation) Engine with GR-5 / GR-9 guardrails.

Splits data into N sequential groups, generates C(N, K) train/test
combinations with purging, and runs backtest on each fold.
"""
import itertools
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

@dataclass
class FoldResult:
    fold_id: int
    train_groups: List[int]
    test_groups: List[int]
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
class CpcvResult:
    fold_results: List[FoldResult] = field(default_factory=list)
    sharpe_mean: float = 0.0
    sharpe_std: float = 0.0
    sharpe_min: float = 0.0
    return_mean: float = 0.0
    return_std: float = 0.0
    mdd_mean: float = 0.0
    mdd_std: float = 0.0
    win_rate_avg: float = 0.0

    # Guardrail flags
    gr5_violation: bool = False
    gr9_violation: bool = False
    pbo_estimate: float = 0.0  # Probability of Backtest Overfitting

def cpcv_split(
    num_bars: int,
    n_groups: int,
    train_groups: int,
    max_holding_bars: int = 20,
) -> List[Tuple[List[int], List[int], int, int, int, int]]:
    """Generate CPCV train/test group splits with purging.

    Args:
        num_bars: Total number of bars in the dataset.
        n_groups: Number of sequential groups (e.g., 5 for GR-5, 9 for GR-9).
        train_groups: Number of groups to train on per fold.
        max_holding_bars: Purge window — removes training bars within
                          this many bars of the test set.

    Returns:
        List of (train_group_idxs, test_group_idxs,
                 train_start_bar, train_end_bar,
                 test_start_bar, test_end_bar)
    """
    if n_groups < 2:
        raise ValueError(f"n_groups must be >= 2, got {n_groups}")
    if train_groups >= n_groups:
        raise ValueError(f"train_groups ({train_groups}) must be < n_groups ({n_groups})")

    test_groups = n_groups - train_groups
    all_groups = list(range(n_groups))
    splits = []

    # Generate all C(n_groups, train_groups) combinations
    for combo in itertools.combinations(all_groups, train_groups):
        train_idxs = list(combo)
        test_idxs = [g for g in all_groups if g not in train_idxs]

        # Bars per group (last group may have leftovers)
        base_size = num_bars // n_groups
        remainder = num_bars % n_groups

        def group_start_end(g: int) -> Tuple[int, int]:
            start = g * base_size + min(g, remainder)
            end = (g + 1) * base_size + min(g + 1, remainder)
            return start, end

        test_start_bars = [group_start_end(g)[0] for g in test_idxs]
        test_end_bars = [group_start_end(g)[1] for g in test_idxs]
        test_start = min(test_start_bars)
        test_end = max(test_end_bars)

        # Training bar range with purging
        train_start_bars = [group_start_end(g)[0] for g in train_idxs]
        train_end_bars = [group_start_end(g)[1] for g in train_idxs]
        train_start = min(train_start_bars)
        train_end = max(train_end_bars)

        # Purge: remove training bars within max_holding_bars of test set
        purge_before = max(0, test_start - max_holding_bars)
        # Also purge training bars that come after the test set
        purge_after_from_end = test_end + max_holding_bars

        # Adjust train_end: don't let training use bars close to test
        if train_end > purge_before:
            train_end = purge_before

        # If training groups are after the test set, purge from start
        # (This handles cases where test groups are earlier than train groups)
        test_end_adjusted = test_end

        # Recalculate train_start: if any train group starts after test_end,
        # we need to move the training window to avoid train-after-test
        train_bars_after_test = [
            s for s in train_start_bars if s >= test_end
        ]
        if train_bars_after_test and test_end > train_start:
            # Training groups are split around test set
            # Use only training groups before test set
            pre_test_train_idxs = [g for g in train_idxs
                                   if group_start_end(g)[1] <= test_end]
            if pre_test_train_idxs:
                train_start = min(group_start_end(g)[0] for g in pre_test_train_idxs)
                train_end = max(group_start_end(g)[1] for g in pre_test_train_idxs)
                train_end = min(train_end, test_start - max_holding_bars)
            else:
                # No valid training before test, skip this combo
                continue

        if train_end <= train_start:
            # No valid training data after purging
            continue

        splits.append((train_idxs, test_idxs, train_start, train_end,
                       test_start, test_end))

    return splits

def compute_pbo(performance_matrix: List[List[float]]) -> float:
    """Estimate Probability of Backtest Overfitting from a performance matrix.

    Args:
        performance_matrix: Rows = folds/samples, Cols = strategies/combinations.
                           The first column is typically the "selected" strategy.

    Returns:
        PBO estimate [0, 1].
    """
    if not performance_matrix or len(performance_matrix) < 2:
        return 0.0

    n_cols = len(performance_matrix[0])
    if n_cols < 2:
        return 0.0

    # Rank each row, compute logits
    ranks = []
    for row in performance_matrix:
        sorted_cols = sorted(range(len(row)), key=lambda i: row[i], reverse=True)
        rank_of_first = sorted_cols.index(0)  # rank of first strategy
        ranks.append(rank_of_first)

    # PBO = fraction of folds where selected strategy ranks in bottom half
    # (rank > median = overfitted on that fold)
    median_rank = (n_cols - 1) / 2.0
    pbo = sum(1 for r in ranks if r >= median_rank) / len(ranks)
    return pbo

class CpcvEngine:
    """Runs CPCV backtests with guardrail checks."""

    def __init__(
        self,
        n_groups: int = 5,
        train_groups: int = 2,
        max_holding_bars: int = 20,
        sharpe_threshold: float = 0.5,
    ):
        self.n_groups = n_groups
        self.train_groups = train_groups
        self.max_holding_bars = max_holding_bars
        self.sharpe_threshold = sharpe_threshold

    def run(
        self,
        data: Dict[str, List[List[float]]],
        detector_fn: Callable,
        portfolio_factory: Callable[[], "PortfolioTracker"],
    ) -> CpcvResult:
        """Run CPCV over all train/test splits.

        Args:
            data: {symbol: bars} — all symbols must have same number of bars.
            detector_fn: Callable(symbol, bars) -> SignalList.
            portfolio_factory: Creates a fresh PortfolioTracker per fold.

        Returns:
            CpcvResult with per-fold metrics, aggregates, and guardrail flags.
        """
        from backtesting.engine.event_loop import run_backtest

        num_bars = min(len(bars) for bars in data.values()) if data else 0
        if num_bars == 0:
            return CpcvResult()

        splits = cpcv_split(num_bars, self.n_groups, self.train_groups,
                            self.max_holding_bars)

        fold_results: List[FoldResult] = []
        performance_matrix: List[List[float]] = []

        for fold_id, (train_g, test_g, train_start, train_end,
                       test_start, test_end) in enumerate(splits):

            train_data = {}
            for sym, bars in data.items():
                train_data[sym] = bars[train_start:train_end]

            test_data = {}
            for sym, bars in data.items():
                test_data[sym] = bars[test_start:test_end]

            if not train_data or not test_data:
                continue

            train_pt = portfolio_factory()
            train_result = run_backtest(train_data, detector_fn, train_pt)

            test_pt = portfolio_factory()
            test_result = run_backtest(test_data, detector_fn, test_pt)

            fold = FoldResult(
                fold_id=fold_id,
                train_groups=train_g,
                test_groups=test_g,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                sharpe=test_result.portfolio.sharpe_ratio,
                total_return_pct=test_result.portfolio.total_return_pct,
                max_drawdown_pct=test_result.portfolio.max_drawdown_pct,
                win_rate=test_result.portfolio.win_rate,
                num_trades=test_result.portfolio.total_trades,
            )
            fold_results.append(fold)

            performance_matrix.append([
                train_result.portfolio.sharpe_ratio,
                test_result.portfolio.sharpe_ratio,
            ])

        if not fold_results:
            return CpcvResult()

        sharpes = [f.sharpe for f in fold_results]
        returns = [f.total_return_pct for f in fold_results]
        mdds = [f.max_drawdown_pct for f in fold_results]
        wrs = [f.win_rate for f in fold_results]

        result = CpcvResult(
            fold_results=fold_results,
            sharpe_mean=sum(sharpes) / len(sharpes),
            sharpe_std=math.sqrt(
                sum((s - sum(sharpes) / len(sharpes)) ** 2 for s in sharpes) / len(sharpes)
            ) if len(sharpes) > 1 else 0.0,
            sharpe_min=min(sharpes),
            return_mean=sum(returns) / len(returns),
            return_std=math.sqrt(
                sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / len(returns)
            ) if len(returns) > 1 else 0.0,
            mdd_mean=sum(mdds) / len(mdds),
            mdd_std=math.sqrt(
                sum((m - sum(mdds) / len(mdds)) ** 2 for m in mdds) / len(mdds)
            ) if len(mdds) > 1 else 0.0,
            win_rate_avg=sum(wrs) / len(wrs),
            pbo_estimate=compute_pbo(performance_matrix),
        )

        n_combos = len(splits)

        # GR-5: max 5 testing combos with improvement
        if self.n_groups == 5:
            improved = sum(1 for f in fold_results
                           if f.sharpe > self.sharpe_threshold)
            result.gr5_violation = improved > 5

        # GR-9: max 9 testing combos with improvement
        if self.n_groups == 9:
            improved = sum(1 for f in fold_results
                           if f.sharpe > self.sharpe_threshold)
            result.gr9_violation = improved > 9

        return result
