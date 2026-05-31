"""Holdout Manager — permanently protect the holdout set.

This module is written once, run once to split the data.
After splitting, gate8_holdout.py is the only module allowed
to read bt_holdout.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pymongo
import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"
HOLDOUT_MANIFEST_PATH = DATA_DIR / "holdout_manifest.json"

class HoldoutAccessError(RuntimeError):
    """Raised when a module other than gate8_holdout accesses holdout data."""

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def _write_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

def _get_db():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise RuntimeError("MONGO_URL and DB_NAME must be set in .env")
    client = pymongo.MongoClient(mongo_url)
    return client[db_name]

def _ensure_holdout_collections(db=None):
    """Create bt_training and bt_holdout collections if they don't exist."""
    if db is None:
        db = _get_db()
    existing = db.list_collection_names()
    for name in ("bt_training", "bt_holdout"):
        if name not in existing:
            db.create_collection(name)
            logger.info(f"Created collection {name}")

def assert_holdout_access(caller_module: str) -> None:
    """Raise HoldoutAccessError if caller is not gate8_holdout."""
    if caller_module != "gate8_holdout":
        raise HoldoutAccessError(
            f"Module '{caller_module}' attempted to access holdout data. "
            f"Only gate8_holdout.py is permitted. HALT."
        )

def split_holdout(holdout_start_ts: int, db=None) -> dict:
    """Split bt_snapshots into bt_training and bt_holdout.

    Args:
        holdout_start_ts: Unix timestamp ms — candles at or after this go to holdout.
        db: MongoDB database handle (optional, creates one if None).

    Returns:
        Manifest dict with holdout metadata.
    """
    if db is None:
        db = _get_db()
    _ensure_holdout_collections(db)

    snapshots_col = db["bt_snapshots"]
    training_col = db["bt_training"]
    holdout_col = db["bt_holdout"]

    # Clear previous splits
    training_col.delete_many({})
    holdout_col.delete_many({})

    snapshots = list(snapshots_col.find({}))
    if not snapshots:
        logger.warning("No snapshots found to split.")
        return {"holdout_start_ts": holdout_start_ts, "symbols": {}, "locked_at": None}

    manifest = {
        "holdout_start_ts": holdout_start_ts,
        "holdout_start_date": datetime.fromtimestamp(holdout_start_ts / 1000, tz=timezone.utc).isoformat(),
        "symbols": {},
        "locked_at": None,
        "holdout_hash": None,
    }

    for doc in snapshots:
        _process_snapshot_doc(doc, holdout_start_ts, training_col, holdout_col, manifest)

    return manifest


def _process_snapshot_doc(doc: dict, holdout_start_ts: int, training_col, holdout_col, manifest: dict) -> None:
    """Split one snapshot document into training/holdout and persist."""
    symbol = doc["symbol"]
    interval = doc["interval"]
    candles = doc["candles"]
    version = doc.get("version", "1.0.0")

    training_candles = [c for c in candles if c[0] < holdout_start_ts]
    holdout_candles = [c for c in candles if c[0] >= holdout_start_ts]

    if training_candles:
        training_col.replace_one(
            {"symbol": symbol, "interval": interval},
            {
                "symbol": symbol,
                "interval": interval,
                "start_ts": training_candles[0][0],
                "end_ts": training_candles[-1][0],
                "candles": training_candles,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "version": version,
            },
            upsert=True,
        )
        logger.info("Training: %s (%d candles)", symbol, len(training_candles))

    if holdout_candles:
        holdout_hash = _hash_candles(holdout_candles)
        holdout_col.replace_one(
            {"symbol": symbol, "interval": interval},
            {
                "symbol": symbol,
                "interval": interval,
                "start_ts": holdout_candles[0][0],
                "end_ts": holdout_candles[-1][0],
                "candles": holdout_candles,
                "sha256_hash": holdout_hash,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "version": version,
            },
            upsert=True,
        )
        manifest["symbols"][symbol] = {
            "interval": interval,
            "num_training_candles": len(training_candles),
            "num_holdout_candles": len(holdout_candles),
            "holdout_hash": holdout_hash,
        }
        logger.info("Holdout: %s (%d candles)", symbol, len(holdout_candles))


def _hash_candles(candles: list) -> str:
    import hashlib
    raw = json.dumps(candles, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def lock_holdout(db=None) -> dict:
    """Finalize the split: write manifest, set holdout_locked=true in config."""
    import hashlib

    if db is None:
        db = _get_db()

    cfg = _load_config()
    holdout_start_str = cfg["dataset"]["holdout_start"]
    holdout_start_dt = datetime.fromisoformat(holdout_start_str.replace("Z", "+00:00"))
    holdout_start_ts = int(holdout_start_dt.timestamp() * 1000)

    manifest = split_holdout(holdout_start_ts, db=db)

    # Compute combined hash of all holdout data
    holdout_col = db["bt_holdout"]
    all_docs = list(holdout_col.find({}, {"sha256_hash": 1}))
    combined = "".join(d.get("sha256_hash", "") for d in all_docs)
    manifest["holdout_hash"] = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    manifest["locked_at"] = datetime.now(timezone.utc).isoformat()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HOLDOUT_MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    logger.info(f"Holdout manifest written to {HOLDOUT_MANIFEST_PATH}")

    cfg["holdout_locked"] = True
    _write_config(cfg)
    logger.info("holdout_locked = true set in config")

    return manifest

def verify_holdout_integrity(db=None) -> bool:
    """Verify holdout hash matches manifest."""
    import hashlib

    if not HOLDOUT_MANIFEST_PATH.exists():
        logger.error("Holdout manifest not found. Run lock_holdout() first.")
        return False

    with open(HOLDOUT_MANIFEST_PATH) as f:
        manifest = json.load(f)

    if db is None:
        db = _get_db()

    holdout_col = db["bt_holdout"]
    all_docs = list(holdout_col.find({}, {"sha256_hash": 1}))
    combined = "".join(d.get("sha256_hash", "") for d in all_docs)
    current_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()

    if current_hash != manifest.get("holdout_hash"):
        logger.error(f"Holdout hash mismatch: stored={manifest['holdout_hash']}, computed={current_hash}")
        return False

    logger.info("Holdout integrity verified: hash matches")
    return True
