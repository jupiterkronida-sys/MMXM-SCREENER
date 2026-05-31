"""Step 5: Generate Final Report."""
import json
from datetime import datetime
from pathlib import Path

OUT_DIR = Path(__file__).parent

def main():
    r = json.load(open(OUT_DIR / "performance_report.json"))
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    cap = r["capital"]
    tr = r["trades"]
    perf = r["performance"]
    dd = r["drawdown"]
    cost = r["costs"]
    sc = r["soft_cap"]

    lines = [f"""
{'='*70}
  MMXM TP2 SIGNAL BACKTEST REPORT
  Capital Model  : $5,000 initial | 2% equity notional x confidence stars
  Leverage Model : 3*=3x  4*=4x  5*=5x  |  SL loss = actual SL distance
  Period         : 2026-05-11 to 2026-05-30 (19 days)
  Generated      : {ts}
{'='*70}

  CAPITAL SUMMARY
    Starting Equity    : $5,000.00
    Final Equity       : ${cap['final_equity']:>10,.2f}
    Total P&L          : ${cap['total_pnl_usd']:>+10,.2f}  ({cap['total_return_pct']:+.2f}%)
    Max Drawdown       : ${dd['max_dd_usd']:>10,.2f}  ({dd['max_dd_pct']:.2f}%)

  SIGNAL UNIVERSE
    Total MMXM signals        : 1,607
    No data (no OHLCV)        : {r['excluded']['no_data']}
    Not entered (zone miss)   : {r['excluded']['not_entered']}
    Tradeable (entered)       : {tr['total']}

  OUTCOME DISTRIBUTION (tradeable only)
    TP2 Hit  : {tr['wins']:>4}  ({tr['wins']/tr['total']*100:.1f}%)
    SL Hit   : {tr['losses']:>4}  ({tr['losses']/tr['total']*100:.1f}%)
    Timeout  : {tr['timeouts']:>4}  ({tr['timeouts']/tr['total']*100:.1f}%)

  TRADE PERFORMANCE
    Win Rate              : {tr['win_rate_pct']:.1f}%
    Avg Win               : ${perf['avg_win_usd']:>+8.2f}
    Avg Loss              : ${perf['avg_loss_usd']:>+8.2f}
    Expectancy per Trade  : ${perf['expectancy_usd']:>+8.4f}
    Profit Factor         : {perf['profit_factor']:.2f}
    Sharpe Ratio          : {perf['sharpe_annualized']:.2f}
    Max Consec Losses     : {tr['max_consecutive_losses']}

  RISK
    Soft Cap Warnings     : {sc['warning_count']}  (all trades executed)
    Max Drawdown          : {dd['max_dd_pct']:.2f}%
"""]

    # Confidence tier breakdown
    lines.append("  BREAKDOWN BY CONFIDENCE (leverage tier)")
    lines.append(f"  {'Tier':<6} {'Pos($)':>7} {'Trades':>7} {'WR%':>6} "
                 f"{'Expect$':>9} {'TotalP&L$':>11} {'Med SL$':>9}")
    lines.append("  " + "-" * 60)
    for stars in [3, 4, 5]:
        key = f"{stars}*"
        if key not in r["by_confidence"]:
            continue
        bc = r["by_confidence"][key]
        sd = bc.get("sl_loss_dist", {})
        pos = bc.get("avg_position_usd", 0)
        lines.append(
            f"  {key:<6} ${pos:>6.0f}  {bc['count']:>7}  {bc['win_rate_pct']:>5.1f}%"
            f"  ${bc['expectancy_usd']:>+8.4f}  ${bc['total_pnl_usd']:>+10.2f}"
            f"  ${abs(sd.get('median_loss_usd', 0)):>8.4f}")

    lines.append(f"""
  BREAKDOWN BY TIMEFRAME
    1h : {r['by_timeframe']['1h']['count']:>4} trades | {r['by_timeframe']['1h']['win_rate_pct']:.1f}% WR | ${r['by_timeframe']['1h']['expectancy_usd']:>+.4f} expect | ${r['by_timeframe']['1h']['total_pnl_usd']:>+.2f} P&L
    4h : {r['by_timeframe']['4h']['count']:>4} trades | {r['by_timeframe']['4h']['win_rate_pct']:.1f}% WR | ${r['by_timeframe']['4h']['expectancy_usd']:>+.4f} expect | ${r['by_timeframe']['4h']['total_pnl_usd']:>+.2f} P&L

  BREAKDOWN BY SIDE
    Long : {r['by_side']['long']['count']:>4} trades | {r['by_side']['long']['win_rate_pct']:.1f}% WR | ${r['by_side']['long']['expectancy_usd']:>+.4f} expect | ${r['by_side']['long']['total_pnl_usd']:>+.2f} P&L
    Short: {r['by_side']['short']['count']:>4} trades | {r['by_side']['short']['win_rate_pct']:.1f}% WR | ${r['by_side']['short']['expectancy_usd']:>+.4f} expect | ${r['by_side']['short']['total_pnl_usd']:>+.2f} P&L

  EXECUTION COSTS
    Cost per trade   : 0.13% of position notional
    Total cost drag  : ${cost['total_cost_usd']:.2f}  ({cost['cost_pct_of_starting_equity']:.2f}% of starting equity)

  CONCLUSION
    The MMXM TP2 signal strategy with confidence-leveraged position sizing
    lost 66.68% of capital over 19 days across 750 trades. Key observations:

    1. Win rate is critically low at 6.1% — strategy relies on rare big winners
    2. Avg win ($9.58) is only 1.7x avg loss ($5.65) — insufficient to offset 6.1% WR
    3. Higher confidence (5*) performs WORSE (3.8% WR) than 3* (14.6% WR)
       — signal confidence does NOT predict outcome
    4. Max 51 consecutive losses would destroy any trader's psychology
    5. Sharpe ratio of -30.5 confirms extreme underperformance vs risk-free
    6. 4h timeframe significantly worse than 1h

  OUTPUT FILES
    signal_outcomes.json     — per-trade detail with equity_after_trade
    performance_report.json  — full metrics JSON
    soft_cap_warnings.json   — soft cap warning events
{'='*70}
""")

    report_text = "\n".join(lines)
    print(report_text)
    with open(OUT_DIR / "tp2_backtest_report.txt", "w") as f:
        f.write(report_text)
    print("Saved: tp2_backtest_report.txt")

if __name__ == "__main__":
    main()
