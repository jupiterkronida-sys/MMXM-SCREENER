"""Portfolio Tracker — tracks cash, open/closed positions, equity curve, and metrics.

Supports perp-style backtesting with mark-to-market PnL.
"""
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

from backtesting.engine.order_manager import TradeResult

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

@dataclass
class Position:
    """An open position tracking its current state."""
    trade: TradeResult
    open_qty: float          # portion still open (0.0 = fully closed)
    entry_value_usd: float   # total notional at entry

@dataclass
class EquityPoint:
    bar_idx: int
    cash: float
    unrealized_pnl: float
    total_equity: float

class PortfolioTracker:
    def __init__(self, initial_capital: float | None = None, risk_free_rate: float = 0.02):
        cfg = _load_config()
        self.initial_capital = initial_capital or cfg.get("initial_capital", 100_000.0)
        self.cash = self.initial_capital
        self.open_positions: List[Position] = []
        self.closed_trades: List[TradeResult] = []
        self.equity_points: List[EquityPoint] = []
        self.risk_free_rate = risk_free_rate
        self._trade_counter = 0

    # ── Core operations ────────────────────────────────────────────────────

    def open_trade(self, trade: TradeResult, position_size_usd: float = 10_000.0) -> None:
        """Record a new trade, reducing cash by position size."""
        if position_size_usd > self.cash:
            position_size_usd = self.cash
        self._trade_counter += 1
        self.cash -= position_size_usd
        self.open_positions.append(Position(
            trade=trade,
            open_qty=1.0,
            entry_value_usd=position_size_usd,
        ))

    def close_trade(self, trade: TradeResult) -> None:
        """Close a trade: add proceeds to cash, move to closed_trades."""
        pos = self._find_position(trade)
        if pos is None:
            return
        pnl_usd = pos.entry_value_usd * trade.net_pnl_pct
        self.cash += pos.entry_value_usd + pnl_usd
        self.open_positions.remove(pos)
        self.closed_trades.append(trade)

    def close_position_portion(self, fill: dict, base_trade: TradeResult,
                                position_size_usd: float) -> TradeResult | None:
        """Close a portion (scale-out) of a trade. Returns partial TradeResult."""
        pos = self._find_position(base_trade)
        if pos is None:
            return None
        portion = fill["portion"]
        pnl_pct = fill["pnl_pct"]
        portion_value = position_size_usd * portion

        partial = TradeResult(
            symbol=base_trade.symbol,
            entry_bar_index=base_trade.entry_bar_index,
            exit_bar_index=fill["bar_idx"],
            side=base_trade.side,
            entry_price=base_trade.entry_price,
            exit_price=fill["price"],
            gross_pnl_pct=pnl_pct,
            net_pnl_pct=pnl_pct - base_trade.total_cost_pct * portion,
            total_cost_pct=base_trade.total_cost_pct * portion,
            exit_reason=fill["reason"],
            holding_bars=fill["bar_idx"] - base_trade.entry_bar_index + 1,
        )

        pnl_usd = portion_value * pnl_pct
        self.cash += portion_value + pnl_usd
        pos.open_qty -= portion
        self.closed_trades.append(partial)

        if pos.open_qty < 1e-12:
            self.open_positions.remove(pos)

        return partial

    # ── MTM & equity ───────────────────────────────────────────────────────

    def mark_to_market(self, bar_idx: int,
                       current_prices: Dict[str, float]) -> float:
        """Compute unrealized PnL for all open positions at current prices.

        Returns total unrealized PnL in USD.
        """
        unrealized = 0.0
        for pos in self.open_positions:
            sym = pos.trade.symbol
            price = current_prices.get(sym)
            if price is None:
                continue
            entry = pos.trade.entry_price
            side = pos.trade.side
            if side == "long":
                pnl_pct = (price - entry) / entry
            else:
                pnl_pct = (entry - price) / entry
            unrealized += pos.entry_value_usd * pnl_pct * pos.open_qty
        self._record_equity(bar_idx, unrealized)
        return unrealized

    def _record_equity(self, bar_idx: int, unrealized_pnl: float) -> None:
        total = self.cash + unrealized_pnl
        self.equity_points.append(EquityPoint(
            bar_idx=bar_idx,
            cash=self.cash,
            unrealized_pnl=unrealized_pnl,
            total_equity=total,
        ))

    # ── Metrics ────────────────────────────────────────────────────────────

    @property
    def total_return_pct(self) -> float:
        if not self.equity_points:
            return 0.0
        return (self.equity_points[-1].total_equity - self.initial_capital) / self.initial_capital

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_points:
            return 0.0
        peak = self.equity_points[0].total_equity
        mdd = 0.0
        for ep in self.equity_points:
            if ep.total_equity > peak:
                peak = ep.total_equity
            dd = (peak - ep.total_equity) / peak
            if dd > mdd:
                mdd = dd
        return mdd

    @property
    def sharpe_ratio(self) -> float:
        """Annualized Sharpe from equity point returns (assuming 1h bars, ~8760/year)."""
        if len(self.equity_points) < 2:
            return 0.0
        returns = []
        for i in range(1, len(self.equity_points)):
            r = (self.equity_points[i].total_equity - self.equity_points[i - 1].total_equity) / self.equity_points[i - 1].total_equity
            if math.isfinite(r):
                returns.append(r)
        if not returns:
            return 0.0
        avg_ret = sum(returns) / len(returns)
        if len(returns) < 2:
            return 0.0
        variance = sum((r - avg_ret) ** 2 for r in returns) / (len(returns) - 1)
        if variance <= 0:
            return 0.0
        std = math.sqrt(variance)
        rf_per_bar = self.risk_free_rate / 8760.0
        excess = avg_ret - rf_per_bar
        sharpe_bar = excess / std if std > 0 else 0.0
        return sharpe_bar * math.sqrt(8760.0)

    @property
    def win_rate(self) -> float:
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.net_pnl_pct > 0)
        return wins / len(self.closed_trades)

    @property
    def total_trades(self) -> int:
        return len(self.closed_trades)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _find_position(self, trade: TradeResult) -> Position | None:
        for pos in self.open_positions:
            if pos.trade is trade:
                return pos
        return None

    def reset(self) -> None:
        self.cash = self.initial_capital
        self.open_positions.clear()
        self.closed_trades.clear()
        self.equity_points.clear()
        self._trade_counter = 0
