"""DexScreener public API (no key). https://docs.dexscreener.com/api/reference"""
from __future__ import annotations

from ..net import health, http

BASE = "https://api.dexscreener.com"


def _solana_addresses(items) -> list[str]:
    out = []
    for it in items or []:
        if it.get("chainId") == "solana" and it.get("tokenAddress"):
            out.append(it["tokenAddress"])
    return out


def latest_profiles() -> list[str]:
    return _solana_addresses(http.get(f"{BASE}/token-profiles/latest/v1"))


def latest_boosts() -> list[str]:
    return _solana_addresses(http.get(f"{BASE}/token-boosts/latest/v1"))


def top_boosts() -> list[str]:
    return _solana_addresses(http.get(f"{BASE}/token-boosts/top/v1"))


def discover() -> dict[str, set[str]]:
    """Token mints DexScreener is showing right now, tagged with why."""
    tags: dict[str, set[str]] = {}
    for name, fn in (("dex_profile", latest_profiles), ("dex_boost", latest_boosts), ("dex_top_boost", top_boosts)):
        try:
            for a in fn():
                tags.setdefault(a, set()).add(name)
            health.ok("dexscreener")
        except Exception as e:  # noqa: BLE001
            health.fail("dexscreener", f"{name}: {e}")
    return tags


def pairs_for_tokens(mints: list[str]) -> list[dict]:
    """All pairs for up to N mints (batched 30 at a time)."""
    out: list[dict] = []
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        try:
            data = http.get(f"{BASE}/tokens/v1/solana/{','.join(chunk)}")
            if isinstance(data, dict):  # older shape {"pairs": [...]}
                data = data.get("pairs") or []
            out.extend(p for p in data if p.get("chainId") == "solana")
            health.ok("dexscreener")
        except Exception as e:  # noqa: BLE001
            health.fail("dexscreener", f"tokens: {e}")
    return out


def best_pairs(pairs: list[dict]) -> dict[str, dict]:
    """Pick the most liquid pair per base-token mint."""
    best: dict[str, dict] = {}
    for p in pairs:
        mint = (p.get("baseToken") or {}).get("address")
        if not mint:
            continue
        liq = (p.get("liquidity") or {}).get("usd") or 0
        if mint not in best or liq > ((best[mint].get("liquidity") or {}).get("usd") or 0):
            best[mint] = p
    return best


def sol_price_usd() -> float | None:
    """SOL/USD from the most liquid SOL/USDC pair."""
    try:
        data = http.get(f"{BASE}/latest/dex/search", params={"q": "SOL USDC"})
        best = None
        for p in data.get("pairs") or []:
            if p.get("chainId") != "solana":
                continue
            if (p.get("baseToken") or {}).get("symbol") in ("SOL", "WSOL") and (p.get("quoteToken") or {}).get("symbol") == "USDC":
                if best is None or (p.get("liquidity") or {}).get("usd", 0) > (best.get("liquidity") or {}).get("usd", 0):
                    best = p
        return float(best["priceUsd"]) if best else None
    except Exception as e:  # noqa: BLE001
        health.fail("dexscreener", f"sol price: {e}")
        return None
