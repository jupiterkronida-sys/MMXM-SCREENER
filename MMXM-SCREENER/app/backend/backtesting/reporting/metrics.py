"""Performance metrics computation from a completed PortfolioTracker."""
import math
from dataclasses import dataclass

from backtesting.engine.portfolio_tracker import PortfolioTracker

@dataclass
class MetricsReport:
    """Comprehensive performance metrics."""
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0

    # Risk
    annualized_volatility_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_bars: int = 0
    value_at_risk_95_pct: float = 0.0

    # Risk-adjusted
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Trading stats
    total_trades: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    avg_holding_bars: float = 0.0

    # Capital
    initial_capital: float = 0.0
    final_equity: float = 0.0

def _compute_bar_returns(eq) -> list:
    returns = []
    for i in range(1, len(eq)):
        r = (eq[i].total_equity - eq[i - 1].total_equity) / eq[i - 1].total_equity
        if math.isfinite(r):
            returns.append(r)
    return returns


def _compute_drawdown_stats(eq) -> tuple:
    peak = eq[0].total_equity
    mdd = 0.0
    dd_duration = 0
    max_dd_duration = 0
    for i, ep in enumerate(eq):
        if ep.total_equity > peak:
            peak = ep.total_equity
            dd_duration = 0
        else:
            dd = (peak - ep.total_equity) / peak
            if dd > mdd:
                mdd = dd
            dd_duration += 1
            if dd_duration > max_dd_duration:
                max_dd_duration = dd_duration
    return mdd, max_dd_duration


def _compute_trade_stats(trades) -> tuple:
    wins = [t.net_pnl_pct for t in trades if t.net_pnl_pct > 0]
    losses = [t.net_pnl_pct for t in trades if t.net_pnl_pct <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 1.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
    avg_hold = sum(t.holding_bars for t in trades) / len(trades) if trades else 0.0
    return avg_win, avg_loss, profit_factor, avg_hold


def _compute_annualized_stats(total_return, avg_ret, n, std, downside_std):
    annual_factor = math.sqrt(8760.0)
    if total_return <= -1.0:
        ann_return = -1.0
    else:
        ann_return = (1 + total_return) ** (8760.0 / n) - 1
    ann_vol = std * annual_factor
    sharpe_val = (avg_ret / std) * annual_factor if std > 0 else 0.0
    sortino_val = (avg_ret / downside_std) * annual_factor if downside_std > 0 else 0.0
    return ann_return, ann_vol, sharpe_val, sortino_val


def compute_metrics(pt: PortfolioTracker) -> MetricsReport:
    """Compute comprehensive metrics from a completed PortfolioTracker."""
    if not pt.equity_points:
        return MetricsReport()

    eq = pt.equity_points
    initial = pt.initial_capital
    final = eq[-1].total_equity
    total_return = (final - initial) / initial

    returns = _compute_bar_returns(eq)
    n = len(returns)
    if n < 2:
        return MetricsReport(
            total_return_pct=total_return,
            total_trades=pt.total_trades,
            win_rate=round(pt.win_rate * 100, 2),
            initial_capital=initial,
            final_equity=final,
        )

    avg_ret = sum(returns) / n
    variance = sum((r - avg_ret) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    downside = [r for r in returns if r < 0]
    downside_var = sum(r ** 2 for r in downside) / (n - 1) if downside else 0.0
    downside_std = math.sqrt(downside_var)

    ann_return, ann_vol, sharpe_val, sortino_val = _compute_annualized_stats(
        total_return, avg_ret, n, std, downside_std
    )

    mdd, max_dd_duration = _compute_drawdown_stats(eq)
    sorted_ret = sorted(returns)
    var_95 = sorted_ret[int(n * 0.05)] if n >= 20 else 0.0

    avg_win, avg_loss, profit_factor, avg_hold = _compute_trade_stats(pt.closed_trades)
    calmar_val = ann_return / mdd if mdd > 0 else 0.0

    return MetricsReport(
        total_return_pct=round(total_return * 100, 4),
        annualized_return_pct=round(ann_return * 100, 4),
        annualized_volatility_pct=round(ann_vol * 100, 4),
        max_drawdown_pct=round(mdd * 100, 4),
        max_drawdown_duration_bars=max_dd_duration,
        value_at_risk_95_pct=round(var_95 * 100, 4),
        sharpe_ratio=round(sharpe_val, 4),
        sortino_ratio=round(sortino_val, 4),
        calmar_ratio=round(calmar_val, 2),
        total_trades=pt.total_trades,
        win_rate=round(pt.win_rate * 100, 2),
        avg_win_pct=round(avg_win * 100, 4),
        avg_loss_pct=round(avg_loss * 100, 4),
        profit_factor=round(profit_factor, 4),
        avg_holding_bars=round(avg_hold, 2),
        initial_capital=initial,
        final_equity=round(final, 2),
    )
