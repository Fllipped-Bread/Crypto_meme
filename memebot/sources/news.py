"""News & social: crypto RSS feeds (free), CryptoPanic (optional free token), and X API v2
mention counts (optional bearer token, paid X plan)."""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET

from ..net import health, http


def rss_headlines(feeds: list[str]) -> list[dict]:
    out = []
    for url in feeds:
        try:
            root = ET.fromstring(http.get(url, raw=True))
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                if title:
                    out.append({"title": title, "link": (item.findtext("link") or "").strip(),
                                "source": url.split("/")[2], "ts": time.time()})
            health.ok("news_rss")
        except Exception as e:  # noqa: BLE001
            health.fail("news_rss", f"{url}: {e}")
    return out


def cryptopanic(token: str) -> list[dict]:
    if not token:
        return []
    try:
        data = http.get("https://cryptopanic.com/api/v1/posts/",
                        params={"auth_token": token, "public": "true", "filter": "hot"})
        health.ok("cryptopanic")
        return [{"title": p.get("title", ""), "link": p.get("url", ""), "source": "cryptopanic",
                 "ts": time.time()} for p in data.get("results", [])]
    except Exception as e:  # noqa: BLE001
        health.fail("cryptopanic", e)
        return []


def x_mentions(symbol: str, bearer: str) -> int | None:
    """Tweets mentioning $SYMBOL in the last hour (X API v2 counts endpoint)."""
    if not bearer or not symbol:
        return None
    try:
        data = http.get("https://api.x.com/2/tweets/counts/recent",
                        params={"query": f"${symbol} -is:retweet", "granularity": "hour"},
                        headers={"Authorization": f"Bearer {bearer}"})
        health.ok("x_api")
        series = data.get("data") or []
        return int(series[-1]["tweet_count"]) if series else 0
    except Exception as e:  # noqa: BLE001
        health.fail("x_api", e)
        return None


def mentions(headlines: list[dict], symbol: str, name: str) -> list[dict]:
    """Headlines that mention $SYMBOL or the token name as a whole word."""
    pats = []
    if symbol and len(symbol) >= 3:
        pats.append(re.compile(rf"(?<![A-Za-z0-9])\$?{re.escape(symbol)}(?![A-Za-z0-9])", re.I))
    if name and len(name) >= 4:
        pats.append(re.compile(rf"\b{re.escape(name)}\b", re.I))
    return [h for h in headlines if any(p.search(h["title"]) for p in pats)]
