"""Generate human-readable backtest reports from metrics and portfolio data."""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from backtesting.reporting.metrics import MetricsReport, compute_metrics
from backtesting.engine.portfolio_tracker import PortfolioTracker
from backtesting.gates.gate8_holdout import GateDecision

logger = logging.getLogger(__name__)


def generate_text_report(
    metrics: MetricsReport,
    gate_decision: Optional[GateDecision] = None,
    title: str = "MMXM Strategy Backtest Report",
) -> str:
    """Generate a plain-text backtest report.

    Args:
        metrics: Computed MetricsReport from compute_metrics().
        gate_decision: Optional Gate 8 evaluation result.
        title: Report title.

    Returns:
        Formatted plain-text report string.
    """
    lines = [
        "=" * 60,
        f"  {title}",
        f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        "",
        "--- PERFORMANCE SUMMARY ---",
        f"  Total Return:         {metrics.total_return_pct:>8.2f}%",
        f"  Annualized Return:    {metrics.annualized_return_pct:>8.2f}%",
        f"  Annualized Vol:       {metrics.annualized_volatility_pct:>8.2f}%",
        "",
        "--- RISK METRICS ---",
        f"  Sharpe Ratio:         {metrics.sharpe_ratio:>8.4f}",
        f"  Sortino Ratio:        {metrics.sortino_ratio:>8.4f}",
        f"  Calmar Ratio:         {metrics.calmar_ratio:>8.2f}",
        f"  Max Drawdown:         {metrics.max_drawdown_pct:>8.2f}%",
        f"  Max DD Duration:      {metrics.max_drawdown_duration_bars:>6} bars",
        f"  VaR (95%):            {metrics.value_at_risk_95_pct:>8.2f}%",
        "",
        "--- TRADING STATISTICS ---",
        f"  Total Trades:         {metrics.total_trades:>8}",
        f"  Win Rate:             {metrics.win_rate:>8.2f}%",
        f"  Avg Win:              {metrics.avg_win_pct:>8.2f}%",
        f"  Avg Loss:             {metrics.avg_loss_pct:>8.2f}%",
        f"  Profit Factor:        {metrics.profit_factor:>8.4f}",
        f"  Avg Holding:          {metrics.avg_holding_bars:>8.1f} bars",
        "",
        "--- CAPITAL ---",
        f"  Initial Capital:      ${metrics.initial_capital:>10,.2f}",
        f"  Final Equity:         ${metrics.final_equity:>10,.2f}",
        f"  Net P&L:              ${metrics.final_equity - metrics.initial_capital:>10,.2f}",
    ]

    if gate_decision:
        lines += [
            "",
            "--- GATE 8 HOLDOUT EVALUATION ---",
            f"  Decision:             {'PASS' if gate_decision.passed else 'FAIL'}",
            f"  Holdout Sharpe:       {gate_decision.sharpe:>8.4f}",
            f"  Benchmark Sharpe:     {gate_decision.benchmark_sharpe:>8.4f}",
            f"  Sharpe vs Benchmark:  {gate_decision.sharpe_vs_benchmark:>+7.2f}%",
            f"  GR-5:                 {'PASS' if gate_decision.gr5_pass else 'FAIL'}",
            f"  GR-15:                {'PASS' if gate_decision.gr15_pass else 'FAIL'}",
        ]
        if gate_decision.failures:
            lines.append(f"  Failures:             {'; '.join(gate_decision.failures)}")

    lines += [
        "",
        "=" * 60,
        "  End of Report",
        "=" * 60,
    ]

    return "\n".join(lines)


def save_report_to_file(report: str, filepath: Path) -> None:
    """Save a text report to file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report)


def generate_and_save_report(
    portfolio: PortfolioTracker,
    gate_decision: Optional[GateDecision] = None,
    output_dir: Optional[Path] = None,
    title: str = "MMXM Strategy Backtest Report",
) -> str:
    """One-call convenience to compute metrics, generate, and save report.

    Args:
        portfolio: Completed PortfolioTracker with trade history.
        gate_decision: Optional Gate 8 evaluation.
        output_dir: Directory to save report (default: backtesting/outputs/).
        title: Report title.

    Returns:
        Report text.
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent / "outputs"

    metrics = compute_metrics(portfolio)
    report = generate_text_report(metrics, gate_decision, title)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = output_dir / f"backtest_report_{timestamp}.txt"
    save_report_to_file(report, filepath)

    logger.info("Report saved to: %s", filepath)
    return report
