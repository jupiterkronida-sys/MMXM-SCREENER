"""Snapshot Manager — fetch, hash, lock survivorship-free OHLCV data.

This is the ONLY module allowed to write to the snapshot store.
All subsequent modules read from the snapshot, never from the exchange.
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pymongo
import yaml

from services.exchanges import bybit_klines

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"
MANIFEST_PATH = DATA_DIR / "snapshot_manifest.json"

class DataIntegrityError(RuntimeError):
    """Raised when a snapshot's hash does not match its stored hash."""

class HoldoutAccessError(RuntimeError):
    """Raised when a module other than gate8_holdout accesses holdout data."""

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def _get_db():
    """Return synchronous pymongo database handle using env vars."""
    import os
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise RuntimeError("MONGO_URL and DB_NAME must be set in .env")
    client = pymongo.MongoClient(mongo_url)
    return client[db_name]

def _candles_hash(candles: list) -> str:
    """SHA-256 hash of the JSON-serialized candle array."""
    raw = json.dumps(candles, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def fetch_and_store_snapshot(
    symbols: List[str],
    interval: str,
    start_ts: int,
    end_ts: int,
    limit: int = 500,
    db=None,
) -> dict:
    """Fetch klines for each symbol, store in MongoDB, return manifest entry."""
    if db is None:
        db = _get_db()
    collection = db["bt_snapshots"]
    manifest = {"symbols": {}, "created_at": datetime.now(timezone.utc).isoformat(), "version": "1.0.0"}

    for symbol in symbols:
        logger.info(f"Fetching {symbol} {interval} ...")
        try:
            import asyncio
            candles = asyncio.run(bybit_klines(symbol, interval, limit))
        except Exception as e:
            logger.warning(f"Failed to fetch {symbol}: {e}. Skipping.")
            continue

        if not candles or len(candles) < 2:
            logger.warning(f"{symbol}: insufficient candles ({len(candles)}). Skipping.")
            continue

        # Filter to date range
        filtered = [c for c in candles if start_ts <= c[0] <= end_ts]
        if len(filtered) < 2:
            logger.warning(f"{symbol}: no candles in date range after filtering. Skipping.")
            continue

        h = _candles_hash(filtered)
        doc = {
            "symbol": symbol,
            "interval": interval,
            "start_ts": filtered[0][0],
            "end_ts": filtered[-1][0],
            "candles": filtered,
            "sha256_hash": h,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
        }
        collection.replace_one({"symbol": symbol, "interval": interval}, doc, upsert=True)
        manifest["symbols"][symbol] = {
            "interval": interval,
            "start_ts": filtered[0][0],
            "end_ts": filtered[-1][0],
            "num_candles": len(filtered),
            "sha256_hash": h,
        }
        logger.info(f"  Stored {len(filtered)} candles for {symbol}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    logger.info(f"Manifest written to {MANIFEST_PATH}")
    return manifest

def load_snapshot(symbol: str, interval: str, db=None) -> Optional[list]:
    """Load a snapshot from MongoDB and verify its hash."""
    if db is None:
        db = _get_db()
    collection = db["bt_snapshots"]
    doc = collection.find_one({"symbol": symbol, "interval": interval})
    if doc is None:
        logger.warning(f"No snapshot found for {symbol} {interval}")
        return None

    stored_hash = doc.get("sha256_hash")
    computed = _candles_hash(doc["candles"])
    if stored_hash != computed:
        raise DataIntegrityError(
            f"Snapshot hash mismatch for {symbol} {interval}: "
            f"stored={stored_hash}, computed={computed}. "
            f"Data may have been tampered with."
        )
    logger.info(f"Snapshot for {symbol} {interval}: hash OK ({len(doc['candles'])} candles)")
    return doc["candles"]

def load_snapshot_from_manifest(manifest_path: str = None) -> dict:
    """Load and return the snapshot manifest JSON."""
    path = Path(manifest_path) if manifest_path else MANIFEST_PATH
    if not path.exists():
        raise FileNotFoundError(f"Snapshot manifest not found at {path}")
    with open(path) as f:
        return json.load(f)

def list_snapshots(db=None) -> list:
    """List all stored snapshots with metadata."""
    if db is None:
        db = _get_db()
    collection = db["bt_snapshots"]
    docs = collection.find({}, {"symbol": 1, "interval": 1, "start_ts": 1, "end_ts": 1,
                                 "num_candles": {"$size": "$candles"}, "sha256_hash": 1})
    return list(docs)
