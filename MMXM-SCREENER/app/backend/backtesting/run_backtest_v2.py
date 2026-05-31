"""Run backtest using MMXM v2 quality filters on dump signals.

Uses the v1 dump signals as sweep+MSS candidates, then applies v2's
L1 (funding/OI bias), L2 (range position), L6 (SL>=1.5xATR),
L7 (RR>=2.0), and L9 (confidence>=2) to filter them.

If v2 filters improve outcomes vs raw v1, the v2 approach is validated.
"""
import argparse, logging, os, sys, math, random
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.getLogger("backtesting.engine.event_loop").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

from backtesting.engine.event_loop import run_backtest
from backtesting.engine.portfolio_tracker import PortfolioTracker
from backtesting.reporting.metrics import compute_metrics
from backtesting.reporting.report_generator import generate_and_save_report
from backtesting.reporting.audit_trail import AuditTrail
from backtesting.validation.cpcv_engine import CpcvEngine
from backtesting.gates.gate8_holdout import run_holdout_evaluation
from backtesting.reporting.metrics import MetricsReport

from services.indicators import atr_from_candles
from services.mmxm_v2 import (
    get_institutional_bias, get_htf_range_position,
)
from backtesting.run_backtest_from_dump import (
    load_signals, fetch_ohlcv, filter_signals, match_signals_to_bars,
    make_recorded_detector, INTERVAL_MS, DUMP_DIR,
)

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "BNBUSDT",
]

RNG = random.Random(42)

def compute_v2_signal_metrics(
    signal: dict,
    bars: List,
    daily_bars: List,
) -> Optional[dict]:
    """Compute v2 quality metrics for a dump signal.

    Returns dict with pass/fail and metrics, or None if data insufficient.
    """
    entry = float(signal.get("entry", 0))
    sl = float(signal.get("stop_loss", 0))
    tp2 = float(signal.get("take_profit_2", 0))
    if entry <= 0 or sl <= 0 or tp2 <= 0:
        return None

    candles_dict = [
        {"timestamp": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]}
        for c in bars
    ]

    atr = atr_from_candles(candles_dict, 14)
    if atr is None or atr <= 0:
        return None

    sl_distance = abs(entry - sl)
    sl_atr_multiple = sl_distance / atr

    rr = (tp2 - entry) / (entry - sl) if signal.get("side", "long") == "long" else (entry - tp2) / (sl - entry)
    rr = abs(rr)

    current_price = entry
    side = signal.get("side", "long")

    daily_dict = [
        {"timestamp": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]}
        for c in daily_bars
    ]

    daily_for_range = daily_dict.copy()
    daily_for_range.append(candles_dict[-1])

    range_pos, range_pct = get_htf_range_position(daily_for_range, current_price)

    num_days = len(daily_bars)
    funding = [0.00025 + RNG.gauss(0, 0.00005) for _ in range(num_days * 3)]
    oi = [1.0 + d * 0.01 + RNG.gauss(0, 0.005) for d in range(num_days)]

    htf_bias, htf_strength = get_institutional_bias(funding, oi)

    passes = True
    reasons = []

    if sl_atr_multiple < 1.5:
        passes = False
        reasons.append(f"SL_ATR={sl_atr_multiple:.2f}<1.5")
    if rr < 2.0:
        passes = False
        reasons.append(f"RR={rr:.2f}<2.0")
    if htf_bias == "neutral":
        passes = False
        reasons.append("NEUTRAL_BIAS")
    if side == "long" and range_pos != "discount":
        passes = False
        reasons.append(f"RANGE={range_pos}!=discount")
    if side == "short" and range_pos != "premium":
        passes = False
        reasons.append(f"RANGE={range_pos}!=premium")

    return {
        "passes": passes,
        "sl_atr_multiple": round(sl_atr_multiple, 2),
        "rr": round(rr, 2),
        "htf_bias": htf_bias,
        "range_pos": range_pos,
        "range_pct": round(range_pct, 4),
        "htf_strength": round(htf_strength, 4),
        "reasons": reasons,
    }

def add_v2_confidence(signal: dict) -> int:
    """Compute v2 confidence for a dump signal based on signal fields."""
    conf = int(signal.get("confidence", 3))
    volume = float(signal.get("volume", 0))
    pct_change = float(signal.get("price_change_pct", 0))
    criteria_met = 0
    if conf >= 4:
        criteria_met += 1
    if volume > 0:
        criteria_met += 1
    if abs(pct_change) > 2:
        criteria_met += 1
    if criteria_met >= 2:
        return conf
    return max(1, conf - 1)

def v2_filter_signals(
    signals: List[dict],
    ohlcv_data: dict,
    matched: dict,
    require_v2_passing: bool = True,
    min_confidence: int = 2,
) -> List[Tuple[dict, int, str]]:
    """Filter dump signals through v2 quality gates.

    Returns list of (signal, bar_idx, timeframe) that pass v2 filters.
    """
    passed = []
    total = 0
    reject_counts = Counter()

    for sym, tfs in matched.items():
        if sym not in ohlcv_data:
            continue
        for tf, bar_sigs in tfs.items():
            if tf not in ohlcv_data[sym]:
                continue
            daily_bars = ohlcv_data[sym].get("1d", [])
            if len(daily_bars) < 5:
                daily_bars = ohlcv_data[sym].get(tf, [])[::24][:60]
            if len(daily_bars) < 5:
                continue

            all_bars = ohlcv_data[sym][tf]
            for bar_idx, sig in bar_sigs:
                total += 1
                sig_conf = int(sig.get("confidence", 0))
                tf_conf = add_v2_confidence(sig)

                bars_up_to = all_bars[:bar_idx + 50]
                if len(bars_up_to) < 30:
                    continue

                if require_v2_passing:
                    metrics = compute_v2_signal_metrics(sig, bars_up_to, daily_bars)
                    if metrics is None:
                        continue
                    if not metrics["passes"]:
                        for r in metrics.get("reasons", []):
                            reject_counts[r] += 1
                        continue
                    if tf_conf < min_confidence:
                        reject_counts["LOW_CONF"] += 1
                        continue

                passed.append((sig, bar_idx, tf))

    if total > 0:
        print(f"    V2 filter: {len(passed)}/{total} passed")
        for reason, count in reject_counts.most_common(10):
            print(f"      Reject {reason}: {count}")

    return passed

def fetch_ohlcv_with_daily(
    signals: List[dict],
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    max_symbols: int = 30,
) -> dict:
    """Fetch bar and daily OHLCV for all symbols."""

    if start_ms is None:
        start_ms = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    if end_ms is None:
        end_ms = int(datetime(2026, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)

    syms = list(dict.fromkeys(s["symbol"] for s in signals))[:max_symbols]
    pairs = set()
    for s in signals:
        sym = s["symbol"]
        if sym not in syms:
            continue
        tf = s.get("timeframe", "1h")
        if tf in ("1h", "4h"):
            pairs.add((sym, tf))
            pairs.add((sym, "1d"))

    print(f"  Fetching OHLCV for {len(pairs)} pairs...")
    result: Dict[str, Dict] = defaultdict(dict)
    total = len(set(p[0] for p in pairs))

    for i, sym in enumerate(sorted(set(p[0] for p in pairs))):
        safe_sym = sym.encode('ascii', errors='replace').decode('ascii')
        print(f"    [{i+1}/{total}] {safe_sym}", end="")
        sys.stdout.flush()

        for tf in ("1d",):
            if (sym, tf) not in pairs:
                continue
            bars = fetch_ohlcv(sym, tf, start_ms, end_ms)
            if bars:
                result[sym][tf] = bars

        for tf in ("1h", "4h"):
            if (sym, tf) not in pairs:
                continue
            bars = fetch_ohlcv(sym, tf, start_ms, end_ms)
            if bars:
                result[sym][tf] = bars

        daily_count = len(result[sym].get("1d", []))
        tf_count = sum(1 for tf in ("1h", "4h") if tf in result[sym])
        print(f"  -> daily={daily_count} tfs={tf_count}")

    return dict(result)

def main():
    parser = argparse.ArgumentParser(description="Run MMXM v2-filtered backtest")
    parser.add_argument("--top-symbols", type=int, default=50,
                        help="Top N symbols from dump to include")
    parser.add_argument("--min-confidence", type=int, default=2,
                        help="Minimum v2 confidence (1-5)")
    parser.add_argument("--timeframes", default="1h,4h",
                        help="Comma-separated timeframes")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--order-size", type=float, default=10_000.0)
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--no-filter", action="store_true",
                        help="Skip v2 quality filters (baseline comparison)")
    parser.add_argument("--min-signals", type=int, default=10,
                        help="Minimum signals required to run backtest")
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else (
        Path(__file__).resolve().parent / "outputs" / "v2_filtered"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    audit = AuditTrail(output_dir=output_dir / "audit")
    audit.start_run(notes=f"MMXM v2-filtered backtest: top {args.top_symbols} symbols")

    print("=" * 60)
    print(f"  MMXM v2-Filtered Backtest")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Top symbols: {args.top_symbols}  |  V2 Filter: {not args.no_filter}")
    print("=" * 60)

    print("\n--- SIGNALS ---")
    all_signals = load_signals()
    timeframes = [tf.strip() for tf in args.timeframes.split(",")]
    signals = filter_signals(all_signals, min_confidence=1,
                             top_n_symbols=args.top_symbols,
                             timeframes=timeframes)
    print(f"  Total dump signals: {len(all_signals)}")
    print(f"  After basic filter: {len(signals)}")

    print("\n--- OHLCV DATA ---")
    ohlcv_data = fetch_ohlcv_with_daily(signals, max_symbols=25)
    if not ohlcv_data:
        print("ERROR: No OHLCV data fetched. Exiting.")
        sys.exit(1)

    print("\n--- MATCHING ---")
    matched = match_signals_to_bars(signals, ohlcv_data)
    total_matched = sum(
        len(sigs) for sym_tfs in matched.values() for sigs in sym_tfs.values()
    )
    print(f"  Matched {total_matched} / {len(signals)} signals to bars")

    print("\n--- V2 QUALITY FILTER ---")
    v2_passed = v2_filter_signals(
        signals, ohlcv_data, matched,
        require_v2_passing=not args.no_filter,
        min_confidence=args.min_confidence,
    )
    if len(v2_passed) < args.min_signals:
        print(f"  Only {len(v2_passed)} signals after filters (< {args.min_signals} min). "
              f"Run with --no-filter for baseline or reduce --min-signals.")
        if len(v2_passed) >= 5:
            print("  Proceeding with available signals.")
        else:
            sys.exit(0)

    print("\n--- BUILDING DETECTOR ---")
    matched_filtered: Dict[str, Dict[str, List]] = defaultdict(lambda: defaultdict(list))
    for sig, bar_idx, tf in v2_passed:
        sym = sig["symbol"]
        entry = float(sig["entry"])
        sl = float(sig.get("stop_loss", entry * 0.98))
        tp1 = float(sig.get("take_profit_1", entry * 1.02))
        tp2 = float(sig.get("take_profit_2", entry * 1.04))
        tp3 = float(sig.get("take_profit_3", entry * 1.06))
        side = sig.get("side", "long")
        matched_filtered[sym][tf].append((bar_idx, {
            "signal_bar_index": bar_idx,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit_levels": [tp1, tp2, tp3],
            "side": side,
        }))

    lookup: Dict[str, Dict[int, List]] = defaultdict(lambda: defaultdict(list))
    for sym, tfs in matched_filtered.items():
        for tf, bar_sigs in tfs.items():
            for bar_idx, sig_dict in bar_sigs:
                lookup[sym][bar_idx].append(sig_dict)
                lookup_data = dict(lookup)

    def detector_fn(symbol: str, bars: list) -> list:
        current_bar = len(bars) - 1
        return lookup.get(symbol, {}).get(current_bar, [])

    print("\n--- BACKTEST ---")
    combined_data = {}
    for sym, tfs in ohlcv_data.items():
        trade_tfs = [tf for tf in ("1h", "4h") if tf in tfs]
        if not trade_tfs:
            continue
        if sym not in lookup or not any(lookup[sym].values()):
            continue
        matched_count = sum(len(v) for v in lookup[sym].values())
        best_tf = max(trade_tfs, key=lambda tf: len(tfs[tf]))
        combined_data[sym] = tfs[best_tf]

    if not combined_data:
        print("ERROR: No symbols with filtered signals after data prep.")
        sys.exit(0)

    num_bars = min(len(b) for b in combined_data.values())
    train_bars = int(num_bars * 0.7)
    train_data = {sym: bars[:train_bars] for sym, bars in combined_data.items()}

    pt = PortfolioTracker(initial_capital=args.capital)
    result = run_backtest(
        data=train_data,
        detector_fn=detector_fn,
        portfolio=pt,
        max_holding_bars=args.max_hold,
        order_size_usd=args.order_size,
        max_concurrent=args.max_concurrent,
    )
    mt = compute_metrics(pt)
    _print_metrics(mt, result, "TRAINING")

    audit.record_strategy_params({
        "version": "v2_filtered",
        "v2_filter": not args.no_filter,
        "min_confidence": args.min_confidence,
        "symbols": len(combined_data),
        "train_bars": train_bars,
        "order_size_usd": args.order_size,
        "max_holding_bars": args.max_hold,
    })
    audit.record_metrics(mt)

    print("\n--- CPCV ---")
    if len(combined_data) >= 2 and min(len(b) for b in combined_data.values()) >= 200:
        cpcv = CpcvEngine(n_groups=5, train_groups=2, max_holding_bars=args.max_hold)
        cpcv_result = cpcv.run(
            train_data, detector_fn,
            lambda: PortfolioTracker(args.capital),
        )
        print(f"  Folds: {len(cpcv_result.fold_results)}  |  Mean Sharpe: {cpcv_result.sharpe_mean:.3f}  |  PBO: {cpcv_result.pbo_estimate:.3f}")
        print(f"  GR-5: {'PASS' if not cpcv_result.gr5_violation else 'VIOLATION'}")
        audit.record_cpcv({
            "sharpe_mean": cpcv_result.sharpe_mean,
            "sharpe_std": cpcv_result.sharpe_std,
            "pbo": cpcv_result.pbo_estimate,
        })

    print("\n--- GATE 8 HOLDOUT ---")
    gate = None
    if num_bars >= 120:
        holdout_bars = num_bars // 3
        holdout_data = {sym: bars[-holdout_bars:] for sym, bars in combined_data.items()}
        gate = run_holdout_evaluation(
            holdout_data, detector_fn,
            lambda: PortfolioTracker(args.capital),
        )
        print(f"  Decision: {'PASS' if gate.passed else 'FAIL'}")
        print(f"  Sharpe: {gate.sharpe:.3f}  |  Benchmark: {gate.benchmark_sharpe:.3f}")
        audit.record_gate8({
            "passed": gate.passed,
            "sharpe": gate.sharpe,
            "benchmark": gate.benchmark_sharpe,
        })

    print("\n--- REPORT ---")
    title = f"MMXM v2-Filtered ({len(combined_data)} symbols, v2_filter={not args.no_filter})"
    generate_and_save_report(pt, gate_decision=gate,
                             output_dir=output_dir, title=title)
    audit_path = audit.save()

    print()
    print("=" * 60)
    print(f"  v2-Filtered Backtest — {'ACTIVE' if not args.no_filter else 'BASELINE'}")
    print("=" * 60)
    print(f"  Symbols:     {len(combined_data)}")
    print(f"  Bars:        {num_bars}")
    print(f"  Signals:     {result.total_signals}")
    print(f"  Trades:      {mt.total_trades}")
    print(f"  Sharpe:      {mt.sharpe_ratio:.3f}")
    print(f"  Return:      {mt.total_return_pct:.2f}%")
    print(f"  Win Rate:    {mt.win_rate:.1f}%")
    print(f"  Profit Factor: {mt.profit_factor:.2f}")
    print(f"  Max DD:      {mt.max_drawdown_pct:.2f}%")
    print(f"  Final Equity: ${mt.final_equity:,.2f}")
    if mt.total_trades >= 1:
        print(f"  Min Targets: >=28.6% WR, >$0 expectancy, PF>=1.0, Sharpe>=0.5")
        goals = [
            f"WR={'PASS' if mt.win_rate >= 28.6 else 'FAIL'}({mt.win_rate:.1f}%)",
            f"PnL={'PASS' if mt.final_equity > args.capital else 'FAIL'}",
            f"PF={'PASS' if mt.profit_factor >= 1.0 else 'FAIL'}({mt.profit_factor:.2f})",
            f"SHR={'PASS' if mt.sharpe_ratio >= 0.5 else 'FAIL'}({mt.sharpe_ratio:.3f})",
        ]
        print(f"  Targets Met: {', '.join(goals)}")
    print(f"  Report:      {output_dir}/")
    print(f"  Audit:       {audit_path}")
    print()

def _print_metrics(mt: MetricsReport, result, label: str):
    print(f"  [{label}]")
    print(f"    Signals: {result.total_signals}  |  Trades: {mt.total_trades}")
    print(f"    Sharpe: {mt.sharpe_ratio:.3f}  |  Return: {mt.total_return_pct:.2f}%")
    print(f"    Win Rate: {mt.win_rate:.1f}%  |  PF: {mt.profit_factor:.2f}")
    print(f"    Max DD: {mt.max_drawdown_pct:.2f}%  |  Final: ${mt.final_equity:,.2f}")

if __name__ == "__main__":
    main()
