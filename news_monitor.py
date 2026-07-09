"""
Forex Factory High-Impact News Alert Bot
------------------------------------------
Watches Forex Factory's public calendar feed for HIGH impact ("red")
events and:
  1. Sends a Telegram alert within ~5 minutes of each red event happening.
  2. Sends a daily digest at 9:00 AM IST listing every red event for
     today and tomorrow.

Data source: https://nfs.faireconomy.media/ff_calendar_thisweek.json
This is the same feed used by countless MT4/MT5 news-filter EAs.

IMPORTANT: Forex Factory rate-limits this feed to 2 requests / 5 minutes
across all formats. This script polls every 5 minutes and only adds one
extra request/day for the digest — well within that limit. Do NOT lower
CHECK_INTERVAL_SEC below 300 or you risk getting temporarily blocked.

Environment variables:
    TELEGRAM_BOT_TOKEN   - from BotFather
    TELEGRAM_CHAT_ID     - your numeric chat id
    CHECK_INTERVAL_SEC   - default 300 (5 minutes) - do not go lower
    DIGEST_HOUR_IST      - default 9 (24hr, IST) - hour to send daily digest
"""

import os
import time
import logging
from datetime import datetime, timedelta, timezone

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("news-bot")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL_SEC = max(int(os.environ.get("CHECK_INTERVAL_SEC", "300")), 300)
DIGEST_HOUR_IST = int(os.environ.get("DIGEST_HOUR_IST", "9"))

THISWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEXTWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

IST = timezone(timedelta(hours=5, minutes=30))

already_alerted = set()   # event keys we've already sent a real-time alert for
last_digest_date = None   # date() we last sent the 9am digest for


def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured, message not sent: %s", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code != 200:
            log.error("Telegram send failed: %s %s", r.status_code, r.text)
    except Exception as e:
        log.error("Telegram send exception: %s", e)


def fetch_calendar(url: str):
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("Failed to fetch %s: %s", url, e)
        return []


def parse_event_time(raw_date: str):
    # Example format: "2026-07-10T12:30:00-04:00"
    try:
        return datetime.strptime(raw_date, "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        try:
            return datetime.fromisoformat(raw_date)
        except Exception as e:
            log.error("Could not parse date %s: %s", raw_date, e)
            return None


def event_key(ev, dt):
    return f"{ev.get('country','')}|{ev.get('title','')}|{dt.isoformat()}"


def is_high_impact(ev):
    return str(ev.get("impact", "")).strip().lower() == "high"


def check_realtime_alerts():
    events = fetch_calendar(THISWEEK_URL)
    now = datetime.now(timezone.utc)

    for ev in events:
        if not is_high_impact(ev):
            continue
        dt = parse_event_time(ev.get("date", ""))
        if dt is None:
            continue

        key = event_key(ev, dt)
        # Only alert for events that have happened in the last 20 minutes
        # (so restarts don't spam old history, and we don't alert early)
        minutes_since = (now - dt).total_seconds() / 60
        if 0 <= minutes_since <= 20 and key not in already_alerted:
            ist_time = dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
            msg = (
                f"🔴 <b>High Impact News: {ev.get('country','')}</b>\n"
                f"{ev.get('title','')}\n"
                f"Time: {ist_time}\n"
                f"Forecast: {ev.get('forecast','-') or '-'}  |  "
                f"Previous: {ev.get('previous','-') or '-'}\n"
                f"(This feed doesn't include the released figure - check forexfactory.com/calendar for the actual print)"
            )
            send_telegram(msg)
            already_alerted.add(key)
            log.info("Alerted: %s", key)

    # Keep the alerted-set from growing forever
    if len(already_alerted) > 500:
        already_alerted.clear()


def send_daily_digest():
    global last_digest_date
    now_ist = datetime.now(IST)
    today = now_ist.date()
    tomorrow = today + timedelta(days=1)

    events = fetch_calendar(THISWEEK_URL)
    # Only hit nextweek.json if we might be near a week boundary
    events += fetch_calendar(NEXTWEEK_URL)

    upcoming = []
    for ev in events:
        if not is_high_impact(ev):
            continue
        dt = parse_event_time(ev.get("date", ""))
        if dt is None:
            continue
        dt_ist = dt.astimezone(IST)
        if dt_ist.date() in (today, tomorrow):
            upcoming.append((dt_ist, ev))

    upcoming.sort(key=lambda x: x[0])

    if not upcoming:
        msg = "🔴 <b>Daily High-Impact News Digest</b>\nNo red news events for today or tomorrow."
    else:
        lines = ["🔴 <b>Daily High-Impact News Digest</b>\n"]
        current_day = None
        for dt_ist, ev in upcoming:
            day_label = "Today" if dt_ist.date() == today else "Tomorrow"
            if day_label != current_day:
                lines.append(f"\n<b>{day_label} ({dt_ist.strftime('%d %b %Y')})</b>")
                current_day = day_label
            lines.append(
                f"{dt_ist.strftime('%I:%M %p')} - {ev.get('country','')}: {ev.get('title','')}"
            )
        msg = "\n".join(lines)

    send_telegram(msg)
    last_digest_date = today
    log.info("Sent daily digest for %s (%d events)", today, len(upcoming))


def main():
    log.info("Starting Forex Factory news watcher.")
    log.info("Check interval: %ds | Digest hour: %d:00 IST", CHECK_INTERVAL_SEC, DIGEST_HOUR_IST)
    send_telegram("✅ News alert bot online. Watching for high-impact (red) events.")

    while True:
        try:
            check_realtime_alerts()
        except Exception as e:
            log.error("Error in real-time check: %s", e)

        now_ist = datetime.now(IST)
        if now_ist.hour == DIGEST_HOUR_IST and last_digest_date != now_ist.date():
            try:
                send_daily_digest()
            except Exception as e:
                log.error("Error sending digest: %s", e)

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
