"""Proactive notification engine for JARVIS.

Runs as a background asyncio task that, once a minute, checks for imminent
calendar events and due reminders (and, less often, overdue tasks and trending
news) and broadcasts spoken notifications to connected clients. A per-item
cache ensures the same thing is never announced twice. Every check is wrapped so
a failure in one source never crashes the loop or the server.
"""

import asyncio
import datetime
import time

import calendar_module
import news_module
import offline_module
import preferences_module
import reminders_module
import tasks_module

_CYCLE_SECONDS = 60
_NEWS_INTERVAL = 1800  # 30 minutes
_NOTIFY_TTL = 6 * 3600  # forget a notified item after 6 hours


class NotificationEngine:
    """Periodically scans local sources and broadcasts proactive messages.

    ``broadcast`` is ``Callable[[str, str], None]`` -> (text, priority).
    ``idle_seconds`` (optional) returns how long the user has been idle, used to
    gate low-priority news.
    """

    def __init__(self, broadcast, idle_seconds=None):
        self._broadcast = broadcast
        self._idle_seconds = idle_seconds or (lambda: 0.0)
        self._notified: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_news = 0.0
        self._last_overdue_day: str | None = None

    def start(self) -> None:
        if self._task is None:
            self._running = True
            self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # ---- internals ----
    def _seen(self, key: str) -> bool:
        """True if ``key`` was already notified recently; records it otherwise."""
        now = time.time()
        # Prune stale entries so the cache can't grow without bound.
        for k, ts in list(self._notified.items()):
            if now - ts > _NOTIFY_TTL:
                del self._notified[k]
        if key in self._notified:
            return True
        self._notified[key] = now
        return False

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(12)  # let startup settle before the first scan
            while self._running:
                try:
                    await self._cycle()
                except Exception as exc:  # noqa: BLE001 - never crash the loop
                    print(f"[notifications] cycle error: {exc}")
                await asyncio.sleep(_CYCLE_SECONDS)
        except asyncio.CancelledError:
            pass

    async def _cycle(self) -> None:
        await self._check_calendar()
        await self._check_reminders()
        await self._check_overdue_tasks()
        await self._check_news()

    async def _check_calendar(self) -> None:
        try:
            events = await asyncio.to_thread(calendar_module.get_upcoming_events, 10)
        except Exception as exc:  # noqa: BLE001
            print(f"[notifications] calendar check failed: {exc}")
            return
        for ev in events:
            title = ev.get("title", "an event")
            mins = ev.get("minutes_until", 0)
            key = f"cal:{title}:{ev.get('start', '')}"
            if self._seen(key):
                continue
            priority = "urgent" if mins <= 5 else "normal"
            self._emit(
                f"Heads up — you have {title} in {max(mins, 1)} minutes.",
                priority,
            )

    async def _check_reminders(self) -> None:
        try:
            due = await asyncio.to_thread(reminders_module.get_due_within, 10)
        except Exception as exc:  # noqa: BLE001
            print(f"[notifications] reminder check failed: {exc}")
            return
        for r in due:
            title = r.get("title", "a reminder")
            if self._seen(f"rem:{title}"):
                continue
            self._emit(f"Reminder: {title}.", "normal")

    async def _check_overdue_tasks(self) -> None:
        # Morning summary only, once per day, in the 8–9am window.
        now = datetime.datetime.now()
        if now.hour != 8:
            return
        today = now.strftime("%Y-%m-%d")
        if self._last_overdue_day == today:
            return
        try:
            overdue = await asyncio.to_thread(tasks_module.get_overdue_tasks)
        except Exception as exc:  # noqa: BLE001
            print(f"[notifications] overdue check failed: {exc}")
            return
        self._last_overdue_day = today
        if overdue:
            titles = ", ".join(t.get("title", "a task") for t in overdue[:5])
            self._emit(
                f"Good morning. You have {len(overdue)} overdue "
                f"task(s): {titles}.",
                "normal",
            )

    async def _check_news(self) -> None:
        # Low priority: only when online, the user has news topics set, JARVIS
        # has been idle >10 min, and at most every 30 minutes.
        if offline_module.is_offline():
            return
        if time.time() - self._last_news < _NEWS_INTERVAL:
            return
        if self._idle_seconds() < 600:
            return
        try:
            prefs = preferences_module.get_all_preferences()
        except Exception:  # noqa: BLE001
            return
        topics = prefs.get("news_topics")
        if not topics:
            return
        topic = topics[0] if isinstance(topics, list) and topics else str(topics)
        try:
            headlines = await asyncio.to_thread(news_module.scrape_headlines, topic)
        except Exception as exc:  # noqa: BLE001
            print(f"[notifications] news check failed: {exc}")
            return
        self._last_news = time.time()
        if headlines:
            top = headlines[0].get("title", "")
            if top and not self._seen(f"news:{top}"):
                self._emit(f"Heads up — {top} is trending.", "low")

    def _emit(self, text: str, priority: str) -> None:
        try:
            self._broadcast(text, priority)
        except Exception as exc:  # noqa: BLE001
            print(f"[notifications] broadcast failed: {exc}")
