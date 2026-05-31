"""Telegram alert sender."""
import os
import httpx
import logging

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")


def _format_signal(sig: dict) -> str:
    if sig.get("source") == "mmxm":
        bias_emoji = "🟢 LONG" if sig["side"] == "long" else "🔴 SHORT"
        return (
            f"<b>MMXM {bias_emoji} — {sig['symbol']}</b>\n"
            f"TF: {sig['timeframe']}  |  Conf: {'⭐'*sig['confidence']}\n"
            f"Price: <code>{sig['current_price']}</code>\n"
            f"Entry: <code>{sig['entry_zone_low']} – {sig['entry_zone_high']}</code>\n"
            f"SL: <code>{sig['stop_loss']}</code>\n"
            f"TP1: <code>{sig['take_profit_1']}</code>\n"
            f"TP2: <code>{sig['take_profit_2']}</code>\n"
            f"TP3: <code>{sig['take_profit_3']}</code>\n"
            f"R:R (to TP2): {sig['risk_reward_tp2']}\n"
            f"Swept: {sig['swept_level']}  |  OB:{sig['ob_used']} FVG:{sig['fvg_used']}\n"
            f"<i>Not financial advice.</i>"
        )
    # screener
    arrow = "🟢 PUMP" if sig["kind"] == "pump" else "🔴 DUMP"
    return (
        f"<b>{arrow} — {sig['symbol']}</b>\n"
        f"1h move: {sig['pct_change_1h']}%  |  Vol z: {sig['volume_zscore']}σ\n"
        f"RSI: {sig['rsi']}  |  Conf: {'⭐'*sig['confidence']}\n"
        f"Price: <code>{sig['current_price']}</code>\n"
        f"<i>Not financial advice.</i>"
    )


async def send_alert(sig: dict) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    text = _format_signal(sig)
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            )
            if r.status_code != 200:
                logger.warning("telegram fail %s %s", r.status_code, r.text[:200])
                return False
            return True
    except Exception as e:
        logger.warning("telegram error: %s", e)
        return False


async def send_message(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            return r.status_code == 200
    except Exception:
        return False
