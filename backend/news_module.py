"""News headlines via RSS feeds (BBC News + optional topic search).

Fetches feeds with httpx and parses them with the standard library's
``xml.etree.ElementTree`` — no extra dependencies. Everything is wrapped so a
network or parse error degrades to a friendly spoken message instead of raising.
"""

import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx

BBC_RSS = "https://feeds.bbci.co.uk/news/rss.xml"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}"

# Some feeds reject requests without a real-looking User-Agent.
_HEADERS = {"User-Agent": "JARVIS/1.0 (voice assistant)"}


def _parse_rss(xml_bytes, default_source: str, limit: int = 5):
    """Parse RSS ``<item>`` elements into {title, source, url} dicts.

    Takes raw bytes (not str): RSS feeds carry an XML encoding declaration, and
    ``ET.fromstring`` rejects a ``str`` that declares an encoding.
    """
    items = []
    root = ET.fromstring(xml_bytes)
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        source_el = item.find("source")
        title = (title_el.text or "").strip() if title_el is not None else ""
        url = (link_el.text or "").strip() if link_el is not None else ""
        source = default_source
        if source_el is not None and (source_el.text or "").strip():
            source = source_el.text.strip()
        if title:
            items.append({"title": title, "source": source, "url": url})
        if len(items) >= limit:
            break
    return items


def scrape_headlines(topic: str | None = None):
    """Return up to ~5 top headlines as {title, source, url} dicts.

    Always pulls BBC News' front page; when ``topic`` is given, also queries
    Google News for that topic and lists those (more relevant) stories first.
    """
    headlines = []

    if topic:
        try:
            url = GOOGLE_NEWS_RSS.format(query=quote_plus(topic))
            resp = httpx.get(
                url, headers=_HEADERS, timeout=10, follow_redirects=True
            )
            resp.raise_for_status()
            headlines.extend(_parse_rss(resp.content, "Google News", limit=5))
        except Exception:  # noqa: BLE001 - fall back to BBC only
            pass

    try:
        resp = httpx.get(
            BBC_RSS, headers=_HEADERS, timeout=10, follow_redirects=True
        )
        resp.raise_for_status()
        headlines.extend(_parse_rss(resp.content, "BBC News", limit=5))
    except Exception:  # noqa: BLE001 - topic results may still be present
        pass

    return headlines


def get_news(topic: str | None = None) -> str:
    """Return a natural-language summary of today's top stories."""
    topic = (topic or "").strip() or None
    try:
        headlines = scrape_headlines(topic)
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"I couldn't fetch the news right now ({exc})."

    if not headlines:
        return "I couldn't reach the news feeds right now."

    # De-duplicate by title (Google + BBC can overlap) and cap at five.
    seen = set()
    top = []
    for h in headlines:
        key = h["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        top.append(h)
        if len(top) >= 5:
            break

    intro = (
        f"Here are today's top stories about {topic}: "
        if topic
        else "Here are today's top stories: "
    )
    lines = [f"{i}. {h['title']} ({h['source']})." for i, h in enumerate(top, 1)]
    return intro + " ".join(lines)
