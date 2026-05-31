"""Step 2: Fetch OHLCV for all signal symbols from Bybit API.
Concurrency-limited with cooldowns to avoid 429 rate limits.
Skips empty/no-data pairs after retries.
"""
import json, os, sys, time, asyncio
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import httpx

OUT_DIR = Path(__file__).parent
BYBIT_BASE = os.environ.get("BYBIT_API_BASE", "https://api.bybit.com")
BAR_MS = {"1h": 3_600_000, "4h": 14_400_000}
MAX_HOLD = {"1h": 48, "4h": 12}
KNOWN_NO_DATA = {
    "TSLAXUSDT", "NVDAXUSDT", "GOOGLXUSDT", "MSTRXUSDT", "COINXUSDT",
    "QQQXUSDT", "SPYXUSDT", "TQQQXUSDT", "PLTRXUSDT", "CRCLXUSDT", "SOXLUSDT",
}

# Concurrency control
SEM = asyncio.Semaphore(5)  # max 5 concurrent requests
COOLDOWN_AFTER = 20  # cooldown every N requests
cooldown_counter = 0

def _parse_kline_rows(rows):
    out = []
    for row in rows:
        out.append([int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
    return out

async def fetch_symbol_ohlcv(symbol, interval, start_ms, end_ms):
    interval_code = {"1h": "60", "4h": "240"}[interval]
    bar_ms = BAR_MS[interval]
    all_bars = []
    cursor = start_ms

    async with httpx.AsyncClient(timeout=30) as c:
        while cursor < end_ms:
            url = f"{BYBIT_BASE}/v5/market/kline"
            params = {"category": "linear", "symbol": symbol, "interval": interval_code, "start": str(cursor), "limit": "200"}
            success = False
            for _ in range(3):
                try:
                    r = await c.get(url, params=params)
                    if r.status_code == 429:
                        await asyncio.sleep(5)
                        continue
                    r.raise_for_status()
                    result = r.json().get("result", {})
                    raw = result.get("list", [])
                    if not raw:
                        return all_bars
                    chunk = _parse_kline_rows(reversed(raw))
                    all_bars.extend(chunk)
                    last_ts = chunk[-1][0]
                    if last_ts <= cursor:
                        return all_bars
                    cursor = last_ts + bar_ms
                    success = True
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        await asyncio.sleep(3)
                        continue
                    return all_bars  # other HTTP errors -> return what we have
                except Exception:
                    await asyncio.sleep(2)
            if not success:
                break
    return all_bars

async def fetch_one(key, plan):
    global cooldown_counter
    async with SEM:
        cooldown_counter += 1
        if cooldown_counter % COOLDOWN_AFTER == 0:
            await asyncio.sleep(2)  # cooldown every 20 requests

        sym = plan["symbol"]
        interval = plan["interval"]

        if sym in KNOWN_NO_DATA:
            return key, [], "known_no_data_tokenized_stock"

        try:
            bars = await fetch_symbol_ohlcv(sym, interval, int(plan["start_ts"]), int(plan["end_ts"]))
            if bars and len(bars) >= 5:
                return key, bars, None
            else:
                return key, [], f"insufficient_bars:{len(bars) if bars else 0}"
        except Exception as e:
            return key, [], str(e)[:80]

async def main():
    signals = json.load(open(OUT_DIR / "signals_validated.json"))

    fetch_plan = defaultdict(lambda: {"start_ts": float("inf"), "end_ts": 0, "symbol": "", "interval": ""})
    for s in signals:
        key = f"{s['symbol']}_{s['timeframe']}"
        created_ms = int(datetime.fromisoformat(str(s["created_at"]).replace("Z", "+00:00")).timestamp() * 1000)
        max_bars = MAX_HOLD.get(s["timeframe"], 48)
        bar_ms = BAR_MS.get(s["timeframe"], 3_600_000)
        end_ms = created_ms + (max_bars + 10) * bar_ms
        fetch_plan[key]["symbol"] = s["symbol"]
        fetch_plan[key]["interval"] = s["timeframe"]
        fetch_plan[key]["start_ts"] = min(fetch_plan[key]["start_ts"], created_ms)
        fetch_plan[key]["end_ts"] = max(fetch_plan[key]["end_ts"], end_ms)

    items = sorted(fetch_plan.items())
    total = len(items)
    print(f"Fetch plan: {total} unique (symbol, timeframe) pairs\n")

    ohlcv_cache = {}
    missing = []

    for i in range(0, total, 20):
        batch = items[i:i+20]
        tasks = [fetch_one(key, plan) for key, plan in batch]
        results = await asyncio.gather(*tasks)

        for key, bars, error in results:
            if error:
                missing.append({"key": key, "symbol": fetch_plan[key]["symbol"],
                                "interval": fetch_plan[key]["interval"], "error": error})
            else:
                ohlcv_cache[key] = bars

        count = min(i + 20, total)
        done_count = len(ohlcv_cache) + len(missing)
        print(f"  Progress: {count}/{total} pairs | OK={len(ohlcv_cache)} MISS={len(missing)}", flush=True)

        if i + 20 < total:
            await asyncio.sleep(1)  # inter-batch cooldown

    json.dump(ohlcv_cache, open(OUT_DIR / "ohlcv_cache.json", "w"), indent=2, default=str)
    json.dump(missing, open(OUT_DIR / "missing_symbols.json", "w"), indent=2, default=str)
    print(f"\nDone: {len(ohlcv_cache)} fetched | {len(missing)} missing/skipped")
    keys_with_data = {k for k in fetch_plan if k in ohlcv_cache}
    syms_with_data = {fetch_plan[k]["symbol"] for k in keys_with_data}
    print(f"Unique symbols with data: {len(syms_with_data)}")

if __name__ == "__main__":
    asyncio.run(main())
