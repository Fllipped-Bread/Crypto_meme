"""Minimal Solana JSON-RPC client (public RPC by default; set a Helius/QuickNode URL in config)."""
from __future__ import annotations

from ..net import health, http

RPC_URL = "https://api.mainnet-beta.solana.com"
WSOL = "So11111111111111111111111111111111111111112"
STABLES = {
    WSOL,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def set_rpc(url: str) -> None:
    global RPC_URL
    if url:
        RPC_URL = url


def rpc(method: str, params: list):
    try:
        r = http.post(RPC_URL, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in r:
            raise RuntimeError(r["error"])
        health.ok("solana_rpc")
        return r.get("result")
    except Exception as e:  # noqa: BLE001
        health.fail("solana_rpc", f"{method}: {e}")
        return None


def balance_sol(address: str) -> float | None:
    r = rpc("getBalance", [address])
    return r["value"] / 1e9 if r else None


def mint_info(mint: str) -> dict | None:
    r = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    try:
        info = r["value"]["data"]["parsed"]["info"]
        dec = int(info["decimals"])
        return {
            "mint_authority": info.get("mintAuthority"),
            "freeze_authority": info.get("freezeAuthority"),
            "supply": int(info["supply"]) / 10 ** dec,
            "decimals": dec,
        }
    except (TypeError, KeyError):
        return None


def largest_holders(mint: str) -> list[float]:
    r = rpc("getTokenLargestAccounts", [mint])
    try:
        return [float(a.get("uiAmount") or 0) for a in r["value"]]
    except (TypeError, KeyError):
        return []


def signatures(address: str, limit: int = 25, before: str | None = None) -> list[dict]:
    opts: dict = {"limit": limit}
    if before:
        opts["before"] = before
    return rpc("getSignaturesForAddress", [address, opts]) or []


def transaction(sig: str) -> dict | None:
    return rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])


def parse_swaps(tx: dict, owner: str) -> list[dict]:
    """Token balance changes of ``owner`` in one transaction.

    Returns [{"mint", "side": "buy"|"sell", "tokens", "sol"}] where ``sol`` is the SOL the
    owner spent (buy) or received (sell), including wrapped SOL movements.
    """
    if not tx or not tx.get("meta") or tx["meta"].get("err"):
        return []
    meta = tx["meta"]
    keys = tx["transaction"]["message"]["accountKeys"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]

    sol_delta = 0.0
    if owner in keys:
        i = keys.index(owner)
        sol_delta = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9

    def by_mint(rows):
        out: dict[str, float] = {}
        for b in rows or []:
            if b.get("owner") != owner:
                continue
            amt = float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            out[b["mint"]] = out.get(b["mint"], 0.0) + amt
        return out

    pre, post = by_mint(meta.get("preTokenBalances")), by_mint(meta.get("postTokenBalances"))
    wsol_delta = post.get(WSOL, 0.0) - pre.get(WSOL, 0.0)
    sol_total = sol_delta + wsol_delta

    swaps = []
    for mint in set(pre) | set(post):
        if mint in STABLES:
            continue
        d = post.get(mint, 0.0) - pre.get(mint, 0.0)
        if abs(d) <= 0:
            continue
        if d > 0 and sol_total < 0:
            swaps.append({"mint": mint, "side": "buy", "tokens": d, "sol": -sol_total})
        elif d < 0 and sol_total > 0:
            swaps.append({"mint": mint, "side": "sell", "tokens": -d, "sol": sol_total})
    return swaps
