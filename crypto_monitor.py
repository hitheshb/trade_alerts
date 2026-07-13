"""
Crypto 1-Minute Volatility Alert Bot
-------------------------------------
Watches BTCUSDT and ETHUSDT (or any symbols you list) on the 1-minute
timeframe using Binance's free public REST API. Whenever a candle closes
with a bigger range (High - Low) than every candle in the last 3-4 hours,
it sends you a Telegram message.

No API key needed for Binance market data (public endpoint).
You DO need a Telegram bot token + chat id (see README.md).

Environment variables (set these on Railway / locally):
    TELEGRAM_BOT_TOKEN   - from BotFather
    TELEGRAM_CHAT_ID     - your numeric chat id
    SYMBOLS              - comma separated, default "BTCUSDT,ETHUSDT"
    LOOKBACK_MINUTES     - how many past 1-min candles count as "recent
                            history", default 210 (3.5 hours)
    CHECK_INTERVAL_SEC   - how often to poll for a new closed candle,
                            default 15
    MIN_RANGE_PCT        - optional noise filter: ignore candles whose
                            range is below this % of price (default 0,
                            meaning no filter)
"""

import os
import time
import logging
from collections import deque
from datetime import datetime, timezone

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("volatility-bot")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()]
LOOKBACK_MINUTES = int(os.environ.get("LOOKBACK_MINUTES", "210"))
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "15"))
MIN_RANGE_PCT = float(os.environ.get("MIN_RANGE_PCT", "0"))

BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"


def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured, message not sent: %s", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.error("Telegram send failed: %s %s", r.status_code, r.text)
    except Exception as e:
        log.error("Telegram send exception: %s", e)


def fetch_klines(symbol: str, limit: int):
    params = {"symbol": symbol, "interval": "1m", "limit": limit}
    r = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


class SymbolWatcher:
    """Tracks the rolling history of candle ranges for one symbol."""

    def __init__(self, symbol: str, lookback: int):
        self.symbol = symbol
        self.lookback = lookback
        self.ranges = deque(maxlen=lookback)
        self.last_open_time = None
        self._seed()

    def _seed(self):
        # Fetch lookback+1 candles; the last one may still be forming,
        # so we only use closed candles (all except the very last one).
        raw = fetch_klines(self.symbol, self.lookback + 2)
        closed = raw[:-1]  # drop the currently-forming candle
        for c in closed[-self.lookback:]:
            high, low = float(c[2]), float(c[3])
            self.ranges.append(high - low)
        self.last_open_time = closed[-1][0] if closed else None
        log.info("[%s] Seeded with %d closed candles.", self.symbol, len(self.ranges))

    def check_for_new_candle(self):
        raw = fetch_klines(self.symbol, 3)
        closed = raw[:-1]  # last entry is the still-forming candle
        if not closed:
            return
        latest_closed = closed[-1]
        open_time = latest_closed[0]

        if open_time == self.last_open_time:
            return  # nothing new yet

        open_p = float(latest_closed[1])
        high_p = float(latest_closed[2])
        low_p = float(latest_closed[3])
        close_p = float(latest_closed[4])
        candle_range = high_p - low_p
        range_pct = (candle_range / close_p) * 100 if close_p else 0

        # Compare against history BEFORE adding this candle
        prev_max = max(self.ranges) if self.ranges else 0

        is_bigger = candle_range > prev_max
        passes_noise_filter = range_pct >= MIN_RANGE_PCT

        if is_bigger and passes_noise_filter and prev_max > 0:
            direction = "🟢 Bullish" if close_p >= open_p else "🔴 Bearish"
            msg = f"⚡ <b>{self.symbol}</b> - {direction}"
            log.info("ALERT %s: range %.2f > prev max %.2f", self.symbol, candle_range, prev_max)
            send_telegram(msg)

        self.ranges.append(candle_range)
        self.last_open_time = open_time


def main():
    log.info("Starting volatility watcher for: %s", SYMBOLS)
    log.info("Lookback: %d minutes | Check interval: %ds", LOOKBACK_MINUTES, CHECK_INTERVAL_SEC)

    watchers = {}
    for sym in SYMBOLS:
        try:
            watchers[sym] = SymbolWatcher(sym, LOOKBACK_MINUTES)
        except Exception as e:
            log.error("Failed to initialize %s: %s", sym, e)

    send_telegram(f"✅ Volatility bot online. Watching: {', '.join(watchers.keys())}")

    while True:
        for sym, watcher in watchers.items():
            try:
                watcher.check_for_new_candle()
            except Exception as e:
                log.error("[%s] Error checking candle: %s", sym, e)
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
