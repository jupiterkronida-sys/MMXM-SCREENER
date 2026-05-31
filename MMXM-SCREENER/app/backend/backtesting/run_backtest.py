"""Convenience script: run a full MMXM backtest pipeline end-to-end.

Usage (from app/backend/):
    python backtesting/run_backtest.py
    python backtesting/run_backtest.py --symbols BTCUSDT,ETHUSDT --timeframe 1h

This script:
  1. Loads snapshot data (or generates synthetic data as fallback)
  2. Wraps the real MMXM detector as a backtest engine detector_fn
  3. Runs bar-by-bar backtest on training period
  4. Runs CPCV cross-validation (GR-5/GR-9)
  5. Runs walk-forward analysis
  6. Runs execution capacity stress test
  7. Evaluates on holdout data (Gate 8)
  8. Generates text report + audit trail
"""
import argparse
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Ensure app/backend is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # app/backend

from backtesting.engine.event_loop import run_backtest
from backtesting.engine.portfolio_tracker import PortfolioTracker
from backtesting.engine.execution_capacity import capacity_stress_test, find_max_position_size
from backtesting.reporting.metrics import compute_metrics
from backtesting.reporting.report_generator import generate_and_save_report
from backtesting.reporting.audit_trail import AuditTrail
from backtesting.validation.cpcv_engine import CpcvEngine
from backtesting.validation.walk_forward import run_walk_forward
from backtesting.gates.gate8_holdout import run_holdout_evaluation

# MMXM Detector Wrapper

def make_mmxm_detector(timeframe: str = "1h"):
    """Wrap the real MMXM detector as a backtest engine detector_fn.

    The returned function:
        (symbol, bars) -> List[dict]
    matches the signature expected by run_backtest().
    """
    from services.mmxm import detect_mmxm

    def detector_fn(symbol: str, bars: List) -> List[dict]:
        if len(bars) < 60:
            return []
        result = detect_mmxm(bars, symbol, timeframe)
        if result is None:
            return []
        mss_time = result["mss_time"]
        signal_bar = next(
            (i for i, c in enumerate(bars) if int(c[0]) == mss_time),
            len(bars) - 1,
        )
        return [{
            "signal_bar_index": signal_bar,
            "entry_price": result["entry"],
            "stop_loss": result["stop_loss"],
            "take_profit_levels": [
                result["take_profit_1"],
                result["take_profit_2"],
                result["take_profit_3"],
            ],
            "side": result["side"],
        }]
    return detector_fn

# Synthetic Data Generator (fallback when no real data)

def _synthetic_bars(num_bars: int = 500, start_price: float = 100.0,
                    volatility: float = 0.002, timeframe: str = "1h") -> List:
    """Generate synthetic OHLCV bars for testing the pipeline."""
    import random, math
    rng = random.Random(42)
    interval_ms = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
    ms = interval_ms.get(timeframe, 3_600_000)
    base = int(datetime.now().timestamp() * 1000) - num_bars * ms
    bars = []
    p = start_price
    for i in range(num_bars):
        ret = rng.gauss(0, volatility)
        o = p
        c = p * (1 + ret)
        h = max(o, c) * (1 + abs(rng.gauss(0, volatility * 0.5)))
        l = min(o, c) * (1 - abs(rng.gauss(0, volatility * 0.5)))
        v = rng.uniform(100_000, 10_000_000)
        bars.append([base + i * ms, round(o, 2), round(h, 2),
                     round(l, 2), round(c, 2), round(v, 2)])
        p = c
    return bars

# Main Pipeline

def run_pipeline(
    symbols: Optional[List[str]] = None,
    timeframe: str = "1h",
    synthetic: bool = False,
    num_bars: int = 1000,
    initial_capital: float = 100_000.0,
    order_size_usd: float = 10_000.0,
    max_holding_bars: int = 20,
    max_concurrent: int = 5,
    output_dir: Optional[Path] = None,
) -> None:
    """Execute the full MMXM backtest pipeline.

    Args:
        symbols: List of trading pairs (default from config).
        timeframe: Candle interval ("15m", "1h", "4h", "1d").
        synthetic: If True, use synthetic data instead of loading snapshots.
        num_bars: Number of bars (used for synthetic data).
        initial_capital: Starting capital.
        order_size_usd: Notional per trade.
        max_holding_bars: Max bars to hold a position.
        max_concurrent: Max simultaneous open positions per symbol.
        output_dir: Report output directory.
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    audit = AuditTrail(output_dir=output_dir / "audit")
    audit.start_run(notes=f"Full pipeline: timeframe={timeframe}, synthetic={synthetic}")

    print("=" * 60)
    print("  MMXM Backtest Pipeline")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Timeframe: {timeframe}  |  Synthetic: {synthetic}")
    print("=" * 60)

    print("\n--- DATA ---")
    print(f"  Generating{' synthetic' if synthetic else ''} data: {num_bars} bars, {symbols or ['BTCUSDT', 'ETHUSDT']}")
    data = {}
    for sym in (symbols or ["BTCUSDT", "ETHUSDT"]):
        data[sym] = _synthetic_bars(num_bars, start_price=100.0 if sym == "BTCUSDT" else 50.0, timeframe=timeframe)

    num_bars = min(len(b) for b in data.values())
    print(f"  Symbols: {len(data)}  |  Bars: {num_bars}")

    print("\n--- DETECTOR ---")
    mmxm_detector = make_mmxm_detector(timeframe=timeframe)
    print("  MMXM detector initialized")

    print("\n--- TRAINING BACKTEST ---")
    train_bars = int(num_bars * 0.7)
    train_data = {sym: bars[:train_bars] for sym, bars in data.items()}

    pt_train = PortfolioTracker(initial_capital=initial_capital)
    train_result = run_backtest(
        data=train_data,
        detector_fn=mmxm_detector,
        portfolio=pt_train,
        max_holding_bars=max_holding_bars,
        order_size_usd=order_size_usd,
        max_concurrent=max_concurrent,
    )
    metrics_train = compute_metrics(pt_train)
    print(f"  Trades: {metrics_train.total_trades}")
    print(f"  Sharpe: {metrics_train.sharpe_ratio:.3f}")
    print(f"  Return: {metrics_train.total_return_pct:.2f}%")
    print(f"  Max DD: {metrics_train.max_drawdown_pct:.2f}%")

    if metrics_train.total_trades == 0:
        print("  WARNING: 0 trades generated. Synthetic random data may not trigger")
        print("           MMXM signals. Try --bars 2000 for more data, or use")
        print("           real market data by connecting to MongoDB.")

    audit.record_strategy_params({
        "timeframe": timeframe,
        "max_holding_bars": max_holding_bars,
        "order_size_usd": order_size_usd,
        "max_concurrent": max_concurrent,
        "train_bars": train_bars,
    })
    audit.record_metrics(metrics_train)

    print("\n--- CPCV VALIDATION ---")
    cpcv = CpcvEngine(n_groups=5, train_groups=2, max_holding_bars=max_holding_bars)
    cpcv_result = cpcv.run(
        train_data,
        mmxm_detector,
        lambda: PortfolioTracker(initial_capital),
    )
    print(f"  Folds: {len(cpcv_result.fold_results)}")
    print(f"  Mean Sharpe: {cpcv_result.sharpe_mean:.3f}")
    print(f"  Sharpe Std:  {cpcv_result.sharpe_std:.3f}")
    print(f"  GR-5: {'VIOLATION' if cpcv_result.gr5_violation else 'OK'}")
    print(f"  GR-9: {'VIOLATION' if cpcv_result.gr9_violation else 'OK'}")
    print(f"  PBO:  {cpcv_result.pbo_estimate:.3f}")

    cpcv_dict = {
        "n_groups": cpcv.n_groups,
        "train_groups": cpcv.train_groups,
        "sharpe_mean": cpcv_result.sharpe_mean,
        "sharpe_std": cpcv_result.sharpe_std,
        "pbo": cpcv_result.pbo_estimate,
        "gr5_violation": cpcv_result.gr5_violation,
        "gr9_violation": cpcv_result.gr9_violation,
    }
    audit.record_cpcv(cpcv_dict)

    print("\n--- WALK-FORWARD ANALYSIS ---")
    wf_result = run_walk_forward(
        train_data,
        mmxm_detector,
        lambda: PortfolioTracker(initial_capital),
        train_size=min(300, train_bars // 3),
        test_size=min(100, train_bars // 10),
        expanding=True,
    )
    print(f"  Windows: {len(wf_result.windows)}")
    print(f"  Mean Sharpe: {wf_result.sharpe_mean:.3f}")
    print(f"  Sharpe Trend: {wf_result.sharpe_trend:.4f}")
    print(f"  Win Rate Avg: {wf_result.win_rate_avg:.1f}%")

    print("\n--- EXECUTION CAPACITY ---")
    symbols_adv = {sym: 1_000_000_000 for sym in data}  # placeholder ADV values
    cap_results = capacity_stress_test(symbols_adv)
    max_pos = find_max_position_size(cap_results)
    for sym, cap in cap_results.items():
        flag = "GR-7 VIOLATION" if cap.gr7_violation else "OK"
        print(f"  {sym}: max_safe=${cap.max_safe_position:>10,.0f}  [{flag}]")
    print(f"  Max position across all: ${max_pos:>10,.0f}")

    print("\n--- GATE 8 HOLDOUT EVALUATION ---")
    holdout_start = train_bars
    holdout_data = {sym: bars[holdout_start:] for sym, bars in data.items()}
    if min(len(b) for b in holdout_data.values()) >= 60:
        gate_decision = run_holdout_evaluation(
            holdout_data,
            mmxm_detector,
            lambda: PortfolioTracker(initial_capital),
        )
        print(f"  Decision: {'PASS' if gate_decision.passed else 'FAIL'}")
        print(f"  Holdout Sharpe: {gate_decision.sharpe:.3f}")
        print(f"  Benchmark Sharpe: {gate_decision.benchmark_sharpe:.3f}")
        print(f"  GR-15: {'PASS' if gate_decision.gr15_pass else 'FAIL'}")

        gate_dict = {
            "passed": gate_decision.passed,
            "sharpe": gate_decision.sharpe,
            "benchmark_sharpe": gate_decision.benchmark_sharpe,
            "gr5_pass": gate_decision.gr5_pass,
            "gr15_pass": gate_decision.gr15_pass,
            "failures": gate_decision.failures,
        }
        audit.record_gate8(gate_dict)
    else:
        print(f"  SKIP: only {min(len(b) for b in holdout_data.values())} holdout bars (< 60)")
        gate_decision = None

    print("\n--- REPORT ---")
    title = f"MMXM Backtest {timeframe} {'(synthetic)' if synthetic else ''}"
    report_text = generate_and_save_report(
        pt_train,
        gate_decision=gate_decision,
        output_dir=output_dir,
        title=title,
    )

    audit_path = audit.save()
    print(f"  Audit trail: {audit_path}")

    print()
    print("=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Training Sharpe:  {metrics_train.sharpe_ratio:.3f}")
    print(f"  CPCV Mean Sharpe: {cpcv_result.sharpe_mean:.3f}")
    print(f"  Walk-Forward:     {wf_result.sharpe_mean:.3f} avg ({wf_result.sharpe_trend:+.3f} trend)")
    if gate_decision:
        print(f"  Gate 8:           {'PASS' if gate_decision.passed else 'FAIL'}")
    print(f"  Report:           {output_dir}/")
    print(f"  Audit:            {audit_path}")
    print()

def main():
    parser = argparse.ArgumentParser(description="Run MMXM backtest pipeline")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT",
                        help="Comma-separated symbol list")
    parser.add_argument("--timeframe", default="1h", choices=["15m", "1h", "4h", "1d"])
    parser.add_argument("--synthetic", action="store_true", default=True,
                        help="Use synthetic data (default: True)")
    parser.add_argument("--bars", type=int, default=1000,
                        help="Number of bars per symbol")
    parser.add_argument("--capital", type=float, default=100_000.0,
                        help="Initial capital")
    parser.add_argument("--order-size", type=float, default=10_000.0,
                        help="Order size per trade (USD)")
    parser.add_argument("--max-hold", type=int, default=20,
                        help="Max holding bars")
    parser.add_argument("--max-concurrent", type=int, default=5,
                        help="Max concurrent positions per symbol")
    args = parser.parse_args()

    run_pipeline(
        symbols=args.symbols.split(","),
        timeframe=args.timeframe,
        synthetic=args.synthetic,
        num_bars=args.bars,
        initial_capital=args.capital,
        order_size_usd=args.order_size,
        max_holding_bars=args.max_hold,
        max_concurrent=args.max_concurrent,
    )

if __name__ == "__main__":
    main()
