"""Copy-trading: follow small wallets whose size fits a ~50 EUR account, ignore whales.

Each tracked wallet is profiled from its own on-chain swaps (Solana RPC):
  * SOL balance and median buy size -> "fits" or "whale"
  * every buy we see is scored 30 min later (did the price go up?) -> hit rate
Manually added wallets are copyable as soon as they fit. Auto-discovered wallets (early buyers
of tokens that ran) need ``promote_after_obs`` scored buys with ``promote_hit_rate`` first.
"""
from __future__ import annotations

import statistics
import threading
import time

from .sources import dexscreener, solana


class WalletBook:
    def __init__(self, cfg: dict, journal):
        self.cfg = cfg
        self.j = journal
        self.w: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.recent_buys: list[dict] = []   # {wallet, mint, sol, ts, pct_of_balance}
        self.last_discovery = 0.0
        for a in cfg["wallets"]["tracked"]:
            self.add(a, "manual")

    def add(self, addr: str, source: str) -> bool:
        addr = addr.strip()
        if not (32 <= len(addr) <= 44):
            return False
        with self.lock:
            if addr in self.w:
                return False
            if len(self.w) >= self.cfg["wallets"]["max_tracked"] and source != "manual":
                return False
            self.w[addr] = {"address": addr, "source": source, "added": time.time(), "sol": None,
                            "buys": [], "sells": 0, "last_sig": None, "obs": 0, "hits": 0,
                            "median_buy": None, "status": "new", "last_seen_trade": None}
        return True

    def remove(self, addr: str) -> None:
        with self.lock:
            self.w.pop(addr, None)

    # ------------------------------------------------------------ profile
    def classify(self, w: dict) -> str:
        c = self.cfg["wallets"]
        buys = [b["sol"] for b in w["buys"][-40:]]
        w["median_buy"] = round(statistics.median(buys), 4) if buys else None
        if w["sol"] is not None and w["sol"] > c["max_wallet_sol"]:
            return "whale"
        if w["median_buy"] is not None and w["median_buy"] > c["max_median_buy_sol"]:
            return "whale"
        if w["median_buy"] is not None and w["median_buy"] < c["min_median_buy_sol"]:
            return "dust"
        if w["source"] == "manual":
            return "copy"
        if w["obs"] >= c["promote_after_obs"] and w["hits"] / w["obs"] >= c["promote_hit_rate"]:
            return "copy"
        return "watch"

    def copyable(self) -> set[str]:
        with self.lock:
            return {a for a, w in self.w.items() if w["status"] == "copy"}

    # -------------------------------------------------------------- poll
    def poll_once(self, max_tx_per_wallet: int = 8) -> list[dict]:
        """Fetch new swaps for every wallet. Returns new buys of copyable wallets."""
        new_copy_buys = []
        for addr in list(self.w):
            w = self.w.get(addr)
            if not w:
                continue
            bal = solana.balance_sol(addr)
            if bal is not None:
                w["sol"] = round(bal, 3)
            sigs = solana.signatures(addr, limit=15)
            fresh = []
            for s in sigs:
                if s.get("signature") == w["last_sig"]:
                    break
                if not s.get("err"):
                    fresh.append(s)
            if sigs:
                first_poll = w["last_sig"] is None
                w["last_sig"] = sigs[0]["signature"]
                if first_poll:
                    fresh = fresh[:max_tx_per_wallet]  # backfill a little history for profiling
            for s in fresh[:max_tx_per_wallet]:
                tx = solana.transaction(s["signature"])
                ts = s.get("blockTime") or time.time()
                for sw in solana.parse_swaps(tx, addr):
                    if sw["side"] == "sell":
                        w["sells"] += 1
                        continue
                    buy = {"mint": sw["mint"], "sol": round(sw["sol"], 4), "ts": ts, "price": None, "scored": False}
                    w["buys"].append(buy)
                    w["buys"] = w["buys"][-60:]
                    w["last_seen_trade"] = ts
                    if time.time() - ts < self.cfg["scanner"]["smart_money_window_min"] * 60:
                        ev = {"wallet": addr, "mint": sw["mint"], "sol": buy["sol"], "ts": ts,
                              "pct_of_balance": (buy["sol"] / w["sol"]) if w.get("sol") else None}
                        with self.lock:
                            self.recent_buys.append(ev)
                        if w["status"] == "copy":
                            new_copy_buys.append(ev)
            w["status"] = self.classify(w)
        cutoff = time.time() - 3600
        with self.lock:
            self.recent_buys = [b for b in self.recent_buys if b["ts"] > cutoff]
        self._score_buys()
        return new_copy_buys

    def _score_buys(self) -> None:
        """Record entry price for new buys and score buys older than 30 min."""
        need_price, need_score = set(), []
        now = time.time()
        for w in self.w.values():
            for b in w["buys"]:
                if b["scored"]:
                    continue
                if b["price"] is None and now - b["ts"] < 600:
                    need_price.add(b["mint"])
                elif b["price"] is not None and now - b["ts"] >= 1800:
                    need_score.append((w, b))
                    need_price.add(b["mint"])
                elif b["price"] is None and now - b["ts"] >= 600:
                    b["scored"] = True  # too old to know its entry price; ignore
        if not need_price:
            return
        prices = {m: float(p.get("priceUsd") or 0)
                  for m, p in dexscreener.best_pairs(dexscreener.pairs_for_tokens(list(need_price)[:60])).items()}
        for w in self.w.values():
            for b in w["buys"]:
                if b["price"] is None and not b["scored"] and prices.get(b["mint"]):
                    b["price"] = prices[b["mint"]]
        for w, b in need_score:
            p = prices.get(b["mint"])
            if p and b["price"]:
                w["obs"] += 1
                if p >= b["price"] * 1.10:
                    w["hits"] += 1
            b["scored"] = True

    def buyers_within(self, mint: str, minutes: float) -> list[dict]:
        cutoff = time.time() - minutes * 60
        copy = self.copyable()
        with self.lock:
            return [b for b in self.recent_buys if b["mint"] == mint and b["ts"] >= cutoff and b["wallet"] in copy]

    # ---------------------------------------------------------- discovery
    def discover_from_pair(self, pair_address: str, mint: str, pages: int = 3, take: int = 20) -> int:
        """Add the earliest buyers we can reach on a pool (signers of its oldest visible swaps)."""
        if not self.cfg["wallets"]["auto_discover"] or time.time() - self.last_discovery < 300:
            return 0
        self.last_discovery = time.time()
        before, sigs = None, []
        for _ in range(pages):
            batch = solana.signatures(pair_address, limit=100, before=before)
            if not batch:
                break
            sigs.extend(batch)
            before = batch[-1]["signature"]
            if len(batch) < 100:
                break
        added = 0
        for s in reversed(sigs[-take:]):
            tx = solana.transaction(s["signature"])
            if not tx:
                continue
            keys = tx["transaction"]["message"]["accountKeys"]
            signer = next((k["pubkey"] for k in keys if isinstance(k, dict) and k.get("signer")), None)
            if not signer:
                continue
            if any(sw["side"] == "buy" and sw["mint"] == mint for sw in solana.parse_swaps(tx, signer)):
                if self.add(signer, f"early buyer of {mint[:6]}"):
                    added += 1
        if added:
            self.j.log("WALLETS", f"discovered {added} early buyers to evaluate", mint=mint)
        return added

    def snapshot(self) -> list[dict]:
        with self.lock:
            out = []
            for w in self.w.values():
                out.append({k: w[k] for k in ("address", "source", "sol", "median_buy", "status", "obs", "hits",
                                              "sells", "last_seen_trade")} | {"n_buys": len(w["buys"])})
            return sorted(out, key=lambda x: (x["status"] != "copy", x["source"] != "manual", -(x["obs"] or 0)))

    def to_dict(self) -> dict:
        with self.lock:
            return {"w": self.w}

    def load(self, d: dict) -> None:
        with self.lock:
            for a, w in (d.get("w") or {}).items():
                self.w[a] = w
