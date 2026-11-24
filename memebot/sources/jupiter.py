"""Jupiter token API (no key): tokens trending on Solana by trading activity."""
from __future__ import annotations

from ..net import health, http


def trending(interval: str = "1h", limit: int = 50) -> dict[str, set[str]]:
    tags: dict[str, set[str]] = {}
    try:
        rows = http.get(f"https://lite-api.jup.ag/tokens/v2/toptrending/{interval}", params={"limit": limit})
        for r in rows or []:
            mint = r.get("id") or r.get("address")
            if mint:
                tags.setdefault(mint, set()).add("jup_trending")
        health.ok("jupiter")
    except Exception as e:  # noqa: BLE001
        health.fail("jupiter", f"trending: {e}")
    return tags
