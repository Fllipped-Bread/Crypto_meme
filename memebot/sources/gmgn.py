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
