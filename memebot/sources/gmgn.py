"""GMGN.ai rankings (unofficial). gmgn sits behind Cloudflare, so this often returns 403 to
non-browser clients. When it works we get holder counts plus smart-money / KOL counts."""
from __future__ import annotations

from ..net import health, http

BASE = "https://gmgn.ai/defi/quotation/v1"
HEADERS = {"Referer": "https://gmgn.ai/", "Origin": "https://gmgn.ai"}


def discover() -> tuple[dict[str, set[str]], dict[str, dict]]:
    tags: dict[str, set[str]] = {}
    info: dict[str, dict] = {}
    try:
        data = http.get(
            f"{BASE}/rank/sol/swaps/1h",
            params={"orderby": "swaps", "direction": "desc", "filters[]": ["renounced", "frozen"]},
            headers=HEADERS,
        )
        rows = ((data or {}).get("data") or {}).get("rank") or []
        for r in rows:
            mint = r.get("address")
            if not mint:
                continue
            t = tags.setdefault(mint, {"gmgn"})
            smart = int(r.get("smart_degen_count") or 0)
            kol = int(r.get("renowned_count") or 0)
            if smart >= 3:
                t.add("gmgn_smart_money")
            if kol >= 1:
                t.add("gmgn_kol")
            info[mint] = {"holders": r.get("holder_count"), "smart": smart, "kol": kol}
        health.ok("gmgn", f"{len(rows)} tokens")
    except Exception as e:  # noqa: BLE001
        health.fail("gmgn", e)
    return tags, info


def _num(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return default


def top_wallets(period: str = "7d", limit: int = 100) -> list[dict]:
    """GMGN smart-wallet leaderboard by PnL. Best effort (Cloudflare often blocks it)."""
    try:
        data = http.get(f"{BASE}/rank/sol/wallets/{period}",
                        params={"orderby": f"pnl_{period}", "direction": "desc"}, headers=HEADERS)
        rows = ((data or {}).get("data") or {}).get("rank") or []
        out = []
        for r in rows[:limit]:
            addr = r.get("wallet_address") or r.get("address")
            if not addr:
                continue
            wr = _num(r, f"winrate_{period}", "winrate")
            out.append({
                "address": addr,
                "sol": _num(r, "sol_balance", "balance"),
                "pnl_pct": _num(r, f"pnl_{period}", "pnl"),
                "realized_usd": _num(r, f"realized_profit_{period}", "realized_profit"),
                "winrate": wr / 100 if wr and wr > 1 else wr,
                "txs": _num(r, f"txs_{period}", "txs_30d", "txs"),
            })
        health.ok("gmgn", f"{len(out)} wallets")
        return out
    except Exception as e:  # noqa: BLE001
        health.fail("gmgn", f"wallets: {e}")
        return []
