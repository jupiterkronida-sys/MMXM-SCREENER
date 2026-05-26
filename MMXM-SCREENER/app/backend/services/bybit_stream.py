"""Bybit V5 websocket ingestion with normalization and event publishing."""
import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable, Dict, List, Optional

import websockets

from .exchanges import get_top_symbols

logger = logging.getLogger(__name__)

BYBIT_PUBLIC_LINEAR = "wss://stream.bybit.com/v5/public/linear"
Publisher = Callable[[str, dict, str], Awaitable[None]]


class BybitStreamIngestor:
    def __init__(self, publisher: Publisher):
        self._publisher = publisher
        self._running = False
        self._symbols: List[str] = []
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def run_forever(self):
        self._running = True
        backoff = 1
        while self._running:
            try:
                await self._refresh_symbols()
                await self._connect_once()
                backoff = 1
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("bybit stream reconnecting: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def stop(self):
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None

    async def _refresh_symbols(self):
        top_n = int(os.environ.get("TOP_N_SYMBOLS", 60))
        rows = await get_top_symbols(top_n)
        self._symbols = [r["symbol"] for r in rows][: min(30, top_n)]
        if not self._symbols:
            self._symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    async def _connect_once(self):
        async with websockets.connect(BYBIT_PUBLIC_LINEAR, ping_interval=None, close_timeout=5) as ws:
            await self._subscribe(ws)
            self._heartbeat_task = asyncio.create_task(self._heartbeat(ws), name="bybit-heartbeat")
            async for raw in ws:
                await self._handle_message(raw)

    async def _subscribe(self, ws):
        topics: List[str] = []
        for symbol in self._symbols:
            topics.append(f"tickers.{symbol}")
            topics.append(f"publicTrade.{symbol}")
            topics.append(f"orderbook.50.{symbol}")
            topics.append(f"kline.1.{symbol}")
            topics.append(f"kline.60.{symbol}")
            topics.append(f"kline.240.{symbol}")
        for i in range(0, len(topics), 10):
            await ws.send(json.dumps({"op": "subscribe", "args": topics[i : i + 10]}))

    async def _heartbeat(self, ws):
        while self._running:
            await asyncio.sleep(15)
            try:
                await ws.send(json.dumps({"op": "ping", "req_id": str(int(time.time() * 1000))}))
            except Exception:
                return

    async def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return

        topic = msg.get("topic")
        if not topic:
            return
        data = msg.get("data")
        if data is None:
            return

        if topic.startswith("tickers."):
            payload = self._normalize_ticker(topic, data)
            if payload:
                await self._publisher("market:ticker", payload, topic="market")
            return
        if topic.startswith("kline."):
            payload = self._normalize_kline(topic, data)
            if payload:
                await self._publisher("market:kline", payload, topic="market")
            return
        if topic.startswith("publicTrade."):
            for payload in self._normalize_trades(topic, data):
                await self._publisher("market:trade", payload, topic="market")
            return
        if topic.startswith("orderbook."):
            payload = self._normalize_orderbook(topic, data)
            if payload:
                await self._publisher("market:orderbook", payload, topic="market")

    def _normalize_ticker(self, topic: str, data: dict) -> Optional[Dict]:
        symbol = topic.split(".", 1)[1]
        return {
            "source": "bybit",
            "symbol": symbol,
            "last_price": float(data.get("lastPrice") or 0),
            "price_change_24h_pct": float(data.get("price24hPcnt") or 0) * 100,
            "turnover_24h": float(data.get("turnover24h") or 0),
            "volume_24h": float(data.get("volume24h") or 0),
            "funding_rate": float(data.get("fundingRate") or 0),
            "open_interest": float(data.get("openInterest") or 0),
        }

    def _normalize_kline(self, topic: str, data: list) -> Optional[Dict]:
        if not data:
            return None
        row = data[0]
        parts = topic.split(".")
        interval = parts[1] if len(parts) > 2 else "1"
        symbol = parts[-1]
        return {
            "source": "bybit",
            "symbol": symbol,
            "interval": interval,
            "start": int(row.get("start") or 0),
            "end": int(row.get("end") or 0),
            "open": float(row.get("open") or 0),
            "high": float(row.get("high") or 0),
            "low": float(row.get("low") or 0),
            "close": float(row.get("close") or 0),
            "volume": float(row.get("volume") or 0),
            "turnover": float(row.get("turnover") or 0),
            "confirm": bool(row.get("confirm")),
        }

    def _normalize_trades(self, topic: str, data: list) -> List[Dict]:
        symbol = topic.split(".", 1)[1]
        out = []
        for t in data:
            out.append(
                {
                    "source": "bybit",
                    "symbol": symbol,
                    "ts": int(t.get("T") or 0),
                    "side": t.get("S"),
                    "price": float(t.get("p") or 0),
                    "size": float(t.get("v") or 0),
                }
            )
        return out

    def _normalize_orderbook(self, topic: str, data: dict) -> Optional[Dict]:
        parts = topic.split(".")
        symbol = parts[-1]
        depth = int(parts[1]) if len(parts) > 2 and parts[1].isdigit() else 50
        bids = [[float(p), float(q)] for p, q in (data.get("b") or [])]
        asks = [[float(p), float(q)] for p, q in (data.get("a") or [])]
        return {
            "source": "bybit",
            "symbol": symbol,
            "depth": depth,
            "u": int(data.get("u") or 0),
            "seq": int(data.get("seq") or 0),
            "bids": bids,
            "asks": asks,
        }
