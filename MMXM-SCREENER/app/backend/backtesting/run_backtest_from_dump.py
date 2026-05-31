"""Run backtest using pre-recorded MMXM signals from a MongoDB dump.

The dump at mongodump/msmm_screener contains:
  - 4,500+ already-detected MMXM signals (signals.bson)
  - 503 market snapshots with ticker data (market_snapshots.bson)

This script:
  1. Reads the BSON dump directly (no MongoDB needed)
  2. Fetches OHLCV candle data from Bybit API for each unique symbol
  3. Matches each signal to the correct bar index by timestamp
  4. Wraps signals as a pre-recorded detector function
  5. Runs the full backtest pipeline: backtest → CPCV → walk-forward → Gate 8 → report
"""
import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
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
from backtesting.validation.walk_forward import run_walk_forward
from backtesting.gates.gate8_holdout import run_holdout_evaluation

DUMP_DIR = Path(os.environ.get("MONGODUMP_DIR", r"C:\Users\Lenovo\Desktop\Nueva carpeta\mongodump\mongodump\msmm_screener"))
BYBIT_BASE = os.environ.get("BYBIT_API_BASE", "https://api.bybit.com")
INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}

def load_signals(dump_dir: Path = DUMP_DIR) -> List[dict]:
    """Decode signals.bson into a list of signal dicts."""
    from bson import decode_all
    path = dump_dir / "signals.bson"
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)
    with open(path, "rb") as f:
        docs = decode_all(f.read())
    print(f"  Loaded {len(docs)} signals from dump")
    return docs

def filter_signals(signals: List[dict],
                   min_confidence: int = 3,
                   top_n_symbols: int = 20,
                   timeframes: Optional[List[str]] = None) -> List[dict]:
    """Keep only valid, high-confidence signals for active symbols."""
    valid = [s for s in signals if s.get("entry") and float(s["entry"]) > 0 and s.get("timeframe")]

    # Filter by timeframe
    if timeframes:
        valid = [s for s in valid if s.get("timeframe") in timeframes]

    # Filter by confidence
    valid = [s for s in valid if s.get("confidence") and int(s["confidence"]) >= min_confidence]

    # Keep only top N symbols by signal count
    sym_counts = Counter(s.get("symbol") for s in valid)
    top_symbols = set(sym for sym, _ in sym_counts.most_common(top_n_symbols))
    valid = [s for s in valid if s.get("symbol") in top_symbols]

    print(f"  Filtered to {len(valid)} signals "
          f"(confidence>={min_confidence}, top {top_n_symbols} symbols)")
    return valid

BYBIT_INTERVAL = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}

def fetch_ohlcv(symbol: str, timeframe: str,
                start_ms: int, end_ms: int) -> List[List[float]]:
    """Fetch OHLCV bars from Bybit v5 API directly, return [t,o,h,l,c,v] list."""
    import httpx

    interval = BYBIT_INTERVAL.get(timeframe, "60")
    chunk_size = 200
    all_bars: List[List[float]] = []
    cursor = start_ms

    interval_ms = INTERVAL_MS.get(timeframe, 3_600_000)

    while cursor < end_ms:
        try:
            chunk = _fetch_ohlcv_chunk(symbol, interval, cursor, chunk_size)
        except Exception as e:
            logger.warning("fetch failed for %s @ %d: %s", symbol, cursor, e)
            break
        if not chunk:
            break
        all_bars.extend(chunk)
        last_ts = chunk[-1][0]
        if last_ts <= cursor:
            break
        cursor = last_ts + interval_ms

    return all_bars

def _fetch_ohlcv_chunk(symbol: str, interval: str, cursor_ms: int, chunk_size: int) -> List[List[float]]:
    """Fetch one chunk of OHLCV from Bybit v5 API."""
    import httpx
    url = f"{BYBIT_BASE}/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "start": str(cursor_ms),
        "limit": str(chunk_size),
    }
    with httpx.Client(timeout=30) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        result = r.json().get("result", {})
        raw = result.get("list", [])
    out = []
    for row in reversed(raw):
        out.append([
            int(row[0]),
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
        ])
    return out

def fetch_all_ohlcv(signals: List[dict]) -> Dict[str, Dict[str, List[List[float]]]]:
    """Fetch OHLCV for all unique (symbol, timeframe) pairs in signals.

    Returns:
        {symbol: {timeframe: bars}}
    """
    pairs = set()
    for s in signals:
        sym = s["symbol"]
        tf = s["timeframe"]
        if tf in ("1h", "4h"):
            pairs.add((sym, tf))

    print(f"  Fetching OHLCV for {len(pairs)} (symbol, timeframe) pairs...")
    result: Dict[str, Dict[str, List]] = defaultdict(dict)
    total = len(pairs)

    for i, (sym, tf) in enumerate(sorted(pairs)):
        print(f"    [{i+1}/{total}] {sym} {tf}", end="")
        sys.stdout.flush()

        # Determine date range: 2026-05-01 to 2026-05-30
        start_ms = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime(2026, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)

        bars = fetch_ohlcv(sym, tf, start_ms, end_ms)
        if bars:
            result[sym][tf] = bars
            print(f"  -> {len(bars)} bars")
        else:
            print(f"  -> 0 bars, skipped")

    return dict(result)

def match_signals_to_bars(
    signals: List[dict],
    ohlcv_data: Dict[str, Dict[str, List[List[float]]]],
) -> Dict[str, Dict[str, List[Tuple[int, dict]]]]:
    """Match each signal to its bar index in the OHLCV data.

    A signal is matched to the bar whose timestamp <= signal_time.
    Returns {symbol: {timeframe: [(bar_idx, signal_dict), ...]}}
    """
    matched: Dict[str, Dict[str, List[Tuple[int, dict]]]] = defaultdict(lambda: defaultdict(list))

    for s in signals:
        sym = s["symbol"]
        tf = s["timeframe"]
        if sym not in ohlcv_data or tf not in ohlcv_data[sym]:
            continue
        bars = ohlcv_data[sym][tf]

        # Signal timestamp from created_at
        created = s.get("created_at")
        if not created:
            continue
        if isinstance(created, str):
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            signal_ms = int(dt.timestamp() * 1000)
        else:
            signal_ms = int(created.timestamp() * 1000)

        # Find the bar containing this timestamp
        bar_idx = None
        for i in range(len(bars) - 1, -1, -1):  # search from newest
            bar_ts = int(bars[i][0])
            if bar_ts <= signal_ms:
                bar_idx = i
                break

        if bar_idx is not None and bar_idx >= 0:
            matched[sym][tf].append((bar_idx, s))

    return dict(matched)

def make_recorded_detector(
    matched_signals: Dict[str, Dict[str, List[Tuple[int, dict]]]],
) -> callable:
    """Create a detector_fn that emits pre-recorded MMXM signals at the right bar.

    The returned function:
        (symbol, bars) -> List[dict]
    matches the run_backtest() signature.
    """
    # Build a lookup: (symbol, timeframe) -> {bar_idx: [signal_dict, ...]}
    lookup: Dict[str, Dict[int, List[dict]]] = defaultdict(lambda: defaultdict(list))
    for sym, tfs in matched_signals.items():
        for tf, bar_sigs in tfs.items():
            for bar_idx, sig in bar_sigs:
                entry = float(sig["entry"])
                sl = float(sig["stop_loss"])
                tp1 = float(sig.get("take_profit_1", entry * 1.02))
                tp2 = float(sig.get("take_profit_2", entry * 1.04))
                tp3 = float(sig.get("take_profit_3", entry * 1.06))
                side = sig.get("side", "long")
                lookup[sym][bar_idx].append({
                    "signal_bar_index": bar_idx,
                    "entry_price": entry,
                    "stop_loss": sl,
                    "take_profit_levels": [tp1, tp2, tp3],
                    "side": side,
                })

    def detector_fn(symbol: str, bars: list) -> list:
        current_bar = len(bars) - 1
        sym_signals = lookup.get(symbol, {})
        return sym_signals.get(current_bar, [])

    return detector_fn

def make_placeholder_ohlcv(symbol: str, timeframe: str,
                           base_price: float) -> List[List[float]]:
    """Generate synthetic bars around the signal window (May 2026)."""
    import random
    rng = random.Random(abs(hash(f"{symbol}_{timeframe}")))
    interval = INTERVAL_MS.get(timeframe, 3_600_000)
    ms = interval
    start = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2026, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)
    bars = []
    p = base_price
    for ts in range(start, end, ms):
        ret = rng.gauss(0, 0.002)
        o = p
        c = p * (1 + ret)
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.001)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.001)))
        v = rng.uniform(100_000, 10_000_000)
        bars.append([ts, round(o, 2), round(h, 2), round(l, 2), round(c, 2), round(v, 2)])
        p = c
    return bars

# Main

def _parse_args():
    parser = argparse.ArgumentParser(description="Run backtest from MongoDB dump")
    parser.add_argument("--top-symbols", type=int, default=10,
                        help="Limit to top N symbols by signal count")
    parser.add_argument("--min-confidence", type=int, default=3,
                        help="Minimum signal confidence (1-5)")
    parser.add_argument("--timeframes", default="1h,4h",
                        help="Comma-separated timeframes to include")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--order-size", type=float, default=10_000.0)
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--output-dir", default=None,
                        help="Output directory for reports")
    return parser.parse_args()


def _setup_output_dir(args):
    out = Path(args.output_dir) if args.output_dir else (
        Path(__file__).resolve().parent / "outputs" / "dump_run"
    )
    out.mkdir(parents=True, exist_ok=True)
    return out


def _print_banner():
    print("=" * 60)
    print("  MMXM Backtest from MongoDB Dump")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


def _filter_signals(args, all_signals):
    timeframes = [tf.strip() for tf in args.timeframes.split(",")]
    signals = filter_signals(all_signals,
                             min_confidence=args.min_confidence,
                             top_n_symbols=args.top_symbols,
                             timeframes=timeframes)
    return signals, timeframes


def _print_signal_stats(signals):
    tf_counts = Counter(s["timeframe"] for s in signals)
    for tf, c in tf_counts.most_common():
        print(f"    {tf}: {c} signals")
    side_counts = Counter(s["side"] for s in signals)
    print(f"    Long: {side_counts.get('long', 0)}  Short: {side_counts.get('short', 0)}")


def _prepare_ohlcv(signals):
    print("\n--- OHLCV DATA ---")
    ohlcv_data = fetch_all_ohlcv(signals)
    if not ohlcv_data:
        print("  No OHLCV fetched. Fallback to placeholder data.")
        base_prices = {"BTCUSDT": 70000, "ETHUSDT": 3500}
        for sym in set(s["symbol"] for s in signals):
            bp = base_prices.get(sym, 100)
            for tf in ("1h", "4h"):
                ohlcv_data.setdefault(sym, {})[tf] = make_placeholder_ohlcv(sym, tf, bp)
                print(f"    {sym} {tf}: {len(ohlcv_data[sym][tf])} placeholder bars")
    print(f"  Symbols with data: {len(ohlcv_data)}")
    return ohlcv_data


def _match_signals(signals, ohlcv_data):
    print("\n--- MATCHING SIGNALS TO BARS ---")
    matched = match_signals_to_bars(signals, ohlcv_data)
    total_matched = sum(
        len(sigs) for sym_tfs in matched.values() for sigs in sym_tfs.values()
    )
    print(f"  Matched {total_matched} / {len(signals)} signals to bars")
    return matched, total_matched


def _run_per_symbol_backtests(ohlcv_data, matched, detector, args):
    print("\n--- BACKTEST (per symbol, per timeframe) ---")
    for sym, tfs in sorted(ohlcv_data.items()):
        for tf, bars in sorted(tfs.items()):
            sym_matched = matched.get(sym, {})
            sigs_for_pair = sym_matched.get(tf, [])
            if not sigs_for_pair:
                continue
            data = {sym: bars}
            pt = PortfolioTracker(initial_capital=args.capital)
            run_backtest(data=data, detector_fn=detector, portfolio=pt,
                         max_holding_bars=args.max_hold,
                         order_size_usd=args.order_size,
                         max_concurrent=args.max_concurrent)
            mt = compute_metrics(pt)
            print(f"  {sym:12s} {tf:3s}: trades={mt.total_trades:3d}  "
                  f"sharpe={mt.sharpe_ratio:>6.2f}  return={mt.total_return_pct:>6.2f}%  "
                  f"win={mt.win_rate:>5.1f}%  dd={mt.max_drawdown_pct:>5.2f}%")


def _build_combined_data(ohlcv_data, matched):
    combined = {}
    for sym, tfs in ohlcv_data.items():
        sym_matched = matched.get(sym, {})
        best_tf = max(tfs.keys(), key=lambda tf: len(sym_matched.get(tf, [])))
        combined[sym] = tfs[best_tf]
    return combined


def _run_combined_backtest(combined_data, detector, args):
    print("\n--- COMBINED BACKTEST (all symbols together) ---")
    pt = PortfolioTracker(initial_capital=args.capital)
    result = run_backtest(data=combined_data, detector_fn=detector, portfolio=pt,
                          max_holding_bars=args.max_hold,
                          order_size_usd=args.order_size,
                          max_concurrent=args.max_concurrent)
    mt = compute_metrics(pt)
    print(f"  Trades: {mt.total_trades}")
    print(f"  Sharpe: {mt.sharpe_ratio:.3f}")
    print(f"  Return: {mt.total_return_pct:.2f}%")
    print(f"  Win Rate: {mt.win_rate:.1f}%")
    print(f"  Max DD: {mt.max_drawdown_pct:.2f}%")
    return pt, mt


def _record_strategy_params(audit, ohlcv_data, signals, total_matched, args):
    audit.record_strategy_params({
        "source": "mongodump",
        "symbols": list(ohlcv_data.keys()),
        "total_signals": len(signals),
        "matched_signals": total_matched,
        "order_size_usd": args.order_size,
        "max_holding_bars": args.max_hold,
    })


def _run_cpcv(combined_data, detector, audit, args):
    print("\n--- CPCV (GR-5/GR-9) ---")
    if len(combined_data) >= 2 and min(len(b) for b in combined_data.values()) >= 200:
        cpcv = CpcvEngine(n_groups=5, train_groups=2, max_holding_bars=args.max_hold)
        cpcv_result = cpcv.run(combined_data, detector,
                               lambda: PortfolioTracker(args.capital))
        print(f"  Folds: {len(cpcv_result.fold_results)}")
        print(f"  Mean Sharpe: {cpcv_result.sharpe_mean:.3f}")
        print(f"  PBO: {cpcv_result.pbo_estimate:.3f}")
        print(f"  GR-5: {'PASS' if not cpcv_result.gr5_violation else 'VIOLATION'}")
        audit.record_cpcv({
            "sharpe_mean": cpcv_result.sharpe_mean,
            "sharpe_std": cpcv_result.sharpe_std,
            "pbo": cpcv_result.pbo_estimate,
        })
        return cpcv_result
    print("  SKIP: not enough symbols or bars")
    return None


def _run_walk_forward(combined_data, detector, args):
    print("\n--- WALK-FORWARD ---")
    num_bars = min(len(b) for b in combined_data.values()) if combined_data else 0
    if num_bars >= 300:
        wf = run_walk_forward(combined_data, detector,
                              lambda: PortfolioTracker(args.capital),
                              train_size=num_bars // 2, test_size=num_bars // 4)
        print(f"  Windows: {len(wf.windows)}")
        print(f"  Mean Sharpe: {wf.sharpe_mean:.3f}")
        print(f"  Trend: {wf.sharpe_trend:+.4f}")
        return wf, num_bars
    print("  SKIP: < 300 bars")
    return None, num_bars


def _run_gate8(combined_data, detector, num_bars, audit, args):
    print("\n--- GATE 8 HOLDOUT ---")
    if num_bars >= 120:
        holdout_bars = num_bars // 3
        holdout_data = {sym: bars[-holdout_bars:] for sym, bars in combined_data.items()}
        gate = run_holdout_evaluation(holdout_data, detector,
                                      lambda: PortfolioTracker(args.capital))
        print(f"  Decision: {'PASS' if gate.passed else 'FAIL'}")
        print(f"  Sharpe: {gate.sharpe:.3f}")
        print(f"  Benchmark Sharpe: {gate.benchmark_sharpe:.3f}")
        audit.record_gate8({
            "passed": gate.passed,
            "sharpe": gate.sharpe,
            "benchmark": gate.benchmark_sharpe,
            "gr5_pass": gate.gr5_pass,
            "gr15_pass": gate.gr15_pass,
        })
        return gate
    print("  SKIP: < 120 bars")
    return None


def _generate_report(pt_all, gate, ohlcv_data, total_matched, output_dir):
    print("\n--- REPORT ---")
    title = f"MMXM Backtest from Dump ({len(ohlcv_data)} symbols, {total_matched} signals)"
    generate_and_save_report(pt_all, gate_decision=gate,
                             output_dir=output_dir, title=title)


def _print_summary(results: dict):
    print()
    print("=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    print(f"  Total signals (dump):  {results['total_signals']}")
    print(f"  Filtered:              {results['filtered']}")
    print(f"  Matched to bars:       {results['matched']}")
    print(f"  Symbols:               {results['symbols']}")
    mt = results["mt_all"]
    print(f"  Trades (combined):     {mt.total_trades}")
    print(f"  Sharpe:                {mt.sharpe_ratio:.3f}")
    print(f"  Win Rate:              {mt.win_rate:.1f}%")
    print(f"  Max DD:                {mt.max_drawdown_pct:.2f}%")
    if results.get("cpcv_result"):
        print(f"  CPCV Sharpe:           {results['cpcv_result'].sharpe_mean:.3f}")
    if results.get("gate"):
        print(f"  Gate 8:                {'PASS' if results['gate'].passed else 'FAIL'}")
    print(f"  Reports:               {results['output_dir']}/")
    print(f"  Audit:                 {results['audit_path']}")
    print()


def main():
    args = _parse_args()
    output_dir = _setup_output_dir(args)
    audit = AuditTrail(output_dir=output_dir / "audit")
    audit.start_run(notes=f"Backtest from MongoDB dump: top {args.top_symbols} symbols")
    _print_banner()

    all_signals = load_signals()
    signals, timeframes = _filter_signals(args, all_signals)
    if not signals:
        print("  No valid signals after filtering. Exiting.")
        sys.exit(1)
    _print_signal_stats(signals)

    ohlcv_data = _prepare_ohlcv(signals)
    matched, total_matched = _match_signals(signals, ohlcv_data)
    detector = make_recorded_detector(matched)

    _run_per_symbol_backtests(ohlcv_data, matched, detector, args)
    combined_data = _build_combined_data(ohlcv_data, matched)
    pt_all, mt_all = _run_combined_backtest(combined_data, detector, args)
    audit.record_metrics(mt_all)
    _record_strategy_params(audit, ohlcv_data, signals, total_matched, args)

    cpcv_result = _run_cpcv(combined_data, detector, audit, args)
    wf, num_bars = _run_walk_forward(combined_data, detector, args)
    gate = _run_gate8(combined_data, detector, num_bars, audit, args)

    _generate_report(pt_all, gate, ohlcv_data, total_matched, output_dir)
    audit_path = audit.save()
    _print_summary({
        "total_signals": len(all_signals), "filtered": len(signals),
        "matched": total_matched, "symbols": len(combined_data),
        "mt_all": mt_all, "cpcv_result": cpcv_result, "gate": gate,
        "output_dir": output_dir, "audit_path": audit_path,
    })

if __name__ == "__main__":
    main()
