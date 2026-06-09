"""Wikipedia quick facts via the free REST summary API (no API key needed).

Looks up the plain-text summary for a query; if the exact title 404s, it runs
an opensearch to find the closest article and retries. Responses are trimmed to
2-3 sentences so they read well aloud.
"""

import re
from urllib.parse import quote

import httpx

SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
OPENSEARCH_URL = "https://en.wikipedia.org/w/api.php"

# Wikipedia asks API clients to send a descriptive User-Agent.
_HEADERS = {"User-Agent": "JARVIS/1.0 (voice assistant)"}


def _shorten(text: str, max_sentences: int = 3) -> str:
    """Collapse whitespace and keep the first ``max_sentences`` sentences."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:max_sentences]).strip()


def _fetch_summary(title: str):
    """Return the plain-text extract for an exact page title, or None on 404."""
    url = SUMMARY_URL.format(title=quote(title.replace(" ", "_"), safe=""))
    resp = httpx.get(url, headers=_HEADERS, timeout=10, follow_redirects=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return (data.get("extract") or "").strip() or None


def _opensearch(query: str):
    """Return the best-matching article title for a free-text query, or None."""
    resp = httpx.get(
        OPENSEARCH_URL,
        params={
            "action": "opensearch",
            "search": query,
            "limit": 1,
            "namespace": 0,
            "format": "json",
        },
        headers=_HEADERS,
        timeout=10,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    # opensearch returns [query, [titles], [descriptions], [urls]].
    titles = data[1] if len(data) > 1 else []
    return titles[0] if titles else None


def search_wikipedia(query: str) -> str:
    """Return a 2-3 sentence plain-text summary of ``query`` for voice."""
    query = (query or "").strip()
    if not query:
        return "What would you like me to look up?"
    try:
        extract = _fetch_summary(query)
        if not extract:
            title = _opensearch(query)
            if title:
                extract = _fetch_summary(title)
        if not extract:
            return f"I couldn't find a Wikipedia article about {query}."
        return _shorten(extract, 3)
    except Exception as exc:  # noqa: BLE001 - fail gracefully
        return f"I couldn't look that up on Wikipedia right now ({exc})."
