from fastapi import FastAPI, APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import logging
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from services.scanner import run_scan, scanner_loop
from services.discovery import coingecko_top, coingecko_trending, cmc_metadata
from services.exchanges import get_top_symbols, get_klines
from services.realtime import RealtimeEventHub
from services.bybit_stream import BybitStreamIngestor
from services.telegram import send_message

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{name}'. "
            f"Set it in '{ROOT_DIR / '.env'}' or export it before starting the backend."
        )
    return value

mongo_url = get_required_env("MONGO_URL")
db_name = get_required_env("DB_NAME")
client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

app = FastAPI(title="MMXM Crypto Screener")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
realtime_hub = RealtimeEventHub()
scanner_task: Optional[asyncio.Task] = None
bybit_task: Optional[asyncio.Task] = None


# ---------- Models ----------
class TelegramTestPayload(BaseModel):
    text: Optional[str] = "🔔 Test alert from MMXM Screener"


# ---------- Health ----------
@app.websocket("/api/ws")
async def ws_stream(ws: WebSocket):
    await realtime_hub.connect(ws)
    try:
        # Optional replay from query params on reconnect.
        since_seq = ws.query_params.get("since_seq")
        since_ts = ws.query_params.get("since_ts")
        replay = await realtime_hub.replay(
            since_seq=int(since_seq) if since_seq and since_seq.isdigit() else None,
            since_ts=int(since_ts) if since_ts and since_ts.isdigit() else None,
            limit=3000,
        )
        for ev in replay:
            await ws.send_json(ev)

        while True:
            raw = await ws.receive_text()
            await realtime_hub.handle_client_message(ws, raw)
    except WebSocketDisconnect:
        pass
    finally:
        await realtime_hub.disconnect(ws)


@api_router.get("/")
async def root():
    return {"service": "MMXM Crypto Screener", "status": "ok"}


@api_router.get("/health")
async def health():
    last_run = await db.scan_runs.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    sig_count = await db.signals.count_documents({})
    return {
        "ok": True,
        "last_scan": last_run,
        "total_signals": sig_count,
        "scan_interval_seconds": int(os.environ.get("SCAN_INTERVAL_SECONDS", 300)),
        "top_n": int(os.environ.get("TOP_N_SYMBOLS", 60)),
        "telegram_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
        "cmc_configured": bool(os.environ.get("CMC_API_KEY")),
    }


@api_router.get("/ws/replay")
async def ws_replay(
    since: Optional[int] = Query(None, description="Replay events with ts >= since"),
    since_seq: Optional[int] = Query(None, description="Replay events with seq >= since_seq"),
    limit: int = Query(2000, ge=1, le=10000),
):
    events = await realtime_hub.replay(since_seq=since_seq, since_ts=since, limit=limit)
    return {"events": events, "count": len(events)}


# ---------- Signals ----------
@api_router.get("/signals")
async def list_signals(
    side: Optional[str] = Query(None, description="long | short"),
    source: Optional[str] = Query(None, description="mmxm | screener"),
    timeframe: Optional[str] = Query(None),
    min_confidence: int = Query(0, ge=0, le=5),
    limit: int = Query(100, ge=1, le=500),
):
    q = {"confidence": {"$gte": min_confidence}}
    if side:
        q["side"] = side
    if source:
        q["source"] = source
    if timeframe:
        q["timeframe"] = timeframe
    cursor = db.signals.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


@api_router.get("/signals/stats")
async def signal_stats():
    """Lightweight aggregate stats for the dashboard."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    total_7d = await db.signals.count_documents({"created_at": {"$gte": cutoff}})
    longs_7d = await db.signals.count_documents({"created_at": {"$gte": cutoff}, "side": "long"})
    shorts_7d = await db.signals.count_documents({"created_at": {"$gte": cutoff}, "side": "short"})
    mmxm_7d = await db.signals.count_documents({"created_at": {"$gte": cutoff}, "source": "mmxm"})
    screener_7d = await db.signals.count_documents({"created_at": {"$gte": cutoff}, "source": "screener"})
    return {
        "total_7d": total_7d,
        "longs_7d": longs_7d,
        "shorts_7d": shorts_7d,
        "mmxm_7d": mmxm_7d,
        "screener_7d": screener_7d,
    }


@api_router.get("/signals/{signal_id}")
async def get_signal(signal_id: str):
    doc = await db.signals.find_one({"id": signal_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Signal not found")
    return doc


# ---------- Backtest ----------
@api_router.get("/backtest")
async def backtest(days: int = Query(30, ge=1, le=90)):
    """Walks past MMXM signals and checks if TP1 or SL hit first using subsequent klines."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sigs = await db.signals.find(
        {"source": "mmxm", "created_at": {"$gte": cutoff}}, {"_id": 0}
    ).to_list(length=500)

    if not sigs:
        return {"days": days, "total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "details": []}

    wins = 0
    losses = 0
    open_ = 0
    details = []
    for s in sigs:
        try:
            kl = await get_klines(s["symbol"], s["timeframe"], 200)
            if not kl:
                continue
            sig_time_ms = int(datetime.fromisoformat(s["created_at"]).timestamp() * 1000)
            future = [c for c in kl if c[0] > sig_time_ms]
            outcome = "open"
            for c in future:
                hi, lo = c[2], c[3]
                if s["side"] == "long":
                    if lo <= s["stop_loss"]:
                        outcome = "loss"
                        break
                    if hi >= s["take_profit_1"]:
                        outcome = "win"
                        break
                else:
                    if hi >= s["stop_loss"]:
                        outcome = "loss"
                        break
                    if lo <= s["take_profit_1"]:
                        outcome = "win"
                        break
            if outcome == "win":
                wins += 1
            elif outcome == "loss":
                losses += 1
            else:
                open_ += 1
            details.append({"symbol": s["symbol"], "side": s["side"], "tf": s["timeframe"], "outcome": outcome, "created_at": s["created_at"]})
        except Exception:
            continue

    closed = wins + losses
    win_rate = round(wins / closed * 100, 1) if closed else 0.0
    return {
        "days": days,
        "total": len(sigs),
        "wins": wins,
        "losses": losses,
        "open": open_,
        "win_rate": win_rate,
        "details": details[:200],
    }


# ---------- Market data passthroughs ----------
@api_router.get("/market/top")
async def market_top():
    tickers = await get_top_symbols(int(os.environ.get("TOP_N_SYMBOLS", 60)))
    return tickers


@api_router.get("/market/coingecko")
async def market_coingecko():
    data = await coingecko_top(100)
    trending = await coingecko_trending()
    return {"top": data, "trending": trending}


@api_router.get("/market/cmc")
async def market_cmc(symbols: str = Query("BTC,ETH,SOL,BNB,XRP,DOGE")):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return await cmc_metadata(syms)


@api_router.get("/market/klines/{symbol}")
async def market_klines(symbol: str, interval: str = "1h", limit: int = 200):
    if interval not in ("15m", "1h", "4h", "1d"):
        raise HTTPException(400, "interval must be one of 15m,1h,4h,1d")
    return await get_klines(symbol.upper(), interval, limit)


# ---------- Manual scan / Telegram test ----------
@api_router.post("/scan/run")
async def scan_run():
    return await run_scan(db, publisher=realtime_hub.publish)


@api_router.post("/telegram/test")
async def telegram_test(payload: TelegramTestPayload):
    ok = await send_message(payload.text or "test")
    return {"sent": ok}


@api_router.get("/scan/runs")
async def scan_runs(limit: int = 20):
    cursor = db.scan_runs.find({}, {"_id": 0}).sort("finished_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


# ---------- Wire & start ----------
async def validate_mongo_connection() -> None:
    try:
        await client.admin.command({"ping": 1})
    except Exception as exc:
        logger.exception("MongoDB startup check failed")
        raise RuntimeError(
            f"Unable to connect to MongoDB at MONGO_URL={mongo_url!r}: {exc}"
        ) from exc


@app.on_event("startup")
async def startup_check():
    logger.info("Validating MongoDB connection...")
    await validate_mongo_connection()
    logger.info("MongoDB connection validated.")


app.include_router(api_router)
origins = [origin.strip() for origin in os.environ.get("CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _start_scanner():
    global scanner_task, bybit_task
    # ensure indexes
    await db.signals.create_index("created_at")
    await db.signals.create_index("dedupe_key")
    await db.signals.create_index("symbol")
    await realtime_hub.start()
    scanner_task = asyncio.create_task(scanner_loop(db, publisher=realtime_hub.publish), name="scanner-loop")
    bybit_ingestor = BybitStreamIngestor(realtime_hub.publish)
    bybit_task = asyncio.create_task(bybit_ingestor.run_forever(), name="bybit-stream-loop")
    logger.info("Scanner background loop started")


@app.on_event("shutdown")
async def shutdown_db_client():
    global scanner_task, bybit_task
    for task in (scanner_task, bybit_task):
        if task:
            task.cancel()
    await asyncio.gather(*[t for t in (scanner_task, bybit_task) if t], return_exceptions=True)
    await realtime_hub.stop()
    client.close()
