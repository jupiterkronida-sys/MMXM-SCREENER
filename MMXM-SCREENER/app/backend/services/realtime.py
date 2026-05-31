"""Realtime event infrastructure: queue, sequencing, replay, websocket fanout."""
import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class ClientState:
    websocket: WebSocket
    subscriptions: Set[str]
    last_pong_ms: int


class RealtimeEventHub:
    """Single-process hub for ordered events and websocket broadcast."""

    def __init__(self, replay_size: int = 20000):
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=50000)
        self._replay: deque = deque(maxlen=replay_size)
        self._clients: Dict[WebSocket, ClientState] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        self._consumer_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._dropped_events: int = 0
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._consumer_task = asyncio.create_task(self._consumer_loop(), name="realtime-consumer")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="realtime-heartbeat")
        logger.info("RealtimeEventHub started")

    async def stop(self):
        self._running = False
        tasks = [t for t in (self._consumer_task, self._heartbeat_task) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._consumer_task = None
        self._heartbeat_task = None
        logger.info("RealtimeEventHub stopped")

    async def connect(self, ws: WebSocket):
        await ws.accept()
        now = int(time.time() * 1000)
        state = ClientState(websocket=ws, subscriptions={"*"}, last_pong_ms=now)
        async with self._lock:
            self._clients[ws] = state
        await ws.send_json(
            {
                "seq": self._seq,
                "ts": now,
                "type": "system:hello",
                "payload": {"server_time_ms": now},
            }
        )

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._clients.pop(ws, None)

    async def handle_client_message(self, ws: WebSocket, raw_message: str):
        try:
            msg = json.loads(raw_message)
        except Exception:
            return
        op = msg.get("op")
        if op == "pong":
            state = self._clients.get(ws)
            if state:
                state.last_pong_ms = int(time.time() * 1000)
            return
        if op not in ("subscribe", "unsubscribe"):
            return

        topics = msg.get("topics") or []
        if not isinstance(topics, list):
            return
        state = self._clients.get(ws)
        if not state:
            return
        clean = {str(t) for t in topics if isinstance(t, str) and t}
        if not clean:
            return

        if op == "subscribe":
            state.subscriptions.update(clean)
        else:
            state.subscriptions.difference_update(clean)

        await ws.send_json(
            {
                "seq": self._seq,
                "ts": int(time.time() * 1000),
                "type": "system:subscribed",
                "payload": {"topics": sorted(state.subscriptions)},
            }
        )

    async def publish(self, event_type: str, payload: Dict[str, Any], topic: str = "*"):
        """Producer API: enqueue event; delivery/broadcast occurs asynchronously."""
        if not self._running:
            return
        item = {"type": event_type, "payload": payload, "topic": topic}
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self._dropped_events += 1
            logger.warning("Realtime queue full; dropping event type=%s (total dropped=%d)", event_type, self._dropped_events)

    async def replay(self, since_seq: Optional[int] = None, since_ts: Optional[int] = None, limit: int = 2000) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 10000))
        if since_seq is None and since_ts is None:
            return list(self._replay)[-limit:]

        out: List[Dict[str, Any]] = []
        for ev in self._replay:
            if since_seq is not None and ev["seq"] < since_seq:
                continue
            if since_ts is not None and ev["ts"] < since_ts:
                continue
            out.append(ev)
        return out[-limit:]

    async def _consumer_loop(self):
        while self._running:
            item = await self._queue.get()
            self._seq += 1
            event = {
                "seq": self._seq,
                "ts": int(time.time() * 1000),
                "type": item["type"],
                "topic": item.get("topic", "*"),
                "payload": item.get("payload", {}),
            }
            self._replay.append(event)
            await self._broadcast(event)

    async def _broadcast(self, event: Dict[str, Any]):
        async with self._lock:
            clients = list(self._clients.values())
        if not clients:
            return

        stale: List[WebSocket] = []
        topic = event.get("topic", "*")
        for client in clients:
            if "*" not in client.subscriptions and topic not in client.subscriptions:
                continue
            try:
                await asyncio.wait_for(client.websocket.send_json(event), timeout=1.0)
            except Exception:
                stale.append(client.websocket)
        for ws in stale:
            await self.disconnect(ws)

    async def _heartbeat_loop(self):
        while self._running:
            await asyncio.sleep(20)
            now = int(time.time() * 1000)
            async with self._lock:
                clients = list(self._clients.values())
            stale = []
            for client in clients:
                if now - client.last_pong_ms > 70000:
                    stale.append(client.websocket)
                    continue
                try:
                    await client.websocket.send_json(
                        {
                            "seq": self._seq,
                            "ts": now,
                            "type": "system:ping",
                            "payload": {"ts": now},
                        }
                    )
                except Exception:
                    stale.append(client.websocket)
            for ws in stale:
                await self.disconnect(ws)
