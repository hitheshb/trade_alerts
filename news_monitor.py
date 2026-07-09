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
import threading
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


def send_telegram(message: str, chat_id: str = None):
    target = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target:
        log.warning("Telegram not configured, message not sent: %s", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": target, "text": message, "parse_mode": "HTML"},
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


def build_events_message(day_specs):
    """day_specs: list of (label, date) tuples, e.g. [("Today", date1), ("Tomorrow", date2)]"""
    events = fetch_calendar(THISWEEK_URL) + fetch_calendar(NEXTWEEK_URL)
    wanted_dates = {day for _, day in day_specs}
    label_for = {day: label for label, day in day_specs}

    matches = []
    for ev in events:
        if not is_high_impact(ev):
            continue
        dt = parse_event_time(ev.get("date", ""))
        if dt is None:
            continue
        dt_ist = dt.astimezone(IST)
        if dt_ist.date() in wanted_dates:
            matches.append((dt_ist, ev))

    matches.sort(key=lambda x: x[0])

    if not matches:
        days_str = " & ".join(label for label, _ in day_specs)
        return f"🔴 <b>High-Impact News</b>\nNo red news events for {days_str}."

    lines = ["🔴 <b>High-Impact News</b>"]
    current_label = None
    for dt_ist, ev in matches:
        label = label_for[dt_ist.date()]
        if label != current_label:
            lines.append(f"\n<b>{label} ({dt_ist.strftime('%d %b %Y')})</b>")
            current_label = label
        lines.append(f"{dt_ist.strftime('%I:%M %p')} - {ev.get('country','')}: {ev.get('title','')}")
    return "\n".join(lines)


def send_daily_digest():
    global last_digest_date
    now_ist = datetime.now(IST)
    today = now_ist.date()
    tomorrow = today + timedelta(days=1)

    msg = build_events_message([("Today", today), ("Tomorrow", tomorrow)])
    send_telegram(msg)
    last_digest_date = today
    log.info("Sent daily digest for %s", today)


def get_updates(offset):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(url, params=params, timeout=35)
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as e:
        log.error("getUpdates failed: %s", e)
        time.sleep(5)
        return []


def command_listener():
    """Runs in its own thread. Long-polls Telegram for incoming messages
    and replies to /today (today only) or /upcoming (today + tomorrow)."""
    offset = None
    log.info("Command listener started (send /today or /upcoming in Telegram anytime).")
    while True:
        updates = get_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message")
            if not msg:
                continue
            text = (msg.get("text") or "").strip().lower()
            chat_id = msg.get("chat", {}).get("id")
            if text.startswith("/today"):
                try:
                    today = datetime.now(IST).date()
                    reply = build_events_message([("Today", today)])
                    send_telegram(reply, chat_id=chat_id)
                    log.info("Replied to /today for chat %s", chat_id)
                except Exception as e:
                    log.error("Error handling /today command: %s", e)
            elif text.startswith("/upcoming"):
                try:
                    today = datetime.now(IST).date()
                    tomorrow = today + timedelta(days=1)
                    reply = build_events_message([("Today", today), ("Tomorrow", tomorrow)])
                    send_telegram(reply, chat_id=chat_id)
                    log.info("Replied to /upcoming for chat %s", chat_id)
                except Exception as e:
                    log.error("Error handling /upcoming command: %s", e)


def main():
    log.info("Starting Forex Factory news watcher.")
    log.info("Check interval: %ds | Digest hour: %d:00 IST", CHECK_INTERVAL_SEC, DIGEST_HOUR_IST)
    send_telegram("✅ News alert bot online. Watching for high-impact (red) events. Send /today or /upcoming anytime.")

    threading.Thread(target=command_listener, daemon=True).start()

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
