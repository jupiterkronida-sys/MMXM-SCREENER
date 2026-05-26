"""Backend API tests for MMXM Crypto Screener."""
import os
import time
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv

# Load frontend .env to get the public URL we should hit
load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- Health ----------
class TestHealth:
    def test_health_ok(self, client):
        r = client.get(f"{API}/health", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert d.get("telegram_configured") is True
        assert d.get("cmc_configured") is True


# ---------- Market data ----------
class TestMarket:
    def test_market_top_ge_20(self, client):
        r = client.get(f"{API}/market/top", timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 20, f"Got only {len(data)} tickers"
        # Validate shape of first ticker
        sample = data[0]
        assert "symbol" in sample

    def test_market_coingecko(self, client):
        r = client.get(f"{API}/market/coingecko", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "top" in d and "trending" in d
        assert isinstance(d["top"], list)
        assert isinstance(d["trending"], list)
        assert len(d["top"]) > 0

    def test_market_cmc(self, client):
        r = client.get(f"{API}/market/cmc", params={"symbols": "BTC,ETH"}, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d, dict)
        # Either "BTC" key or list. Check for rank/market_cap presence somewhere.
        # Accept multiple shapes
        flat = str(d)
        assert "BTC" in flat or "rank" in flat or "market_cap" in flat

    def test_market_klines_btc(self, client):
        r = client.get(f"{API}/market/klines/BTCUSDT", params={"interval": "1h"}, timeout=60)
        assert r.status_code == 200, r.text
        kl = r.json()
        assert isinstance(kl, list)
        assert len(kl) >= 50
        c0 = kl[0]
        assert isinstance(c0, list) and len(c0) >= 6

    def test_market_klines_invalid_interval(self, client):
        r = client.get(f"{API}/market/klines/BTCUSDT", params={"interval": "2h"}, timeout=30)
        assert r.status_code == 400


# ---------- Signals ----------
class TestSignals:
    def test_list_signals(self, client):
        r = client.get(f"{API}/signals", timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_signals_filters(self, client):
        for params in [{"side": "long"}, {"side": "short"}, {"source": "mmxm"}, {"source": "screener"}, {"min_confidence": 3}]:
            r = client.get(f"{API}/signals", params=params, timeout=30)
            assert r.status_code == 200, f"Failed for {params}: {r.text}"
            data = r.json()
            assert isinstance(data, list)
            for s in data:
                if "side" in params:
                    assert s["side"] == params["side"]
                if "source" in params:
                    assert s["source"] == params["source"]
                if "min_confidence" in params:
                    assert s["confidence"] >= params["min_confidence"]

    def test_signals_stats(self, client):
        r = client.get(f"{API}/signals/stats", timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ("total_7d", "longs_7d", "shorts_7d", "mmxm_7d", "screener_7d"):
            assert k in d, f"Missing key {k}"
            assert isinstance(d[k], int)


# ---------- Scan ----------
class TestScan:
    def test_scan_run(self, client):
        t0 = time.time()
        r = client.post(f"{API}/scan/run", timeout=180)
        elapsed = time.time() - t0
        assert r.status_code == 200, r.text
        d = r.json()
        assert elapsed < 120, f"Scan took {elapsed:.1f}s"
        assert d.get("symbols_scanned", 0) >= 20, f"symbols_scanned={d.get('symbols_scanned')}"

    def test_scan_runs(self, client):
        r = client.get(f"{API}/scan/runs", timeout=30)
        assert r.status_code == 200
        runs = r.json()
        assert isinstance(runs, list)
        assert len(runs) >= 1
        assert "symbols_scanned" in runs[0]


# ---------- Backtest ----------
class TestBacktest:
    def test_backtest_30d(self, client):
        r = client.get(f"{API}/backtest", params={"days": 30}, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("total", "wins", "losses", "win_rate"):
            assert k in d
        assert isinstance(d["total"], int)
        assert isinstance(d["wins"], int)
        assert isinstance(d["losses"], int)


# ---------- Telegram ----------
class TestTelegram:
    def test_telegram_test_send(self, client):
        r = client.post(f"{API}/telegram/test", json={"text": "🧪 Backend test ping (automated)"}, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("sent") is True
