"""The JARVIS brain: Claude with tool-calling.

Maintains a rolling conversation history (last ~20 messages) and runs a
synchronous tool-use loop. Tools bridge to the calendar, mail, notes, web
search, and terminal modules. Designed to be driven from an async server via
``asyncio.to_thread``.
"""

import datetime
import json
import os
import time

import anthropic
from dotenv import load_dotenv

import browser_module
import calendar_module
import briefing_module
import currency_module
import documents_module
import files_module
import language_module
import mail_module
import memory
import news_module
import notes_module
import offline_module
import preferences_module
import reminders_module
import spotify_module
import system_actions
import tasks_module
import translation_module
import weather_module
import wiki_module

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
            "Run a shell command on the user's Mac and return its output. Only "
            "an allowlist of safe commands is permitted (open, ls, pwd, echo, "
            "date, whoami, say); anything else is refused. Has a 10 second "
            "timeout. Before calling this, state in your reply what you are "
            "about to run and why."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
                "explanation": {
                    "type": "string",
                    "description": (
                        "A short, plain-language description of what this "
                        "command does and why you are running it. Required as "
                        "a confirmation step before any command runs."
                    ),
                },
            },
            "required": ["command", "explanation"],
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
            "Get the current weather and today's forecast. If no city is "
            "given, the user's location is auto-detected from their IP, so "
            "you can call this with no arguments for 'what's the weather'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": (
                        "Optional city name (e.g. 'Prague', 'Tokyo'). Omit to "
                        "use the auto-detected location."
                    ),
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
    {
        "name": "recall_memory",
        "description": (
            "Search JARVIS's persistent memory of past conversations and notes "
            "for anything relevant to a query. Use this when the user refers to "
            "something from earlier or asks what they told you before."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for in memory.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_recent_memories",
        "description": (
            "Get the most recent things JARVIS has stored in memory "
            "(conversations and notes), newest first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many recent memories to fetch (default 5).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Compose and send an email through the user's Mail app. Confirm the "
            "recipient, subject, and body with the user before sending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Email body text."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "search_notes",
        "description": "Search the user's Notes app for notes whose title matches a query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search note titles for."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_recent_notes",
        "description": "Get the titles of the user's most recently modified notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many recent notes to fetch (default 5).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_news",
        "description": (
            "Get today's top news headlines. Use for 'news', 'headlines', "
            "'what's happening', or 'what's going on in the world'. Optionally "
            "pass a topic to focus the search. Read the result back naturally."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Optional topic to focus on (e.g. 'technology', "
                        "'Czech politics'). Omit for general top stories."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "search_wikipedia",
        "description": (
            "Look up a concise factual summary from Wikipedia. Use for "
            "'what is', 'who is', or 'tell me about' questions where a full web "
            "search would be overkill. Returns 2-3 sentences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The person, place, or thing to look up.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "translate",
        "description": (
            "Translate text between English and Slovak, Italian, Czech, "
            "French, Spanish, German, or Hungarian. Use for 'how do you say', "
            "'translate', or 'in Slovak/Italian/...'. To translate something "
            "INTO English, set source_language to the original language."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to translate."},
                "target_language": {
                    "type": "string",
                    "description": "Language to translate into (e.g. 'Slovak').",
                },
                "source_language": {
                    "type": "string",
                    "description": (
                        "Language the text is in (default 'English'). Set this "
                        "when translating into English."
                    ),
                },
            },
            "required": ["text", "target_language"],
        },
    },
    {
        "name": "set_system_volume",
        "description": (
            "Control the Mac's system output volume. Provide 'level' (0-100) to "
            "set it, 'direction' ('up'/'down') to nudge it by 10, or 'mute' "
            "(true to mute, false to unmute). This is the whole computer's "
            "volume, distinct from Spotify's."
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
                    "description": "Relative volume change (±10).",
                },
                "mute": {
                    "type": "boolean",
                    "description": "True to mute, false to unmute.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "set_brightness",
        "description": (
            "Control display brightness. Provide 'level' (0-100) for an exact "
            "brightness, or 'direction' ('up'/'down') for 'brighter'/'dimmer'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                    "description": "Absolute brightness, 0-100.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Relative brightness change.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "enable_focus_mode",
        "description": (
            "Turn on Do Not Disturb / Focus mode. Optionally pass 'minutes' to "
            "be reminded when the session ends. Use for 'focus mode', 'do not "
            "disturb', or 'focus for X minutes'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {
                    "type": "integer",
                    "description": "Optional length of the focus session.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "disable_focus_mode",
        "description": "Turn off Do Not Disturb / Focus mode.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "start_timer",
        "description": (
            "Start a timer. JARVIS will speak a notification when it's done. "
            "Use for 'start a X minute timer' or 'timer'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {
                    "type": "integer",
                    "description": "How long the timer should run, in minutes.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional name for the timer (e.g. 'tea').",
                },
            },
            "required": ["minutes"],
        },
    },
    {
        "name": "pomodoro",
        "description": (
            "Start a pomodoro cycle: 25 minutes of focus then a 5-minute "
            "break, with a spoken announcement at each phase."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "convert_currency",
        "description": (
            "Convert an amount between currencies (EUR, CZK, USD, GBP, HUF, "
            "PLN, CHF). Accepts names like 'euros' or 'crowns'. Use for "
            "'convert', 'how much is', or 'X in euros/crowns/dollars'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "The amount to convert.",
                },
                "from_currency": {
                    "type": "string",
                    "description": "Currency to convert from (code or name).",
                },
                "to_currency": {
                    "type": "string",
                    "description": "Currency to convert to (code or name).",
                },
            },
            "required": ["amount", "from_currency", "to_currency"],
        },
    },
    {
        "name": "get_exchange_rate",
        "description": (
            "Get the current exchange rate between two currencies, without an "
            "amount. Use for 'what's the exchange rate'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_currency": {
                    "type": "string",
                    "description": "Currency to convert from (code or name).",
                },
                "to_currency": {
                    "type": "string",
                    "description": "Currency to convert to (code or name).",
                },
            },
            "required": ["from_currency", "to_currency"],
        },
    },
    {
        "name": "open_app",
        "description": (
            "Open / launch a Mac application by name. Understands common names "
            "like 'vscode', 'chrome', 'word'. Use for 'open', 'launch', 'start'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The application to open (e.g. 'Spotify').",
                }
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "close_app",
        "description": (
            "Quit / close a Mac application by name. Use for 'close', 'quit'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The application to close.",
                }
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "switch_to_app",
        "description": (
            "Bring a Mac application to the foreground. Use for 'switch to'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The application to switch to.",
                }
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "list_running_apps",
        "description": "List the user's currently open (foreground) apps.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "find_file",
        "description": (
            "Find files by name using macOS Spotlight. Optionally pass a "
            "time_range (e.g. 7, 'last week') to limit to recently created "
            "files. Use for 'find', 'where is', 'find my CV'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to match in the file name.",
                },
                "time_range": {
                    "type": "string",
                    "description": (
                        "Optional recency window, e.g. '7', 'week', "
                        "'last week'."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_recent_files",
        "description": (
            "Find files created recently, optionally filtered by type "
            "('documents', 'images', 'pdfs'). Use for 'find last week's "
            "presentation' or 'recent downloads'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_type": {
                    "type": "string",
                    "description": "Optional: 'documents', 'images', or 'pdfs'.",
                },
                "days": {
                    "type": "integer",
                    "description": "How many days back to look (default 7).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_by_content",
        "description": (
            "Search inside files (full-content Spotlight search) for a phrase. "
            "Use when looking for a file by what it contains, not its name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for inside files.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_file",
        "description": "Open a file in its default app, given its full path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Full path of the file to open.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "set_preference",
        "description": (
            "Remember a user preference across sessions. Call this when the "
            "user states a preference, e.g. 'my name is Sandro' -> "
            "set_preference('name', 'Sandro'); 'always give me weather in "
            "Celsius' -> set_preference('weather_unit', 'celsius'); 'I prefer "
            "Slovak' -> set_preference('language', 'sk'). Known keys: name, "
            "language, weather_unit, weather_city, news_topics, volume_level, "
            "wake_time, currency_from, currency_to. After saving, confirm "
            "briefly (e.g. 'Got it, I'll remember that')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Preference name."},
                "value": {"type": "string", "description": "Preference value."},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "get_preference",
        "description": "Look up a stored user preference by key.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Preference name."}
            },
            "required": ["key"],
        },
    },
    {
        "name": "create_event",
        "description": (
            "Create a calendar event. Accepts natural date/time like "
            "'tomorrow at 3pm' or 'next Monday at 10:00'. Use for 'add to "
            "calendar', 'schedule', 'create event'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title."},
                "date": {
                    "type": "string",
                    "description": "Date phrase, e.g. 'tomorrow', 'June 10 2026'.",
                },
                "time": {
                    "type": "string",
                    "description": "Time phrase, e.g. '3pm', '10:00'.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Length in minutes (default 60).",
                },
                "notes": {"type": "string", "description": "Optional notes."},
            },
            "required": ["title", "date"],
        },
    },
    {
        "name": "update_event",
        "description": (
            "Move or edit an existing calendar event found by title. Use for "
            "'move meeting', 'reschedule', 'change my appointment'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title_or_id": {
                    "type": "string",
                    "description": "Title (or part) of the event to update.",
                },
                "new_title": {"type": "string", "description": "New title."},
                "new_date": {"type": "string", "description": "New date phrase."},
                "new_time": {"type": "string", "description": "New time phrase."},
                "new_notes": {"type": "string", "description": "New notes."},
            },
            "required": ["title_or_id"],
        },
    },
    {
        "name": "delete_event",
        "description": (
            "Delete a calendar event by title. Provide the date when possible "
            "to avoid deleting the wrong one. Use for 'cancel appointment', "
            "'delete event'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title."},
                "date": {
                    "type": "string",
                    "description": "Date of the event (recommended).",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "create_reminder",
        "description": (
            "Create a reminder in the Reminders app, optionally with a due "
            "date/time and priority (low/medium/high). Use for 'remind me', "
            "'set a reminder'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Reminder text."},
                "due_date": {"type": "string", "description": "Due date phrase."},
                "due_time": {"type": "string", "description": "Due time phrase."},
                "notes": {"type": "string", "description": "Optional notes."},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Priority (default medium).",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_reminders",
        "description": "List pending reminders. Use for 'what are my reminders'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "Optional Reminders list to filter by.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "complete_reminder",
        "description": "Mark a reminder as done. Use for 'mark as done'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Reminder to complete."}
            },
            "required": ["title"],
        },
    },
    {
        "name": "create_text_file",
        "description": (
            "Create a plain .txt file (Desktop by default). Use for 'write a "
            "file', 'save this as'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "File name."},
                "content": {"type": "string", "description": "File contents."},
                "location": {
                    "type": "string",
                    "enum": ["Desktop", "Documents", "Downloads"],
                    "description": "Where to save (default Desktop).",
                },
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "create_markdown_note",
        "description": "Create a Markdown (.md) note on the Desktop and open it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title/filename."},
                "content": {"type": "string", "description": "Markdown body."},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "create_word_doc",
        "description": "Create a Word (.docx) document on the Desktop.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "File name."},
                "content": {"type": "string", "description": "Document text."},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file's contents (searches Desktop/Documents/Downloads by "
            "name if no full path). Use for 'read my file', 'open my document'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path_or_name": {
                    "type": "string",
                    "description": "Full path or file name to read.",
                }
            },
            "required": ["path_or_name"],
        },
    },
    {
        "name": "append_to_file",
        "description": "Append text to an existing file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "File path or name."},
                "content": {"type": "string", "description": "Text to append."},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "add_task",
        "description": (
            "Add a task (to the JARVIS Tasks list + local store) with optional "
            "due date and priority. Use for 'add task', 'todo'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title."},
                "due_date": {"type": "string", "description": "Due date phrase."},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Priority (default medium).",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_tasks",
        "description": (
            "List pending tasks sorted by priority. Use for 'what are my "
            "tasks', 'what's pending', 'task list'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "complete_task",
        "description": "Mark a task as done. Use for 'mark task done', 'complete task'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task to complete."}
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_overdue_tasks",
        "description": "List tasks that are past their due date.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_connectivity",
        "description": (
            "Report whether JARVIS is currently online or offline."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
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


def _format_list(items, empty="Nothing found.") -> str:
    """Format a list-of-strings tool result (notes, etc.), or pass through a str."""
    if isinstance(items, str):
        return items
    if not items:
        return empty
    return "\n".join(f"- {i}" for i in items)


def _format_memories(rows) -> str:
    """Format memory rows (dicts with 'content') into a readable list."""
    if isinstance(rows, str):
        return rows
    if not rows:
        return "No matching memories."
    lines = []
    for r in rows:
        content = (r.get("content") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"- {content}")
    return "\n".join(lines) if lines else "No matching memories."


def _format_reminders(items) -> str:
    """Format reminder dicts (or an error string) into spoken text."""
    if isinstance(items, str):
        return items
    if not items:
        return "You have no pending reminders."
    lines = []
    for r in items:
        bit = r.get("title", "untitled")
        if r.get("due"):
            bit += f" (due {r['due']})"
        lines.append(bit)
    noun = "reminder" if len(lines) == 1 else "reminders"
    return f"You have {len(lines)} {noun}: " + "; ".join(lines) + "."


# Upcoming-events cache for the per-turn context block (calendar reads are slow).
_CTX_CACHE = {"events": None, "ts": 0.0}
_CTX_TTL = 300  # seconds


class JarvisBrain:
    """One conversational session with Claude (per WebSocket connection)."""

    def __init__(self, notifier=None):
        # Reads ANTHROPIC_API_KEY from the environment.
        self.client = anthropic.Anthropic()
        self.history = []
        # Optional callable (delay_seconds, text) used by timers / focus mode to
        # schedule a spoken notification back to the client. Set by the server
        # per WebSocket connection; None means timers can't fire.
        self.notifier = notifier
        # Persistent user preferences (name, language, units, etc.).
        self.preferences = preferences_module.get_all_preferences()
        # Language handling. ``locked_language`` (when set) forces every reply
        # into that language; otherwise the language is auto-detected per turn.
        # A previously-stored non-English language preference acts as a lock so
        # the choice survives across sessions. ``language`` is the effective
        # code for the latest turn, read by the server for TTS.
        pref_lang = language_module.normalize_language(
            self.preferences.get("language")
        )
        self.locked_language = pref_lang if pref_lang and pref_lang != "en" else None
        self.language = self.locked_language or "en"

    def _system_prompt(
        self,
        memories: str = "",
        language_instruction: str = "",
        context_block: str = "",
    ) -> str:
        today = datetime.datetime.now().strftime("%A, %B %d, %Y")
        prompt = (
            "You are JARVIS, a voice assistant. Be concise (1-3 sentences "
            "unless asked for more). You have access to: calendar, email, "
            "notes, web search, terminal (restricted to a safe allowlist), "
            "Spotify control, weather, daily briefing, and a persistent memory "
            "you can search. You can also: get news headlines, look up "
            "Wikipedia facts, translate languages (Slovak, Italian, Czech, "
            "French, Spanish, German, Hungarian), control system volume and "
            "brightness, start focus/Do Not Disturb mode and timers, "
            "auto-detect the user's location for weather, convert currencies, "
            "open/close/switch between apps, find files using Spotlight "
            "search, respond in the user's language, and remember user "
            "preferences across sessions. You can now also: create, modify and "
            "delete calendar events, set reminders with due dates, create "
            "documents and Word files, manage task lists with priorities, and "
            "work offline for local functions. Before running a terminal "
            f"command, briefly say what you're about to do. Current date: {today}."
        )

        prefs_json = json.dumps(self.preferences, ensure_ascii=False)
        prompt += (
            f"\n\nUser preferences (JSON): {prefs_json}. Always use these as "
            "defaults unless the user specifies otherwise. If you learn a new "
            "preference from the conversation (their name, preferred language, "
            "default city, units, etc.), call set_preference to remember it, "
            "then confirm briefly."
        )

        if context_block:
            prompt += "\n\n" + context_block

        if offline_module.is_offline():
            prompt += (
                "\n\nIMPORTANT: You are currently offline. Only use tools that "
                "work offline (calendar, reminders, notes, tasks, files, "
                "volume/brightness, memory, app launcher, file finder). Do not "
                "attempt web search, fresh weather, news, or Wikipedia. For "
                "offline-unavailable requests, explain clearly and offer "
                "local alternatives."
            )

        if language_instruction:
            prompt += "\n\n" + language_instruction

        if memories:
            prompt += (
                "\n\nRelevant memories from earlier (use only if helpful):\n"
                + memories
            )
        return prompt

    def _recall_block(self, query: str) -> str:
        """Search memory for ``query`` and return the top 3 hits as text."""
        try:
            hits = memory.search_memory(query)
        except Exception:  # noqa: BLE001 - memory is best-effort context
            return ""
        lines = []
        for h in hits[:3]:
            content = (h.get("content") or "").strip().replace("\n", " ")
            if len(content) > 200:
                content = content[:200] + "…"
            if content:
                lines.append(f"- {content}")
        return "\n".join(lines)

    def _offline_tool(self, name: str, tool_input: dict):
        """Offline handling for internet-dependent tools. Returns text, or None
        to let the normal (local) dispatch proceed."""
        inp = tool_input or {}
        if name == "search_web":
            return "Web search is unavailable while you're offline."
        if name == "get_news":
            return "Unable to fetch news — you're offline."
        if name == "search_wikipedia":
            return "Unable to search Wikipedia — you're offline."
        if name == "get_weather":
            cached = offline_module.recall("weather")
            return (
                f"⚠ Offline — showing last known weather: {cached}"
                if cached
                else "Weather is unavailable while you're offline."
            )
        if name in ("convert_currency", "get_exchange_rate"):
            cached = offline_module.recall("currency")
            return (
                f"⚠ Offline — using last known rate: {cached}"
                if cached
                else "Currency conversion is unavailable while you're offline."
            )
        if name == "translate":
            code = language_module.normalize_language(
                inp.get("target_language", "")
            )
            phrase = (
                offline_module.offline_translate(inp.get("text", ""), code)
                if code
                else None
            )
            if phrase:
                name_map = language_module.LANGUAGE_NAMES.get(code, "that language")
                return f'In {name_map}, that\'s "{phrase}" (offline phrase book).'
            return "Translation is unavailable offline, except common phrases."
        return None  # not an internet-only tool — proceed normally

    def _build_context(self) -> str:
        """Build a rich per-turn context block for the system prompt."""
        now = datetime.datetime.now()
        hour = now.hour
        if hour < 12:
            tod = "morning"
        elif hour < 17:
            tod = "afternoon"
        elif hour < 21:
            tod = "evening"
        else:
            tod = "night"

        # Upcoming events (cached ~5 min since calendar reads are slow).
        events = _CTX_CACHE["events"]
        if events is None or (time.time() - _CTX_CACHE["ts"]) > _CTX_TTL:
            try:
                raw = calendar_module.get_today_events()
                events = raw[:2] if isinstance(raw, list) else []
            except Exception:  # noqa: BLE001
                events = []
            _CTX_CACHE["events"] = events
            _CTX_CACHE["ts"] = time.time()
        upcoming = "; ".join(events) if events else "nothing on the calendar"

        # Pending high-priority task count (local SQLite, fast).
        try:
            high = sum(
                1
                for t in tasks_module.get_all_tasks()
                if t.get("priority") == "high"
            )
        except Exception:  # noqa: BLE001
            high = 0

        location = self.preferences.get("weather_city", "Prague")
        lines = [
            f"Current context: It's {tod} on {now.strftime('%A %H:%M')}.",
            f"Upcoming: {upcoming}.",
            f"{high} high-priority task(s) pending.",
            f"User is currently in {location}.",
            f"Connectivity: {offline_module.status_label()}.",
            "Use this context to give proactive, relevant suggestions. If it's "
            "morning, offer a briefing; if a meeting is within 30 minutes, "
            "mention it; in the evening use a relaxed tone; after 10pm keep "
            "replies short and quieter, and suggest rest if appropriate.",
        ]
        return "\n".join(lines)

    def _execute_tool(self, name: str, tool_input: dict) -> str:
        """Dispatch a tool call to the matching module. Always returns text."""
        try:
            # Offline degradation: short-circuit tools that need the internet,
            # serving cached values or a clear explanation where possible.
            if offline_module.is_offline():
                offline_result = self._offline_tool(name, tool_input)
                if offline_result is not None:
                    return offline_result

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
                # None city -> auto-detect the user's location.
                city = (tool_input or {}).get("city")
                result = weather_module.get_weather(city)
                offline_module.remember("weather", result)  # cache for offline
                return result

            if name == "get_daily_briefing":
                city = (tool_input or {}).get("city") or "Prague"
                return briefing_module.get_daily_briefing(city)

            if name == "recall_memory":
                query = (tool_input or {}).get("query", "")
                return _format_memories(memory.search_memory(query))

            if name == "get_recent_memories":
                limit = int((tool_input or {}).get("limit", 5))
                return _format_memories(memory.get_recent(limit))

            if name == "create_event":
                inp = tool_input or {}
                return calendar_module.create_event(
                    inp.get("title", "Untitled"),
                    inp.get("date", ""),
                    inp.get("time", ""),
                    int(inp.get("duration_minutes", inp.get("duration", 60))),
                    calendar=inp.get("calendar"),
                    notes=inp.get("notes"),
                )

            if name == "update_event":
                inp = tool_input or {}
                return calendar_module.update_event(
                    inp.get("title_or_id", ""),
                    new_title=inp.get("new_title"),
                    new_date=inp.get("new_date"),
                    new_time=inp.get("new_time"),
                    new_notes=inp.get("new_notes"),
                )

            if name == "delete_event":
                inp = tool_input or {}
                return calendar_module.delete_event(
                    inp.get("title", ""), inp.get("date")
                )

            if name == "create_reminder":
                inp = tool_input or {}
                return reminders_module.create_reminder(
                    inp.get("title", ""),
                    due_date=inp.get("due_date"),
                    due_time=inp.get("due_time"),
                    notes=inp.get("notes"),
                    priority=inp.get("priority", "medium"),
                )

            if name == "get_reminders":
                items = reminders_module.get_reminders(
                    (tool_input or {}).get("list_name")
                )
                return _format_reminders(items)

            if name == "complete_reminder":
                return reminders_module.complete_reminder(
                    (tool_input or {}).get("title", "")
                )

            if name == "create_text_file":
                inp = tool_input or {}
                return documents_module.create_text_file(
                    inp.get("filename", "untitled"),
                    inp.get("content", ""),
                    inp.get("location", "Desktop"),
                )

            if name == "create_markdown_note":
                inp = tool_input or {}
                return documents_module.create_markdown_note(
                    inp.get("title", "untitled"), inp.get("content", "")
                )

            if name == "create_word_doc":
                inp = tool_input or {}
                return documents_module.create_word_doc(
                    inp.get("filename", "untitled"), inp.get("content", "")
                )

            if name == "read_file":
                return documents_module.read_file(
                    (tool_input or {}).get("path_or_name", "")
                )

            if name == "append_to_file":
                inp = tool_input or {}
                return documents_module.append_to_file(
                    inp.get("filename", ""), inp.get("content", "")
                )

            if name == "add_task":
                inp = tool_input or {}
                return tasks_module.add_task(
                    inp.get("title", ""),
                    due_date=inp.get("due_date"),
                    priority=inp.get("priority", "medium"),
                )

            if name == "get_tasks":
                return tasks_module.format_tasks(tasks_module.get_all_tasks())

            if name == "complete_task":
                return tasks_module.complete_task(
                    (tool_input or {}).get("title", "")
                )

            if name == "get_overdue_tasks":
                overdue = tasks_module.get_overdue_tasks()
                if not overdue:
                    return "You have no overdue tasks."
                return "Overdue: " + tasks_module.format_tasks(overdue)

            if name == "check_connectivity":
                return f"You are currently {offline_module.status_label()}."

            if name == "send_email":
                inp = tool_input or {}
                return mail_module.send_email(
                    inp.get("to", ""),
                    inp.get("subject", ""),
                    inp.get("body", ""),
                )

            if name == "search_notes":
                query = (tool_input or {}).get("query", "")
                return _format_list(
                    notes_module.search_notes(query), empty="No matching notes."
                )

            if name == "get_recent_notes":
                limit = int((tool_input or {}).get("limit", 5))
                return _format_list(
                    notes_module.get_recent_notes(limit), empty="No notes found."
                )

            if name == "get_news":
                topic = (tool_input or {}).get("topic")
                return news_module.get_news(topic)

            if name == "search_wikipedia":
                query = (tool_input or {}).get("query", "")
                return wiki_module.search_wikipedia(query)

            if name == "translate":
                inp = tool_input or {}
                return translation_module.translate(
                    inp.get("text", ""),
                    inp.get("target_language", ""),
                    inp.get("source_language", "English"),
                )

            if name == "set_system_volume":
                inp = tool_input or {}
                if "mute" in inp and inp.get("mute") is not None:
                    return (
                        system_actions.mute()
                        if inp.get("mute")
                        else system_actions.unmute()
                    )
                direction = inp.get("direction")
                if direction == "up":
                    return system_actions.volume_up()
                if direction == "down":
                    return system_actions.volume_down()
                if inp.get("level") is not None:
                    return system_actions.set_volume(inp.get("level"))
                return "Tell me a volume level, a direction, or to mute."

            if name == "set_brightness":
                inp = tool_input or {}
                direction = inp.get("direction")
                if direction == "up":
                    return system_actions.brighter()
                if direction == "down":
                    return system_actions.dimmer()
                if inp.get("level") is not None:
                    return system_actions.set_brightness(inp.get("level"))
                return "Tell me a brightness level or a direction."

            if name == "enable_focus_mode":
                minutes = (tool_input or {}).get("minutes")
                return system_actions.enable_focus_mode(
                    minutes, notifier=self.notifier
                )

            if name == "disable_focus_mode":
                return system_actions.disable_focus_mode()

            if name == "start_timer":
                inp = tool_input or {}
                return system_actions.start_timer(
                    inp.get("minutes"),
                    inp.get("label", "Focus session"),
                    notifier=self.notifier,
                )

            if name == "pomodoro":
                return system_actions.pomodoro(notifier=self.notifier)

            if name == "convert_currency":
                inp = tool_input or {}
                result = currency_module.convert_currency(
                    inp.get("amount"),
                    inp.get("from_currency", ""),
                    inp.get("to_currency", ""),
                )
                offline_module.remember("currency", result)  # cache for offline
                return result

            if name == "get_exchange_rate":
                inp = tool_input or {}
                return currency_module.get_exchange_rate(
                    inp.get("from_currency", ""),
                    inp.get("to_currency", ""),
                )

            if name == "open_app":
                return system_actions.open_app(
                    (tool_input or {}).get("app_name", "")
                )

            if name == "close_app":
                return system_actions.close_app(
                    (tool_input or {}).get("app_name", "")
                )

            if name == "switch_to_app":
                return system_actions.switch_to_app(
                    (tool_input or {}).get("app_name", "")
                )

            if name == "list_running_apps":
                return system_actions.list_running_apps()

            if name == "find_file":
                inp = tool_input or {}
                return files_module.find_file(
                    inp.get("query", ""), inp.get("time_range")
                )

            if name == "find_recent_files":
                inp = tool_input or {}
                return files_module.find_recent_files(
                    inp.get("file_type"), inp.get("days", 7)
                )

            if name == "find_by_content":
                return files_module.find_by_content(
                    (tool_input or {}).get("query", "")
                )

            if name == "open_file":
                return files_module.open_file((tool_input or {}).get("path", ""))

            if name == "set_preference":
                inp = tool_input or {}
                key = inp.get("key", "")
                value = inp.get("value")
                # Setting the language preference also locks the session so the
                # choice persists and overrides per-turn auto-detection.
                if key == "language":
                    code = language_module.normalize_language(value) or value
                    value = code
                    self.locked_language = code
                    self.language = code
                return preferences_module.set_preference(key, value)

            if name == "get_preference":
                key = (tool_input or {}).get("key", "")
                val = preferences_module.get_preference(key)
                if val is None:
                    return f"No preference is set for '{key}'."
                return f"{key}: {val}"

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

        # Refresh preferences each turn so a mid-session set_preference is
        # reflected in the prompt going forward.
        self.preferences = preferences_module.get_all_preferences()

        # Determine the language for this turn: a locked language (explicit
        # request or stored preference) wins; otherwise auto-detect from the
        # message. Store it so the server can pass the right TTS language code.
        detected = language_module.detect_language(user_text)
        effective = self.locked_language or detected
        self.language = effective
        language_instruction = ""
        if effective != "en" or self.locked_language:
            lang_name = language_module.LANGUAGE_NAMES.get(effective, "English")
            language_instruction = (
                f"The user is speaking in {lang_name}. Respond entirely in "
                f"{lang_name}. Do not switch to English unless explicitly asked."
            )

        # Pull any relevant past memories once and fold them into the system
        # prompt for this turn so JARVIS can actually recall earlier context,
        # along with a rich context block (time, upcoming events, tasks, ...).
        context_block = self._build_context()
        system = self._system_prompt(
            self._recall_block(user_text), language_instruction, context_block
        )

        # Bound the number of tool iterations to avoid runaway loops.
        for _ in range(8):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system,
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
