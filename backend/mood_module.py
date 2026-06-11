"""Idle mood engine: ambient phrases when the user goes quiet.

After 3 minutes of inactivity JARVIS speaks one randomly chosen waiting /
time-of-day phrase; after 10 further minutes of silence, one short "still
here" phrase. Each fires at most once per idle period — any user message
resets the cycle. Phrases are delivered through the same broadcast path as
proactive notifications, tagged as ``proactive`` messages.
"""

import asyncio
import random

import time_parser

_FIRST_IDLE_SECONDS = 180  # 3 minutes of inactivity → first phrase
_SECOND_IDLE_SECONDS = 600  # +10 minutes more → "still here"
_POLL_SECONDS = 10

WAITING_PHRASES = [
    "Awaiting your instructions, Sandro.",
    "Systems nominal. Standing by.",
    "All quiet. Ready when you are.",
    "Monitoring... no activity detected.",
    "I'm here whenever you need me.",
]

MORNING_PHRASES = [
    "Good morning. You have a full day ahead.",
    "Morning systems check complete. All nominal.",
]
AFTERNOON_PHRASES = [
    "Afternoon check-in. Everything running smoothly.",
    "Midday systems check. All clear.",
]
EVENING_PHRASES = [
    "Evening, Sandro. Winding down for the day?",
    "The day is almost done. Anything you need before then?",
]
NIGHT_PHRASES = [
    "It's late, Sandro. I'll be here if you need me.",
    "Running night protocols. All systems quiet.",
]

STILL_HERE_PHRASES = [
    "Still here.",
    "Monitoring.",
    "On standby.",
]


class IdleMoodEngine:
    """Speaks an ambient phrase when the user has been idle for a while.

    ``broadcast`` is ``Callable[[str], None]``; ``idle_seconds`` returns how
    long the user has been inactive; ``has_clients`` gates speaking so phrases
    are never produced into the void (or during the frontend boot sequence,
    since connecting resets the idle clock).
    """

    def __init__(self, broadcast, idle_seconds, has_clients=None):
        self._broadcast = broadcast
        self._idle_seconds = idle_seconds
        self._has_clients = has_clients or (lambda: True)
        self._stage = 0  # 0 = armed, 1 = first phrase said, 2 = done
        self._task: asyncio.Task | None = None
        self._running = False

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
    async def _loop(self) -> None:
        try:
            while self._running:
                try:
                    self._cycle()
                except Exception as exc:  # noqa: BLE001 - never crash the loop
                    print(f"[mood] cycle error: {exc}")
                await asyncio.sleep(_POLL_SECONDS)
        except asyncio.CancelledError:
            pass

    def _cycle(self) -> None:
        idle = self._idle_seconds()
        if idle < _FIRST_IDLE_SECONDS:
            self._stage = 0  # user is active again — re-arm for the next lull
            return
        if not self._has_clients():
            return
        if self._stage == 0:
            self._speak(random.choice(self._first_pool()))
            self._stage = 1
        elif (
            self._stage == 1
            and idle >= _FIRST_IDLE_SECONDS + _SECOND_IDLE_SECONDS
        ):
            self._speak(random.choice(STILL_HERE_PHRASES))
            self._stage = 2
        # Stage 2: stay quiet until the user speaks again.

    def _first_pool(self) -> list[str]:
        hour = time_parser.now_prague().hour
        if 6 <= hour < 12:
            time_aware = MORNING_PHRASES
        elif 12 <= hour < 17:
            time_aware = AFTERNOON_PHRASES
        elif 17 <= hour < 21:
            time_aware = EVENING_PHRASES
        else:
            time_aware = NIGHT_PHRASES
        return WAITING_PHRASES + time_aware

    def _speak(self, text: str) -> None:
        try:
            self._broadcast(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[mood] broadcast failed: {exc}")
