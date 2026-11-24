"""SOL price and EUR/USD rate from CoinGecko's free API, with DexScreener fallback."""
from __future__ import annotations

from ..net import health, http
from . import dexscreener


def sol_and_fx() -> tuple[float | None, float | None]:
    """Returns (sol_usd, usd_per_eur)."""
    try:
        d = http.get("https://api.coingecko.com/api/v3/simple/price",
                     params={"ids": "solana", "vs_currencies": "usd,eur"})
        usd, eur = float(d["solana"]["usd"]), float(d["solana"]["eur"])
        health.ok("coingecko")
        return usd, usd / eur
    except Exception as e:  # noqa: BLE001
        health.fail("coingecko", e)
    return dexscreener.sol_price_usd(), None
