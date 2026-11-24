"""pump.fun frontend API (unofficial, no key). Best effort: may change or be blocked."""
from __future__ import annotations

from ..net import health, http

BASE = "https://frontend-api-v3.pump.fun"
HEADERS = {"Origin": "https://pump.fun", "Referer": "https://pump.fun/"}


def _coins(path: str, params: dict) -> list[dict]:
    data = http.get(f"{BASE}{path}", params=params, headers=HEADERS)
    if isinstance(data, dict):
        data = data.get("coins") or data.get("data") or []
    return data or []


def discover() -> tuple[dict[str, set[str]], dict[str, dict]]:
    """Returns ({mint: tags}, {mint: coin-info}).

    Tags: ``pump_live`` (on the live/hot feed), ``pump_graduated`` (bonding curve complete,
    migrated to PumpSwap/Raydium), ``pump_koth`` (king of the hill).
    """
    tags: dict[str, set[str]] = {}
    info: dict[str, dict] = {}
    queries = [
        ("/coins/currently-live", {"limit": 50, "offset": 0, "includeNsfw": "false"}),
        ("/coins", {"offset": 0, "limit": 50, "sort": "last_trade_timestamp", "order": "DESC", "includeNsfw": "false"}),
        ("/coins", {"offset": 0, "limit": 50, "sort": "market_cap", "order": "DESC", "includeNsfw": "false", "complete": "true"}),
    ]
    for path, params in queries:
        try:
            for c in _coins(path, params):
                mint = c.get("mint")
                if not mint:
                    continue
                t = tags.setdefault(mint, set())
                if path.endswith("currently-live"):
                    t.add("pump_live")
                if c.get("complete") or c.get("raydium_pool") or c.get("pump_swap_pool"):
                    t.add("pump_graduated")
                if c.get("king_of_the_hill_timestamp"):
                    t.add("pump_koth")
                t.add("pumpfun")
                info[mint] = {
                    "creator": c.get("creator"),
                    "reply_count": c.get("reply_count"),
                    "twitter": c.get("twitter"),
                    "usd_market_cap": c.get("usd_market_cap"),
                }
            health.ok("pumpfun")
        except Exception as e:  # noqa: BLE001
            health.fail("pumpfun", e)
    return tags, info
