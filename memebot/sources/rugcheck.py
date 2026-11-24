"""Token safety data: rugcheck.xyz report, with Solana RPC fallback, and a Jupiter sell quote
as a honeypot check."""
from __future__ import annotations

from ..net import health, http
from . import solana

RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
JUP_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
WSOL = "So11111111111111111111111111111111111111112"

# Pool / program accounts that commonly show up as "top holders" but are not people.
_POOL_HINTS = ("pool", "amm", "raydium", "pump", "meteora", "orca", "bonding")


def report(mint: str) -> dict | None:
    """Normalised safety facts for a mint, or None if no source answered."""
    try:
        r = http.get(RUGCHECK.format(mint=mint))
        health.ok("rugcheck")
        return _from_rugcheck(r)
    except Exception as e:  # noqa: BLE001
        health.fail("rugcheck", e)
    return _from_rpc(mint)


def _from_rugcheck(r: dict) -> dict:
    supply = float((r.get("token") or {}).get("supply") or 0) or None
    holders = r.get("topHolders") or []
    known = r.get("knownAccounts") or {}

    def is_pool(h: dict) -> bool:
        owner = h.get("owner") or h.get("address") or ""
        kind = str((known.get(owner) or {}).get("type", "")).lower()
        name = str((known.get(owner) or {}).get("name", "")).lower()
        return kind == "amm" or any(x in name for x in _POOL_HINTS)

    people = [h for h in holders if not is_pool(h)]
    top10 = sum(float(h.get("pct") or 0) for h in people[:10])

    creator = r.get("creator")
    dev_pct = 0.0
    if r.get("creatorBalance") and supply:
        dev_pct = float(r["creatorBalance"]) / supply * 100
    for h in people:
        if creator and h.get("owner") == creator:
            dev_pct = max(dev_pct, float(h.get("pct") or 0))

    lp_locked = None
    for m in r.get("markets") or []:
        pct = (m.get("lp") or {}).get("lpLockedPct")
        if pct is not None:
            lp_locked = max(lp_locked or 0.0, float(pct))

    risks = [{"name": x.get("name"), "level": x.get("level")} for x in r.get("risks") or []]
    return {
        "source": "rugcheck",
        "mint_authority": r.get("mintAuthority"),
        "freeze_authority": r.get("freezeAuthority"),
        "top10_pct": round(top10, 2),
        "dev_pct": round(dev_pct, 2),
        "creator": creator,
        "creator_balance": r.get("creatorBalance"),
        "lp_locked_pct": lp_locked,
        "holders": r.get("totalHolders"),
        "score": r.get("score"),
        "rugged": bool(r.get("rugged")),
        "insiders_detected": r.get("graphInsidersDetected") or 0,
        "risks": risks,
        "danger": [x["name"] for x in risks if str(x.get("level")).lower() == "danger"],
    }


def _from_rpc(mint: str) -> dict | None:
    info = solana.mint_info(mint)
    if not info:
        return None
    supply = info["supply"] or 1
    largest = solana.largest_holders(mint)
    # index 0 is almost always the liquidity pool / bonding curve; skip it
    top10 = sum(a for a in largest[1:11]) / supply * 100 if largest else None
    return {
        "source": "rpc",
        "mint_authority": info["mint_authority"],
        "freeze_authority": info["freeze_authority"],
        "top10_pct": round(top10, 2) if top10 is not None else None,
        "dev_pct": None,
        "creator": None,
        "creator_balance": None,
        "lp_locked_pct": None,
        "holders": None,
        "score": None,
        "rugged": False,
        "insiders_detected": 0,
        "risks": [],
        "danger": [],
    }


def can_sell(mint: str, token_amount_raw: int = 1_000_000) -> bool | None:
    """Ask Jupiter for a token->SOL quote. False = no route (likely honeypot), None = unknown."""
    try:
        q = http.get(JUP_QUOTE, params={
            "inputMint": mint, "outputMint": WSOL, "amount": str(token_amount_raw), "slippageBps": 300,
        })
        health.ok("jupiter")
        return bool(q.get("outAmount")) and int(q.get("outAmount", 0)) > 0
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        health.fail("jupiter", msg)
        if "400" in msg or "No routes" in msg or "COULD_NOT_FIND" in msg:
            return False
        return None
