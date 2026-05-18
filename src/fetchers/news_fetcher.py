"""
Market news headline fetcher from Economic Times and Moneycontrol RSS feeds.
Headlines are passed to Claude raw — Claude performs sentiment classification
as part of Layer 6 analysis.
"""

from __future__ import annotations
import logging
import requests
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

_FEEDS = {
    "Economic Times": "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
    "ET Markets":     "https://economictimes.indiatimes.com/markets/stocks/rss.cms",
    "Moneycontrol":   "https://www.moneycontrol.com/rss/marketsnews.xml",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _parse_rss(url: str, max_items: int) -> list[str]:
    try:
        resp = requests.get(url, timeout=10, headers=_HEADERS)
        resp.raise_for_status()
        content = resp.content

        # Strip BOM and try to decode/re-encode cleanly for ET feeds
        if content.startswith(b"\xef\xbb\xbf"):
            content = content[3:]

        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # Fallback: try stripping to first valid XML tag
            text = content.decode("utf-8", errors="replace")
            start = text.find("<?xml")
            if start == -1:
                start = text.find("<rss")
            if start > 0:
                text = text[start:]
            root = ET.fromstring(text.encode("utf-8"))

        titles = []
        for item in root.iter("item"):
            el = item.find("title")
            if el is not None and el.text:
                titles.append(el.text.strip())
            if len(titles) >= max_items:
                break
        return titles
    except Exception as exc:
        logger.warning("RSS fetch failed [%s]: %s", url, exc)
        return []


def fetch_market_headlines(max_items: int = 20) -> dict:
    """
    Fetch latest market headlines from ET + Moneycontrol RSS.

    Returns
    -------
    {
        "headlines": [str, ...],   # deduplicated, most recent first
        "sources_ok": [str, ...],  # which sources returned data
    }
    """
    all_headlines: list[str] = []
    sources_ok: list[str]    = []

    per_source = max(max_items // len(_FEEDS), 8)
    for name, url in _FEEDS.items():
        titles = _parse_rss(url, per_source)
        if titles:
            all_headlines.extend(titles)
            sources_ok.append(name)
            logger.info("  %-20s %d headlines", name, len(titles))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for h in all_headlines:
        key = h.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(h)

    if not unique:
        logger.warning("No headlines fetched — check RSS connectivity")

    return {
        "headlines":  unique[:max_items],
        "sources_ok": sources_ok,
    }
