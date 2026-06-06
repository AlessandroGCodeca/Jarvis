"""Lightweight web browsing for JARVIS.

Uses the DuckDuckGo HTML endpoint for search (no API key, no JS) and a small
stdlib HTML-to-text pass for fetching pages. Parsing is done with the standard
library only (no BeautifulSoup) to keep the dependency list minimal.
"""

import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# DuckDuckGo result anchors and snippets.
_RESULT_LINK_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _clean_ddg_url(href: str) -> str:
    """DuckDuckGo wraps result URLs in a redirect; pull out the real target."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return href


def search_web(query: str):
    """Return the top 3 results as dicts with title, snippet, and url."""
    try:
        resp = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=_HEADERS,
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return [{"title": "Search failed", "snippet": str(exc), "url": ""}]

    html_text = resp.text
    links = _RESULT_LINK_RE.findall(html_text)
    snippets = _SNIPPET_RE.findall(html_text)

    results = []
    for i, (href, title_html) in enumerate(links[:3]):
        snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
        results.append(
            {
                "title": _strip_tags(title_html),
                "snippet": snippet,
                "url": _clean_ddg_url(href),
            }
        )
    return results


class _TextExtractor(HTMLParser):
    """Collect visible text, skipping script/style content."""

    def __init__(self):
        super().__init__()
        self._chunks = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def fetch_page(url: str, max_chars: int = 4000) -> str:
    """Fetch a URL and return cleaned, tag-stripped text content."""
    try:
        resp = httpx.get(
            url, headers=_HEADERS, timeout=15, follow_redirects=True
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"Could not fetch page: {exc}"

    parser = _TextExtractor()
    try:
        parser.feed(resp.text)
    except Exception:  # noqa: BLE001 - malformed HTML
        return _strip_tags(resp.text)[:max_chars]

    text = re.sub(r"\s+", " ", parser.get_text()).strip()
    return text[:max_chars]
