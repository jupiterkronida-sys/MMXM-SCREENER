"""Step 4: Compute Performance Metrics."""
import json, statistics
from collections import defaultdict
from pathlib import Path

OUT_DIR = Path(__file__).parent

def breakdown(subset):
    if not subset:
        return {"count": 0, "wins": 0, "losses": 0, "win_rate_pct": 0,
                "avg_win_usd": 0, "avg_loss_usd": 0, "expectancy_usd": 0, "total_pnl_usd": 0}
    w = [o for o in subset if o["outcome"] == "TP2_HIT"]
    l = [o for o in subset if o["outcome"] == "SL_HIT"]
    wr = len(w) / len(subset) if subset else 0
    aw = statistics.mean(o["net_dollar_pnl"] for o in w) if w else 0
    al = statistics.mean(o["net_dollar_pnl"] for o in l) if l else 0
    return {
        "count": len(subset), "wins": len(w), "losses": len(l),
        "win_rate_pct": round(wr * 100, 1),
        "avg_win_usd": round(aw, 4), "avg_loss_usd": round(al, 4),
        "expectancy_usd": round(wr * aw + (1 - wr) * al, 4),
        "total_pnl_usd": round(sum(o["net_dollar_pnl"] for o in subset), 2),
    }

def sl_loss_dist(subset):
    sl_trades = [o for o in subset if o["outcome"] == "SL_HIT"]
    if not sl_trades:
        return {}
    losses_usd = sorted(o["net_dollar_pnl"] for o in sl_trades)
    sl_pcts = sorted(o["sl_distance_pct"] * 100 for o in sl_trades)
    return {
        "sl_trades": len(sl_trades),
        "min_loss_usd": round(min(losses_usd), 4),
        "median_loss_usd": round(statistics.median(losses_usd), 4),
        "max_loss_usd": round(max(losses_usd), 4),
        "median_sl_pct": round(statistics.median(sl_pcts), 3),
        "max_sl_pct": round(max(sl_pcts), 3),
    }

def main():
    outcomes = json.load(open(OUT_DIR / "signal_outcomes.json"))
    tradeable = [o for o in outcomes if o["outcome"] in ("TP2_HIT", "SL_HIT", "TIMEOUT")]
    wins = [o for o in tradeable if o["outcome"] == "TP2_HIT"]
    losses = [o for o in tradeable if o["outcome"] == "SL_HIT"]
    timeouts = [o for o in tradeable if o["outcome"] == "TIMEOUT"]

    total_trades = len(tradeable)
    win_rate = len(wins) / total_trades if total_trades else 0
    avg_win_usd = statistics.mean([o["net_dollar_pnl"] for o in wins]) if wins else 0
    avg_loss_usd = statistics.mean([o["net_dollar_pnl"] for o in losses]) if losses else 0
    expectancy = win_rate * avg_win_usd + (1 - win_rate) * avg_loss_usd
    total_gross = sum(o["net_dollar_pnl"] for o in wins)
    total_loss = abs(sum(o["net_dollar_pnl"] for o in losses)) if losses else 1
    profit_factor = total_gross / total_loss if total_loss > 0 else float("inf")
    total_pnl = sum(o["net_dollar_pnl"] for o in tradeable)
    total_cost = sum(o["cost_dollars"] for o in tradeable)
    final_equity = tradeable[-1]["equity_after_trade"] if tradeable else 5000.0
    total_return = (final_equity - 5000.0) / 5000.0 * 100

    # Equity curve + drawdown
    eq_curve = [5000.0] + [o["equity_after_trade"] for o in tradeable]
    peak = eq_curve[0]
    max_dd_pct = 0.0
    max_dd_usd = 0.0
    for eq in eq_curve:
        if eq > peak:
            peak = eq
        dd_pct = (peak - eq) / peak * 100
        dd_usd = peak - eq
        if dd_pct > max_dd_pct:
            max_dd_pct, max_dd_usd = dd_pct, dd_usd

    # Consecutive losses
    max_consec_losses = 0
    current_consec = 0
    for o in tradeable:
        if o["net_dollar_pnl"] <= 0:
            current_consec += 1
            if current_consec > max_consec_losses:
                max_consec_losses = current_consec
        else:
            current_consec = 0

    # Sharpe (daily returns)
    daily = defaultdict(float)
    for o in tradeable:
        day = str(o["created_at"])[:10]
        daily[day] += o["net_dollar_pnl"]
    daily_vals = list(daily.values())
    if len(daily_vals) > 1 and statistics.stdev(daily_vals) > 0:
        sharpe = (statistics.mean(daily_vals) / statistics.stdev(daily_vals)) * (365 ** 0.5)
    else:
        sharpe = 0.0

    soft_cap_hits = [o for o in tradeable if o.get("soft_cap_triggered")]

    report = {
        "capital": {
            "initial_equity": 5000.0,
            "final_equity": round(final_equity, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "total_return_pct": round(total_return, 2),
        },
        "trades": {
            "total": total_trades, "wins": len(wins),
            "losses": len(losses), "timeouts": len(timeouts),
            "win_rate_pct": round(win_rate * 100, 1),
            "max_consecutive_losses": max_consec_losses,
        },
        "performance": {
            "avg_win_usd": round(avg_win_usd, 4),
            "avg_loss_usd": round(avg_loss_usd, 4),
            "expectancy_usd": round(expectancy, 4),
            "profit_factor": round(profit_factor, 2),
            "sharpe_annualized": round(sharpe, 2),
        },
        "drawdown": {
            "max_dd_pct": round(max_dd_pct, 2),
            "max_dd_usd": round(max_dd_usd, 2),
        },
        "costs": {
            "total_cost_usd": round(total_cost, 2),
            "cost_pct_of_starting_equity": round(total_cost / 5000 * 100, 2),
        },
        "soft_cap": {
            "warning_count": len(soft_cap_hits),
            "threshold_pct": 10.0,
            "note": "Trades executed despite warning — soft cap only",
        },
        "excluded": {
            "not_entered": sum(1 for o in outcomes if o["outcome"] == "NOT_ENTERED"),
            "no_data": sum(1 for o in outcomes if o["outcome"] == "NO_DATA"),
        },
        "by_confidence": {},
        "by_timeframe": {
            "1h": breakdown([o for o in tradeable if o["timeframe"] == "1h"]),
            "4h": breakdown([o for o in tradeable if o["timeframe"] == "4h"]),
        },
        "by_side": {
            "long": breakdown([o for o in tradeable if o["side"] == "long"]),
            "short": breakdown([o for o in tradeable if o["side"] == "short"]),
        },
    }

    for stars in [3, 4, 5]:
        subset = [o for o in tradeable if o["confidence"] == stars]
        if subset:
            bd = breakdown(subset)
            bd["sl_loss_dist"] = sl_loss_dist(subset)
            bd["avg_position_usd"] = round(
                statistics.mean([o["position_size_usd"] for o in subset]), 2)
            report["by_confidence"][f"{stars}*"] = bd

    json.dump(report, open(OUT_DIR / "performance_report.json", "w"), indent=2)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
