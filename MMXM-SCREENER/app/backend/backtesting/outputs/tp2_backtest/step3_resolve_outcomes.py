"""Step 3: Resolve Outcomes + Apply Confidence-Leveraged Position Sizing.
Each signal: 2% equity × confidence_stars notional, full close at TP2,
SL loss = actual SL distance. Compounding equity from $5,000.
"""
import json
from datetime import datetime
from pathlib import Path

OUT_DIR = Path(__file__).parent

BAR_MS = {"1h": 3_600_000, "4h": 14_400_000}
MAX_HOLD = {"1h": 48, "4h": 12}
COST_PCT = 0.0013
SOFT_CAP_PCT = 0.10

def main():
    signals = json.load(open(OUT_DIR / "signals_validated.json"))
    ohlcv = json.load(open(OUT_DIR / "ohlcv_cache.json"))
    missing_keys = {m["key"] for m in json.load(open(OUT_DIR / "missing_symbols.json"))}

    equity = 5000.0
    outcomes = []
    trade_n = 0
    warnings = []
    entered_count = 0
    not_entered_count = 0
    no_data_count = 0

    for sig in signals:
        key = f"{sig['symbol']}_{sig['timeframe']}"
        side = sig["side"].lower()
        entry = sig["entry"]
        sl = sig["stop_loss"]
        tp2 = sig["take_profit_2"]
        ez_low = sig["entry_zone_low"]
        ez_high = sig["entry_zone_high"]
        sl_pct = sig["sl_distance_pct"]
        tp2_pct = sig["tp2_distance_pct"]
        confidence = sig["confidence"]
        max_hold = MAX_HOLD.get(sig["timeframe"], 48)

        base = {
            "signal_id": str(sig.get("id", sig.get("_id", ""))),
            "symbol": sig["symbol"],
            "timeframe": sig["timeframe"],
            "side": side,
            "confidence": confidence,
            "created_at": str(sig["created_at"]),
            "entry": entry,
            "stop_loss": sl,
            "take_profit_2": tp2,
            "entry_zone_low": ez_low,
            "entry_zone_high": ez_high,
            "sl_distance_pct": sl_pct,
            "tp2_distance_pct": tp2_pct,
            "rr_ratio": sig["rr_ratio"],
        }

        if key in missing_keys or key not in ohlcv:
            no_data_count += 1
            outcomes.append({**base, "outcome": "NO_DATA", "equity_after_trade": equity})
            continue

        bars = ohlcv[key]
        created_ms = int(datetime.fromisoformat(str(sig["created_at"]).replace("Z", "+00:00")).timestamp() * 1000)

        # Find bars after signal creation
        post_bars = []
        for b in bars:
            if int(b[0]) > created_ms:
                post_bars.append(b)

        if not post_bars:
            no_data_count += 1
            outcomes.append({**base, "outcome": "NO_DATA", "equity_after_trade": equity})
            continue

        entry_bar_idx = None
        entry_bar_data = None
        for i, bar in enumerate(post_bars[:max_hold]):
            low, high = bar[3], bar[2]
            if side == "long" and low <= ez_low:
                entry_bar_idx = i
                entry_bar_data = bar
                break
            if side == "short" and high >= ez_high:
                entry_bar_idx = i
                entry_bar_data = bar
                break

        if entry_bar_idx is None:
            not_entered_count += 1
            outcomes.append({**base, "outcome": "NOT_ENTERED", "equity_after_trade": equity})
            continue

        base_notional = round(equity * 0.02, 2)
        position_size = round(base_notional * confidence, 2)
        equity_at_entry = equity
        trade_n += 1
        entered_count += 1

        outcome = None
        exit_bar = None
        exit_price = None
        gross_dollar = 0.0
        holding_bars = 0

        scan_bars = post_bars[entry_bar_idx:entry_bar_idx + max_hold]
        for bar in scan_bars:
            o, h, l, c = bar[1], bar[2], bar[3], bar[4]
            tp2_hit = (side == "long" and h >= tp2) or (side == "short" and l <= tp2)
            sl_hit = (side == "long" and l <= sl) or (side == "short" and h >= sl)

            if sl_hit:
                outcome = "SL_HIT"
                exit_price = sl
                gross_dollar = -(position_size * sl_pct)
                exit_bar = bar
                holding_bars = scan_bars.index(bar) + 1
                break
            if tp2_hit:
                outcome = "TP2_HIT"
                exit_price = tp2
                gross_dollar = position_size * tp2_pct
                exit_bar = bar
                holding_bars = scan_bars.index(bar) + 1
                break

        if outcome is None:
            last_bar = scan_bars[-1]
            exit_price = float(last_bar[4])
            if side == "long":
                move_pct = (exit_price - entry) / entry
            else:
                move_pct = (entry - exit_price) / entry
            gross_dollar = position_size * move_pct
            outcome = "TIMEOUT"
            exit_bar = last_bar
            holding_bars = len(scan_bars)

        cost_dollars = round(position_size * COST_PCT, 4)
        net_dollar = round(gross_dollar - cost_dollars, 4)

        loss_pct_of_equity = abs(net_dollar) / equity_at_entry * 100 if net_dollar < 0 else 0
        soft_cap_triggered = loss_pct_of_equity > 10.0
        if soft_cap_triggered:
            warn = (f"[SOFT CAP WARNING — trade {trade_n}: {sig['symbol']} "
                    f"confidence={confidence}* position=${position_size:.2f} "
                    f"loss=${net_dollar:.2f} ({loss_pct_of_equity:.2f}% of equity) "
                    f"SL dist={sl_pct*100:.2f}%]")
            print(warn)
            warnings.append(warn)

        equity += net_dollar
        equity = round(equity, 2)

        outcome_rec = {**base,
            "outcome": outcome,
            "trade_n": trade_n,
            "base_notional": base_notional,
            "position_size_usd": position_size,
            "equity_at_entry": equity_at_entry,
            "entry_bar_time": str(entry_bar_data[0]),
            "exit_bar_time": str(exit_bar[0]) if exit_bar else None,
            "holding_bars": holding_bars,
            "gross_dollar_pnl": round(gross_dollar, 4),
            "cost_dollars": cost_dollars,
            "net_dollar_pnl": net_dollar,
            "loss_pct_of_equity": round(loss_pct_of_equity, 4),
            "soft_cap_triggered": soft_cap_triggered,
            "equity_after_trade": equity,
        }
        outcomes.append(outcome_rec)

        if equity <= 0:
            json.dump(outcomes, open(OUT_DIR / "signal_outcomes.json", "w"), indent=2, default=str)
            json.dump(warnings, open(OUT_DIR / "soft_cap_warnings.json", "w"), indent=2)
            print(f"\n[RUIN at trade {trade_n} — {sig['symbol']} {confidence}*, equity=${equity:.2f}]")
            return

    json.dump(outcomes, open(OUT_DIR / "signal_outcomes.json", "w"), indent=2, default=str)
    json.dump(warnings, open(OUT_DIR / "soft_cap_warnings.json", "w"), indent=2)

    tradeable = [o for o in outcomes if o["outcome"] in ("TP2_HIT", "SL_HIT", "TIMEOUT")]
    wins = [o for o in tradeable if o["outcome"] == "TP2_HIT"]
    losses = [o for o in tradeable if o["outcome"] == "SL_HIT"]
    timeouts = [o for o in tradeable if o["outcome"] == "TIMEOUT"]

    print(f"\n{'='*60}")
    print(f"  STEP 3 COMPLETE")
    print(f"{'='*60}")
    print(f"  Total signals:         {len(signals)}")
    print(f"  No data (skipped):     {no_data_count}")
    print(f"  Not entered (zone):    {not_entered_count}")
    print(f"  Entered (traded):      {entered_count}")
    print(f"  TP2 hits:              {len(wins)}")
    print(f"  SL hits:               {len(losses)}")
    print(f"  Timeouts:              {len(timeouts)}")
    print(f"  Final equity:          ${equity:.2f}")
    print(f"  Total P&L:             ${equity - 5000:.2f}")
    print(f"  Return:                {(equity-5000)/5000*100:.2f}%")
    print(f"  Soft cap warnings:     {len(warnings)}")
    print(f"\n  Saved: signal_outcomes.json, soft_cap_warnings.json")

if __name__ == "__main__":
    main()
