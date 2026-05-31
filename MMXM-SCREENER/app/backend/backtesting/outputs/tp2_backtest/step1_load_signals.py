"""Step 1: Load and Validate All MMXM Signals from BSON dump.
No dedup — each signal is a separate entry attempt at a different bar.
"""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from collections import Counter
from pathlib import Path
from bson import decode_all

DUMP_DIR = Path(r"C:\Users\Lenovo\Desktop\Nueva carpeta\mongodump\mongodump\msmm_screener")
OUT_DIR  = Path(__file__).parent
OUT_DIR.mkdir(parents=True, exist_ok=True)

def load_signals():
    path = DUMP_DIR / "signals.bson"
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)
    with open(path, "rb") as f:
        docs = decode_all(f.read())
    print(f"  Loaded {len(docs)} total documents from dump")
    return docs

def validate(raw_docs):
    valid, invalid = [], []

    for doc in raw_docs:
        reason = None
        try:
            src = str(doc.get("source", ""))
            if src != "mmxm":
                doc["rejection_reason"] = f"source={src}, not mmxm"
                invalid.append({k: str(v) for k, v in doc.items()})
                continue

            if not doc.get("entry") or not str(doc.get("entry")):
                reason = "missing entry"
            elif not doc.get("take_profit_2") or not str(doc.get("take_profit_2")):
                reason = "missing take_profit_2"
            elif not doc.get("stop_loss") or not str(doc.get("stop_loss")):
                reason = "missing stop_loss"

            entry = float(doc["entry"])
            sl    = float(doc["stop_loss"])
            tp2   = float(doc["take_profit_2"])
            side  = str(doc.get("side", "")).lower()

            if side not in ("long", "short"):
                reason = f"invalid side: {side}"

            try:
                confidence = int(float(str(doc.get("confidence", 1))))
                if confidence not in {1, 2, 3, 4, 5}:
                    doc["confidence_raw"] = doc.get("confidence")
                    confidence = 1
            except:
                confidence = 1

            sl_distance_pct  = abs(entry - sl)  / entry if entry else 0
            tp2_distance_pct = abs(tp2 - entry) / entry if entry else 0

            if side == "long" and not (sl < entry < tp2):
                reason = f"long ladder fail: sl={sl} entry={entry} tp2={tp2}"
            elif side == "short" and not (sl > entry > tp2):
                reason = f"short ladder fail: sl={sl} entry={entry} tp2={tp2}"
            elif sl_distance_pct == 0:
                reason = "sl_distance_pct == 0"
            elif sl_distance_pct > 0.50:
                reason = f"sl_distance_pct={sl_distance_pct:.4f} > 0.50"
            elif tp2_distance_pct == 0:
                reason = "tp2_distance_pct == 0"

            if reason:
                doc["rejection_reason"] = reason
                invalid.append({k: str(v) for k, v in doc.items()})
            else:
                doc["entry"]             = entry
                doc["stop_loss"]         = sl
                doc["take_profit_2"]     = tp2
                doc["take_profit_1"]     = float(doc.get("take_profit_1") or 0)
                doc["take_profit_3"]     = float(doc.get("take_profit_3") or 0)
                doc["entry_zone_low"]    = float(doc.get("entry_zone_low") or entry)
                doc["entry_zone_high"]   = float(doc.get("entry_zone_high") or entry)
                doc["confidence"]        = confidence
                doc["sl_distance_pct"]   = sl_distance_pct
                doc["tp2_distance_pct"]  = tp2_distance_pct
                doc["rr_ratio"]          = tp2_distance_pct / sl_distance_pct if sl_distance_pct > 0 else 0
                doc["_id"]               = str(doc["_id"])
                valid.append(doc)

        except Exception as e:
            doc["rejection_reason"] = str(e)
            invalid.append({k: str(v) for k, v in doc.items()})

    return valid, invalid

def print_stats(valid, invalid, raw_count):
    fail_pct = len(invalid) / raw_count * 100 if raw_count else 0
    print(f"\nTotal raw: {raw_count}")
    print(f"Valid: {len(valid)}")
    print(f"Invalid: {len(invalid)} ({fail_pct:.1f}%)")

    if valid:
        print(f"\nTimeframes: {dict(Counter(s['timeframe'] for s in valid))}")
        print(f"Sides: {dict(Counter(s['side'] for s in valid))}")
        print(f"Confidence dist: {dict(Counter(s['confidence'] for s in valid))}")
        print(f"Symbols: {len(set(s['symbol'] for s in valid))}")
        print(f"Date range: {min(s.get('created_at','') for s in valid)} to {max(s.get('created_at','') for s in valid)}")

        sl_pcts = sorted(s['sl_distance_pct']*100 for s in valid)
        print(f"\nSL Distance % dist:")
        print(f"  Min: {min(sl_pcts):.3f}% | P10: {sl_pcts[len(sl_pcts)//10]:.3f}% | Median: {sl_pcts[len(sl_pcts)//2]:.3f}% | P90: {sl_pcts[9*len(sl_pcts)//10]:.3f}% | Max: {max(sl_pcts):.3f}%")

        tp2_pcts = sorted(s['tp2_distance_pct']*100 for s in valid)
        print(f"TP2 Distance % dist:")
        print(f"  Min: {min(tp2_pcts):.3f}% | Median: {tp2_pcts[len(tp2_pcts)//2]:.3f}% | Max: {max(tp2_pcts):.3f}%")

    if invalid:
        reasons = Counter(s.get("rejection_reason", "unknown")[:80] for s in invalid)
        print(f"\nRejection reasons:")
        for reason, count in reasons.most_common(10):
            print(f"  {reason}: {count}")

    if fail_pct > 5:
        print(f"\n[STEP 1 WARNING: {fail_pct:.1f}% invalid — check signals_invalid.json]")

if __name__ == "__main__":
    raw   = load_signals()
    valid, invalid = validate(raw)
    print_stats(valid, invalid, len(raw))

    with open(OUT_DIR / "signals_validated.json", "w") as f:
        json.dump(valid, f, default=str, indent=2)
    with open(OUT_DIR / "signals_invalid.json", "w") as f:
        json.dump(invalid, f, default=str, indent=2)
    print(f"\nSaved: signals_validated.json ({len(valid)}), signals_invalid.json ({len(invalid)})")
