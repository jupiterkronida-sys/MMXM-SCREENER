"""CoinGecko + CoinMarketCap discovery / metadata."""
import os
import httpx
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

CG_BASE = "https://api.coingecko.com/api/v3"
CMC_BASE = "https://pro-api.coinmarketcap.com/v1"


async def coingecko_top(n: int = 250) -> List[Dict]:
    url = f"{CG_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": min(n, 250),
        "page": 1,
        "price_change_percentage": "1h,24h,7d",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params)
        if r.status_code != 200:
            return []
        return r.json()


async def coingecko_trending() -> List[str]:
    """Returns list of trending coin symbols (uppercase)."""
    url = f"{CG_BASE}/search/trending"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url)
        if r.status_code != 200:
            return []
        items = r.json().get("coins", [])
    return [i["item"]["symbol"].upper() for i in items]


async def cmc_metadata(symbols: List[str]) -> Dict[str, Dict]:
    """Fetch CMC quotes for given symbols (e.g., ['BTC','ETH'])."""
    api_key = os.environ.get("CMC_API_KEY")
    if not api_key or not symbols:
        return {}
    url = f"{CMC_BASE}/cryptocurrency/quotes/latest"
    headers = {"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"}
    params = {"symbol": ",".join(symbols[:100]), "convert": "USD"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params, headers=headers)
        if r.status_code != 200:
            logger.warning(f"CMC fetch failed: {r.status_code}")
            return {}
        data = r.json().get("data", {})
    out = {}
    for sym, payload in data.items():
        # CMC may return list (duplicates) or dict
        item = payload[0] if isinstance(payload, list) else payload
        q = item.get("quote", {}).get("USD", {})
        out[sym] = {
            "rank": item.get("cmc_rank"),
            "market_cap": q.get("market_cap"),
            "volume_24h": q.get("volume_24h"),
            "percent_change_1h": q.get("percent_change_1h"),
            "percent_change_24h": q.get("percent_change_24h"),
            "percent_change_7d": q.get("percent_change_7d"),
        }
    return out
