"""Bybit + Gate.io public market-data clients (no auth needed)."""
import httpx
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

BYBIT_BASE = "https://api.bybit.com"
GATEIO_BASE = "https://api.gateio.ws/api/v4"

# Map our timeframe strings to exchange interval codes
BYBIT_INTERVAL = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
GATEIO_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}


async def bybit_top_symbols(limit: int = 60) -> List[Dict]:
    """Top USDT-perpetual symbols by 24h turnover on Bybit linear."""
    url = f"{BYBIT_BASE}/v5/market/tickers"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params={"category": "linear"})
        r.raise_for_status()
        data = r.json().get("result", {}).get("list", [])
    rows = [
        {
            "symbol": d["symbol"],
            "last_price": float(d.get("lastPrice") or 0),
            "price_change_24h_pct": float(d.get("price24hPcnt") or 0) * 100,
            "turnover_24h": float(d.get("turnover24h") or 0),
            "volume_24h": float(d.get("volume24h") or 0),
            "funding_rate": float(d.get("fundingRate") or 0),
            "open_interest": float(d.get("openInterest") or 0),
        }
        for d in data
        if d["symbol"].endswith("USDT")
    ]
    rows.sort(key=lambda x: x["turnover_24h"], reverse=True)
    return rows[:limit]


async def gateio_top_symbols(limit: int = 60) -> List[Dict]:
    """Top USDT-margined perpetual contracts by 24h volume on Gate.io."""
    url = f"{GATEIO_BASE}/futures/usdt/tickers"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url)
        r.raise_for_status()
        data = r.json()
    rows = []
    for d in data:
        contract = d.get("contract", "")
        if not contract.endswith("_USDT"):
            continue
        last = float(d.get("last") or 0)
        vol_quote = float(d.get("volume_24h_quote") or 0)
        vol_base = float(d.get("volume_24h_base") or 0)
        change_pct = float(d.get("change_percentage") or 0)
        funding = float(d.get("funding_rate") or 0)
        rows.append({
            "symbol": contract.replace("_USDT", "USDT"),
            "last_price": last,
            "price_change_24h_pct": change_pct,
            "turnover_24h": vol_quote,
            "volume_24h": vol_base,
            "funding_rate": funding,
            "open_interest": 0.0,
        })
    rows.sort(key=lambda x: x["turnover_24h"], reverse=True)
    return rows[:limit]


async def get_top_symbols(limit: int = 60) -> List[Dict]:
    try:
        rows = await bybit_top_symbols(limit)
        if rows:
            return rows
    except Exception as e:
        logger.warning(f"bybit top symbols failed (likely region-blocked): {e}; using Gate.io")
    return await gateio_top_symbols(limit)


async def bybit_klines(symbol: str, interval: str, limit: int = 200) -> List[List[float]]:
    """Returns list of [open_time_ms, open, high, low, close, volume] oldest->newest."""
    url = f"{BYBIT_BASE}/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": BYBIT_INTERVAL[interval],
        "limit": limit,
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        raw = r.json().get("result", {}).get("list", [])
    # Bybit returns newest first -> reverse
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


async def gateio_klines(symbol: str, interval: str, limit: int = 200) -> List[List[float]]:
    """Gate.io USDT-margined perpetuals candles."""
    contract = symbol.replace("USDT", "_USDT")
    url = f"{GATEIO_BASE}/futures/usdt/candlesticks"
    params = {"contract": contract, "interval": GATEIO_INTERVAL[interval], "limit": limit}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params)
        if r.status_code != 200:
            return []
        raw = r.json()
    out = []
    for row in raw:
        out.append([
            int(row["t"]) * 1000,
            float(row["o"]),
            float(row["h"]),
            float(row["l"]),
            float(row["c"]),
            float(row["v"]),
        ])
    return out


async def gateio_top_symbols(limit: int = 60) -> List[Dict]:
    """Top USDT-perpetuals on Gate.io by 24h quote volume."""
    url = f"{GATEIO_BASE}/futures/usdt/tickers"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url)
        r.raise_for_status()
        data = r.json()
    rows = []
    for d in data:
        contract = d.get("contract", "")
        if not contract.endswith("_USDT"):
            continue
        rows.append({
            "symbol": contract.replace("_USDT", "USDT"),
            "last_price": float(d.get("last") or 0),
            "price_change_24h_pct": float(d.get("change_percentage") or 0),
            "turnover_24h": float(d.get("volume_24h_quote") or 0),
            "volume_24h": float(d.get("volume_24h_base") or 0),
            "funding_rate": float(d.get("funding_rate") or 0),
            "open_interest": 0.0,
        })
    rows.sort(key=lambda x: x["turnover_24h"], reverse=True)
    return rows[:limit]


async def get_klines(symbol: str, interval: str, limit: int = 200) -> List[List[float]]:
    """Gate.io primary (this server can't reach Bybit). Bybit fallback."""
    try:
        k = await gateio_klines(symbol, interval, limit)
        if k and len(k) >= 50:
            return k
    except Exception as e:
        logger.warning(f"gateio klines fail {symbol} {interval}: {e}")
    try:
        return await bybit_klines(symbol, interval, limit)
    except Exception as e:
        logger.debug(f"bybit klines fail {symbol} {interval}: {e}")
        return []


async def get_top_symbols(limit: int = 60) -> List[Dict]:
    try:
        rows = await gateio_top_symbols(limit)
        if rows:
            return rows
    except Exception as e:
        logger.warning(f"gateio top symbols failed: {e}")
    try:
        return await bybit_top_symbols(limit)
    except Exception as e:
        logger.warning(f"bybit top symbols failed: {e}")
        return []


def bybit_ws_kline_interval(app_interval: str) -> str:
    """Map app intervals to Bybit websocket kline interval codes."""
    return {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}.get(app_interval, "60")
