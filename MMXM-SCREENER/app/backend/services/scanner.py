"""Orchestrator: pulls top symbols, scans MMXM + pump/dump, persists, alerts."""
import asyncio
import os
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Callable, Awaitable, Optional

from .exchanges import get_top_symbols, get_klines
from .mmxm import detect_mmxm
from .screener import detect_pump_dump
from .telegram import send_alert, send_message

logger = logging.getLogger(__name__)

# Avoid spamming the same setup repeatedly
DEDUPE_WINDOW = timedelta(hours=4)
Publisher = Callable[[str, dict, str], Awaitable[None]]


def _signal_key(sig: dict) -> str:
    if sig.get("source") == "mmxm":
        return f"mmxm:{sig['symbol']}:{sig['timeframe']}:{sig['side']}"
    return f"screener:{sig['symbol']}:{sig['kind']}"


async def _was_recently_alerted(db, key: str) -> bool:
    cutoff = (datetime.now(timezone.utc) - DEDUPE_WINDOW).isoformat()
    doc = await db.signals.find_one(
        {"dedupe_key": key, "created_at": {"$gt": cutoff}}, {"_id": 0, "id": 1}
    )
    return doc is not None


async def _persist_and_alert(db, sig: dict, publisher: Optional[Publisher] = None):
    key = _signal_key(sig)
    if await _was_recently_alerted(db, key):
        return
    sig["id"] = str(uuid.uuid4())
    sig["dedupe_key"] = key
    sig["created_at"] = datetime.now(timezone.utc).isoformat()
    sig["status"] = "active"
    await db.signals.insert_one(sig.copy())
    sent = await send_alert(sig)
    sig["telegram_sent"] = sent
    await db.signals.update_one({"id": sig["id"]}, {"$set": {"telegram_sent": sent}})
    if publisher:
        await publisher("signal:new", sig, topic="signals")


async def _scan_one_symbol(symbol: str) -> List[Dict]:
    out = []
    # Pump / dump on 1h
    try:
        k1h = await get_klines(symbol, "1h", 100)
        if k1h:
            res = detect_pump_dump(k1h, symbol)
            if res:
                res["source"] = "screener"
                out.append(res)
    except Exception as e:
        logger.debug(f"pumpdump {symbol}: {e}")

    # MMXM on 4h (HTF) and 1h (LTF)
    for tf in ("4h", "1h"):
        try:
            kl = await get_klines(symbol, tf, 200)
            if not kl:
                continue
            mm = detect_mmxm(kl, symbol, tf)
            if mm:
                mm["source"] = "mmxm"
                # confidence: 3 base + bonuses
                conf = 3
                if mm["risk_reward_tp2"] >= 2:
                    conf += 1
                if mm["ob_used"] and mm["fvg_used"]:
                    conf += 1
                mm["confidence"] = min(conf, 5)
                out.append(mm)
        except Exception as e:
            logger.debug(f"mmxm {symbol} {tf}: {e}")
    return out


async def run_scan(db, publisher: Optional[Publisher] = None) -> Dict:
    started = datetime.now(timezone.utc)
    top_n = int(os.environ.get("TOP_N_SYMBOLS", 60))
    if publisher:
        await publisher("scan:started", {"started_at": started.isoformat(), "top_n": top_n}, topic="scanner")
    try:
        tickers = await get_top_symbols(top_n)
    except Exception as e:
        logger.error(f"top symbols fetch failed: {e}")
        return {"ok": False, "error": str(e)}

    # update universe snapshot
    snapshot = {
        "id": str(uuid.uuid4()),
        "captured_at": started.isoformat(),
        "tickers": tickers,
    }
    await db.market_snapshots.insert_one(snapshot.copy())
    if publisher:
        await publisher(
            "market:snapshot",
            {"captured_at": snapshot["captured_at"], "tickers": tickers},
            topic="market",
        )
    # keep last 50 snapshots only
    await db.market_snapshots.delete_many({"captured_at": {"$lt": (started - timedelta(days=2)).isoformat()}})

    new_signals = 0
    # scan in chunks to be polite to APIs
    chunk = 6
    symbols = [t["symbol"] for t in tickers]
    for i in range(0, len(symbols), chunk):
        batch = symbols[i : i + chunk]
        results = await asyncio.gather(*[_scan_one_symbol(s) for s in batch], return_exceptions=True)
        for sigs in results:
            if isinstance(sigs, Exception):
                continue
            for sig in sigs:
                before = await db.signals.count_documents({"dedupe_key": _signal_key(sig)})
                await _persist_and_alert(db, sig, publisher=publisher)
                after = await db.signals.count_documents({"dedupe_key": _signal_key(sig)})
                if after > before:
                    new_signals += 1
        await asyncio.sleep(0.4)

    finished = datetime.now(timezone.utc)
    summary = {
        "ok": True,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "symbols_scanned": len(symbols),
        "new_signals": new_signals,
    }
    await db.scan_runs.insert_one({**summary, "id": str(uuid.uuid4())})
    if publisher:
        await publisher("scan:completed", summary, topic="scanner")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        stats = {
            "total_7d": await db.signals.count_documents({"created_at": {"$gte": cutoff}}),
            "longs_7d": await db.signals.count_documents({"created_at": {"$gte": cutoff}, "side": "long"}),
            "shorts_7d": await db.signals.count_documents({"created_at": {"$gte": cutoff}, "side": "short"}),
            "mmxm_7d": await db.signals.count_documents({"created_at": {"$gte": cutoff}, "source": "mmxm"}),
            "screener_7d": await db.signals.count_documents({"created_at": {"$gte": cutoff}, "source": "screener"}),
        }
        await publisher("stats:update", stats, topic="stats")
        health = {
            "ok": True,
            "last_scan": summary,
            "total_signals": await db.signals.count_documents({}),
            "scan_interval_seconds": int(os.environ.get("SCAN_INTERVAL_SECONDS", 300)),
            "top_n": int(os.environ.get("TOP_N_SYMBOLS", 60)),
            "telegram_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
            "cmc_configured": bool(os.environ.get("CMC_API_KEY")),
        }
        await publisher("health:update", health, topic="health")
    return summary


async def scanner_loop(db, publisher: Optional[Publisher] = None):
    interval = int(os.environ.get("SCAN_INTERVAL_SECONDS", 300))
    # On boot: send a small ping so user knows alerts work
    await send_message("✅ <b>MMXM Crypto Screener online</b>\nScanning Bybit perpetuals every "
                       f"{interval//60} min. Alerts will arrive here.")
    while True:
        try:
            res = await run_scan(db, publisher=publisher)
            logger.info(f"scan done: {res}")
        except Exception as e:
            logger.exception(f"scan loop error: {e}")
        await asyncio.sleep(interval)
