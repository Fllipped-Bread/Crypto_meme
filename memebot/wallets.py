"""Copy-trading: find wallets with a good win rate and PnL whose trade size fits a ~50 EUR account.

Every wallet is judged on its own on-chain swaps (Solana RPC), not on anyone's say-so:

  * per coin: SOL spent on buys vs SOL received on sells -> closed trades, wins, PnL in SOL
  * win rate, total realized PnL, median buy size, median hold time, trades per day
  * SOL balance

Classification
  whale  more SOL or bigger buys than fits a small account          -> ignored
  dust   buys too small to mean anything                            -> ignored
  bot    hundreds of trades a day or holds for seconds (MEV/sniper) -> ignored
  poor   enough closed trades but win rate / PnL below the bar      -> ignored
  watch  not enough closed trades yet to judge
  copy   win rate >= min_winrate, PnL >= min_pnl_sol, >= min_closed_trades closed

Where wallets come from
  * your list (``tracked``), added from the dashboard
  * GMGN 7-day PnL leaderboard (when GMGN answers), pre-filtered by balance and win rate
  * traders of coins that ran (signers of swaps in the pool), then profiled like everyone else
"""
from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque

from .sources import gmgn, solana


def trade_stats(swaps: list[dict], now: float | None = None) -> dict:
    """Win rate / PnL from a wallet's swaps ({mint, side, sol, tokens, ts})."""
    now = now or time.time()
    per: dict[str, dict] = {}
    for s in sorted(swaps, key=lambda x: x["ts"]):
        d = per.setdefault(s["mint"], {"spent": 0.0, "recv": 0.0, "bought": 0.0, "sold": 0.0,
                                       "first_buy": None, "last_sell": None})
        if s["side"] == "buy":
            d["spent"] += s["sol"]
            d["bought"] += s["tokens"]
            d["first_buy"] = d["first_buy"] or s["ts"]
        elif d["bought"] > 0:  # ignore sells of coins bought before our history window
            d["recv"] += s["sol"]
            d["sold"] += s["tokens"]
            d["last_sell"] = s["ts"]
    closed = [d for d in per.values() if d["bought"] > 0 and d["sold"] >= 0.9 * d["bought"]]
    wins = [d for d in closed if d["recv"] > d["spent"]]
    holds = [d["last_sell"] - d["first_buy"] for d in closed if d["last_sell"] and d["first_buy"]]
    buys = [s["sol"] for s in swaps if s["side"] == "buy"]
    span_days = max(1.0, (now - min((s["ts"] for s in swaps), default=now)) / 86400)
    return {
        "closed": len(closed),
        "wins": len(wins),
        "winrate": round(len(wins) / len(closed), 3) if closed else None,
        "pnl_sol": round(sum(d["recv"] - d["spent"] for d in closed), 4),
        "median_buy": round(statistics.median(buys), 4) if buys else None,
        "median_hold_s": round(statistics.median(holds)) if holds else None,
        "trades_per_day": round(len(swaps) / span_days, 1),
        "open_coins": sum(1 for d in per.values() if d["bought"] > 0 and d["sold"] < 0.9 * d["bought"]),
    }


class WalletBook:
    def __init__(self, cfg: dict, journal):
        self.cfg = cfg
        self.j = journal
        self.w: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.recent_buys: list[dict] = []   # {wallet, mint, sol, ts, pct_of_balance}
        self.discovery_queue: deque = deque(maxlen=20)   # (pair_address, mint, symbol)
        self.mined: set[str] = set()
        self.last_discovery = 0.0
        self.last_leaderboard = 0.0
        for a in cfg["wallets"]["tracked"]:
            self.add(a, "manual")

    def add(self, addr: str, source: str, hint: dict | None = None) -> bool:
        addr = addr.strip()
        if not (32 <= len(addr) <= 44):
            return False
        with self.lock:
            if addr in self.w:
                return False
            if source != "manual" and len(self.w) >= self.cfg["wallets"]["max_tracked"]:
                if not self._evict_one():
                    return False
            self.w[addr] = {"address": addr, "source": source, "added": time.time(), "sol": None,
                            "swaps": [], "last_sig": None, "profiled": False, "stats": {},
                            "status": "new", "last_seen_trade": None, "hint": hint or {}}
        return True

    def remove(self, addr: str) -> None:
        with self.lock:
            self.w.pop(addr, None)

    def _evict_one(self) -> bool:
        """Drop the least useful auto-discovered wallet to make room."""
        order = {"whale": 0, "dust": 0, "bot": 0, "poor": 1, "new": 2, "watch": 3}
        cands = [w for w in self.w.values() if w["source"] != "manual" and w["status"] in order]
        if not cands:
            return False
        worst = min(cands, key=lambda w: (order[w["status"]], self.score(w)))
        self.w.pop(worst["address"], None)
        return True

    # ------------------------------------------------------------ profile
    @staticmethod
    def score(w: dict) -> float:
        st = w.get("stats") or {}
        if not st.get("winrate"):
            return 0.0
        return st["winrate"] * math.log1p(max(0.0, st.get("pnl_sol") or 0)) * math.log1p(st.get("closed") or 0)

    def classify(self, w: dict) -> str:
        c = self.cfg["wallets"]
        st = w["stats"] = trade_stats(w["swaps"])
        if w["sol"] is not None and w["sol"] > c["max_wallet_sol"]:
            return "whale"
        if st["median_buy"] is not None and st["median_buy"] > c["max_median_buy_sol"]:
            return "whale"
        if st["median_buy"] is not None and st["median_buy"] < c["min_median_buy_sol"]:
            return "dust"
        if st["trades_per_day"] > c["max_trades_per_day"] or \
                (st["median_hold_s"] is not None and st["closed"] >= 3 and st["median_hold_s"] < c["min_median_hold_sec"]):
            return "bot"
        if st["closed"] < c["min_closed_trades"]:
            # your own wallets get the benefit of the doubt until there is data
            return "copy" if w["source"] == "manual" and w.get("profiled") else "watch"
        if (st["winrate"] or 0) >= c["min_winrate"] and st["pnl_sol"] >= c["min_pnl_sol"]:
            return "copy"
        return "poor"

    def copyable(self) -> set[str]:
        with self.lock:
            return {a for a, w in self.w.items() if w["status"] == "copy"}

    def _profile(self, w: dict) -> None:
        """First look at a wallet: read up to ``profile_tx`` past transactions."""
        want = self.cfg["wallets"]["profile_tx"]
        sigs, before = [], None
        while len(sigs) < want:
            batch = solana.signatures(w["address"], limit=min(100, want - len(sigs)), before=before)
            if not batch:
                break
            sigs.extend(batch)
            before = batch[-1]["signature"]
            if len(batch) < 100:
                break
        if sigs:
            w["last_sig"] = sigs[0]["signature"]
        for s in sigs:
            if s.get("err"):
                continue
            self._ingest(w, s, live=False)
        w["profiled"] = True

    def _ingest(self, w: dict, sig: dict, live: bool) -> list[dict]:
        tx = solana.transaction(sig["signature"])
        ts = sig.get("blockTime") or time.time()
        events = []
        for sw in solana.parse_swaps(tx, w["address"]):
            rec = {"mint": sw["mint"], "side": sw["side"], "sol": round(sw["sol"], 5),
                   "tokens": sw["tokens"], "ts": ts}
            w["swaps"].append(rec)
            w["last_seen_trade"] = max(w["last_seen_trade"] or 0, ts)
            if live:
                events.append(rec)
        w["swaps"] = w["swaps"][-300:]
        return events

    # -------------------------------------------------------------- poll
    def poll_once(self, max_tx_per_wallet: int = 8, max_new_profiles: int = 2) -> tuple[list[dict], list[dict]]:
        """New swaps for every wallet. Returns (copy_buys, copy_sells) from copyable wallets."""
        buys_out, sells_out = [], []
        profiled_now = 0
        window = self.cfg["scanner"]["smart_money_window_min"] * 60
        for addr in list(self.w):
            w = self.w.get(addr)
            if not w or w["status"] in ("whale", "dust", "bot") and w["source"] != "manual":
                continue
            bal = solana.balance_sol(addr)
            if bal is not None:
                w["sol"] = round(bal, 3)
            if not w["profiled"]:
                if profiled_now >= max_new_profiles:
                    continue
                if w["sol"] is not None and w["sol"] > self.cfg["wallets"]["max_wallet_sol"]:
                    w["status"] = "whale"   # no need to read its history
                    continue
                self._profile(w)
                profiled_now += 1
                w["status"] = self.classify(w)
                st = w["stats"]
                self.j.log("WALLETS", f"profiled {addr[:4]}…{addr[-4:]}: {w['status']} · "
                                      f"win {st['winrate'] if st['winrate'] is not None else '–'} · "
                                      f"PnL {st['pnl_sol']} SOL · {st['closed']} closed")
                continue
            sigs = solana.signatures(addr, limit=15)
            fresh = []
            for s in sigs:
                if s.get("signature") == w["last_sig"]:
                    break
                if not s.get("err"):
                    fresh.append(s)
            if sigs:
                w["last_sig"] = sigs[0]["signature"]
            was_copy = w["status"] == "copy"
            for s in reversed(fresh[:max_tx_per_wallet]):
                for ev in self._ingest(w, s, live=True):
                    if time.time() - ev["ts"] > window:
                        continue
                    out = {"wallet": addr, "mint": ev["mint"], "sol": ev["sol"], "ts": ev["ts"],
                           "pct_of_balance": (ev["sol"] / (w["sol"] + ev["sol"])) if w.get("sol") is not None else None}
                    if ev["side"] == "buy":
                        with self.lock:
                            self.recent_buys.append(out)
                        if was_copy:
                            buys_out.append(out)
                    elif was_copy:
                        sells_out.append(out)
            w["status"] = self.classify(w)
        cutoff = time.time() - 3600
        with self.lock:
            self.recent_buys = [b for b in self.recent_buys if b["ts"] > cutoff]
        return buys_out, sells_out

    def buyers_within(self, mint: str, minutes: float) -> list[dict]:
        cutoff = time.time() - minutes * 60
        copy = self.copyable()
        with self.lock:
            return [b for b in self.recent_buys if b["mint"] == mint and b["ts"] >= cutoff and b["wallet"] in copy]

    # ---------------------------------------------------------- discovery
    def queue_runner(self, pair_address: str, mint: str, symbol: str) -> None:
        if mint not in self.mined and all(m != mint for _, m, _ in self.discovery_queue):
            self.discovery_queue.append((pair_address, mint, symbol))

    def discover_tick(self) -> None:
        c = self.cfg["wallets"]
        if not c["auto_discover"]:
            return
        now = time.time()
        if now - self.last_leaderboard > 3600 and self.cfg["sources"].get("gmgn"):
            self.last_leaderboard = now
            added = 0
            for r in gmgn.top_wallets():
                if r["sol"] is not None and r["sol"] > c["max_wallet_sol"]:
                    continue
                if r["winrate"] is not None and r["winrate"] < c["min_winrate"]:
                    continue
                if self.add(r["address"], "gmgn top PnL", hint=r):
                    added += 1
            if added:
                self.j.log("WALLETS", f"GMGN leaderboard: {added} small wallets with good win rate to verify on-chain")
        if self.discovery_queue and now - self.last_discovery > c["discover_every_min"] * 60:
            self.last_discovery = now
            pool, mint, sym = self.discovery_queue.popleft()
            self.mined.add(mint)
            n = self.traders_of_pool(pool, mint, sym)
            if n:
                self.j.log("WALLETS", f"found {n} traders of ${sym} to verify on-chain", sym, mint)

    def traders_of_pool(self, pool: str, mint: str, symbol: str = "", take: int = 30) -> int:
        """Signers of recent swaps in a pool that ran; profiling then decides who is any good."""
        sigs = solana.signatures(pool, limit=100)
        added, seen = 0, set()
        for s in sigs[:take]:
            if s.get("err"):
                continue
            tx = solana.transaction(s["signature"])
            if not tx:
                continue
            keys = tx["transaction"]["message"]["accountKeys"]
            signer = next((k["pubkey"] for k in keys if isinstance(k, dict) and k.get("signer")), None)
            if not signer or signer in seen:
                continue
            seen.add(signer)
            if any(sw["mint"] == mint for sw in solana.parse_swaps(tx, signer)):
                if self.add(signer, f"traded ${symbol or mint[:6]}"):
                    added += 1
        return added

    # ------------------------------------------------------------ output
    def snapshot(self) -> list[dict]:
        with self.lock:
            out = []
            for w in self.w.values():
                st = w.get("stats") or {}
                out.append({"address": w["address"], "source": w["source"], "sol": w["sol"], "status": w["status"],
                            "winrate": st.get("winrate"), "pnl_sol": st.get("pnl_sol"), "closed": st.get("closed"),
                            "median_buy": st.get("median_buy"), "median_hold_s": st.get("median_hold_s"),
                            "trades_per_day": st.get("trades_per_day"), "last_seen_trade": w["last_seen_trade"],
                            "score": round(self.score(w), 3), "profiled": w["profiled"]})
            rank = {"copy": 0, "watch": 1, "new": 2, "poor": 3, "bot": 4, "whale": 5, "dust": 6}
            return sorted(out, key=lambda x: (rank.get(x["status"], 9), -x["score"]))

    def to_dict(self) -> dict:
        with self.lock:
            return {"w": self.w, "mined": list(self.mined)}

    def load(self, d: dict) -> None:
        with self.lock:
            for a, w in (d.get("w") or {}).items():
                if "swaps" not in w:   # state from v1: re-profile
                    continue
                self.w[a] = w
            self.mined = set(d.get("mined") or [])


