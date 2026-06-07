"""The JARVIS brain: Claude with tool-calling.

Maintains a rolling conversation history (last ~20 messages) and runs a
synchronous tool-use loop. Tools bridge to the calendar, mail, notes, web
search, and terminal modules. Designed to be driven from an async server via
``asyncio.to_thread``.
"""

import datetime
import os

import anthropic
from dotenv import load_dotenv

import browser_module
import calendar_module
import briefing_module
import mail_module
import memory
import notes_module
import spotify_module
import system_actions
import weather_module

load_dotenv()

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
MAX_HISTORY = 20

TOOLS = [
    {
        "name": "get_calendar",
        "description": (
            "Get the user's calendar events. Use range 'today' for the next "
            "24 hours or 'week' for the next 7 days."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "range": {
                    "type": "string",
                    "enum": ["today", "week"],
                    "description": "Which window of events to fetch.",
                }
            },
            "required": ["range"],
        },
    },
    {
        "name": "get_emails",
        "description": "Get the user's most recent unread emails.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many unread emails to fetch (default 5).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "create_note",
        "description": "Create a note in the user's Notes app.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title."},
                "body": {"type": "string", "description": "Note body text."},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "search_web",
        "description": (
            "Search the web for current information. Returns the top results "
            "with titles, snippets, and URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command on the user's Mac and return its output. "
            "Has a 10 second timeout."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "spotify_play",
        "description": (
            "Play music on Spotify. Pass a song, artist, album, or playlist "
            "name to search and play it; pass nothing to resume playback."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to play (song/artist/album/playlist).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "spotify_playback",
        "description": (
            "Control Spotify playback or get the current track. Use 'current' "
            "for 'what's playing'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume", "skip", "previous", "current"],
                    "description": "The playback action to perform.",
                }
            },
            "required": ["action"],
        },
    },
    {
        "name": "spotify_volume",
        "description": (
            "Set or adjust Spotify volume. Provide an absolute 'level' (0-100), "
            "or a 'direction' ('up'/'down') to nudge it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                    "description": "Absolute volume, 0-100.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Relative volume change.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_weather",
        "description": (
            "Get the current weather and today's forecast for a city. "
            "Defaults to Prague if no city is given."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name (e.g. 'Prague', 'Tokyo').",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_daily_briefing",
        "description": (
            "Get a daily/morning briefing that combines today's calendar, "
            "unread emails, and the weather. Use this for 'morning briefing', "
            "'daily briefing', 'good morning', or 'what's my day look like'. "
            "Read the result back as one natural, flowing spoken summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City for the weather (default Prague).",
                }
            },
            "required": [],
        },
    },
]


def _format_calendar(events) -> str:
    if isinstance(events, str):
        return events
    if not events:
        return "No events found."
    return "\n".join(f"- {e}" for e in events)


def _format_emails(emails) -> str:
    if isinstance(emails, str):
        return emails
    if not emails:
        return "No unread emails."
    lines = []
    for e in emails:
        lines.append(
            f"- From: {e.get('sender', '?')} | Subject: {e.get('subject', '?')}\n"
            f"  {e.get('preview', '')}"
        )
    return "\n".join(lines)


def _format_web(results) -> str:
    if isinstance(results, str):
        return results
    if not results:
        return "No results found."
    lines = []
    for r in results:
        lines.append(
            f"- {r.get('title', '')}\n  {r.get('snippet', '')}\n  {r.get('url', '')}"
        )
    return "\n".join(lines)


class JarvisBrain:
    """One conversational session with Claude (per WebSocket connection)."""

    def __init__(self):
        # Reads ANTHROPIC_API_KEY from the environment.
        self.client = anthropic.Anthropic()
        self.history = []

    def _system_prompt(self) -> str:
        today = datetime.datetime.now().strftime("%A, %B %d, %Y")
        return (
            "You are JARVIS, a voice assistant. Be concise (1-3 sentences "
            "unless asked for more). You have tools: calendar, email, notes, "
            f"web search, terminal. Current date: {today}"
        )

    def _execute_tool(self, name: str, tool_input: dict) -> str:
        """Dispatch a tool call to the matching module. Always returns text."""
        try:
            if name == "get_calendar":
                rng = (tool_input or {}).get("range", "today")
                if rng == "week":
                    return _format_calendar(calendar_module.get_week_events())
                return _format_calendar(calendar_module.get_today_events())

            if name == "get_emails":
                limit = int((tool_input or {}).get("limit", 5))
                return _format_emails(mail_module.get_unread_emails(limit))

            if name == "create_note":
                title = (tool_input or {}).get("title", "Untitled")
                body = (tool_input or {}).get("body", "")
                result = notes_module.create_note(title, body)
                memory.save_memory(f"Note: {title}\n{body}", tag="note")
                return result

            if name == "search_web":
                query = (tool_input or {}).get("query", "")
                return _format_web(browser_module.search_web(query))

            if name == "run_command":
                command = (tool_input or {}).get("command", "")
                return system_actions.run_command(command)

            if name == "spotify_play":
                return spotify_module.play((tool_input or {}).get("query", ""))

            if name == "spotify_playback":
                action = (tool_input or {}).get("action", "current")
                if action == "pause":
                    return spotify_module.pause()
                if action == "resume":
                    return spotify_module.resume()
                if action == "skip":
                    return spotify_module.skip()
                if action == "previous":
                    return spotify_module.previous()
                return spotify_module.get_current_track()

            if name == "spotify_volume":
                inp = tool_input or {}
                direction = inp.get("direction")
                if direction in ("up", "down"):
                    return spotify_module.adjust_volume(direction)
                return spotify_module.set_volume(inp.get("level", 50))

            if name == "get_weather":
                city = (tool_input or {}).get("city") or "Prague"
                return weather_module.get_weather(city)

            if name == "get_daily_briefing":
                city = (tool_input or {}).get("city") or "Prague"
                return briefing_module.get_daily_briefing(city)

            return f"Unknown tool: {name}"
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            return f"Tool '{name}' failed: {exc}"

    def _trim(self) -> None:
        """Cap history without orphaning a tool_use/tool_result pair."""
        if len(self.history) <= MAX_HISTORY:
            return
        self.history = self.history[-MAX_HISTORY:]
        # Ensure we start on a genuine user text turn, not a dangling
        # assistant turn or a bare tool_result.
        while self.history and not (
            self.history[0]["role"] == "user"
            and isinstance(self.history[0]["content"], str)
        ):
            self.history.pop(0)

    def process(self, user_text: str) -> str:
        """Run one turn (including any tool calls) and return the reply text."""
        self.history.append({"role": "user", "content": user_text})

        # Bound the number of tool iterations to avoid runaway loops.
        for _ in range(8):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=self._system_prompt(),
                tools=TOOLS,
                messages=self.history,
            )

            if response.stop_reason == "tool_use":
                self.history.append(
                    {"role": "assistant", "content": response.content}
                )
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )
                self.history.append({"role": "user", "content": tool_results})
                continue

            # Normal completion.
            text = "".join(
                b.text for b in response.content if b.type == "text"
            ).strip()
            self.history.append({"role": "assistant", "content": text})
            self._trim()
            memory.save_memory(
                f"User: {user_text}\nJARVIS: {text}", tag="conversation"
            )
            return text or "(no response)"

        # Safety net if the tool loop never settled.
        fallback = "I got stuck working through that — could you rephrase?"
        self.history.append({"role": "assistant", "content": fallback})
        self._trim()
        return fallback
