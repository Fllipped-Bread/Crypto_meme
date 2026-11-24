"""Wires the agents to live data: SCAN -> CHECK -> SIZE -> EXEC -> LOG."""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import agents
from .agents import Journal, PaperBook, f
from .net import health
from .sources import dexscreener, fx, gmgn, news, pumpfun, rugcheck, solana
from .wallets import WalletBook


class Engine:
    def __init__(self, cfg: dict, home: Path):
        self.cfg = cfg
        self.home = home
        self.state_path = home / "state.json"
        self.j = Journal(home / "night.log")
        self.book = PaperBook(cfg, self.j)
        self.wallets = WalletBook(cfg, self.j)
        self.holders = agents.HolderHistory()
        self.safety_cache: dict[str, tuple[float, dict | None, bool | None]] = {}
        self.extra: dict[str, dict] = {}             # pump.fun / gmgn info per mint
        self.headlines: list[dict] = []
        self.candidates: list[dict] = []             # last scan, for the dashboard
        self.agent_status = {k: {"state": "idle", "note": ""} for k in
                             ("scanner", "rugcheck", "sizer", "executor", "logger", "equity", "wallets")}
        self.checked = 0
        self.filtered = 0
        self.manual_pause = False
        self.stop = threading.Event()
        self.last_recap_day = None
        self.recap: dict | None = None
        solana.set_rpc(cfg["sources"]["solana_rpc"])
        self._load()

    # --------------------------------------------------------- persistence
    def _load(self) -> None:
        import json
        if self.state_path.exists():
            try:
                d = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.book.load(d.get("book", {}))
                self.wallets.load(d.get("wallets", {}))
                self.j.log("INFO", f"resumed paper account · equity €{self.book.equity():.2f}")
                return
            except Exception as e:  # noqa: BLE001
                self.j.log("INFO", f"could not read state.json ({e}); starting fresh")
        self.j.log("INFO", f"new paper account · €{self.book.start:.2f}")

    def save(self) -> None:
        try:
            agents.save_json(self.state_path, {"book": self.book.to_dict(), "wallets": self.wallets.to_dict()})
        except OSError:
            pass

    def reset(self, balance: float | None = None) -> None:
        with self.book.lock:
            if balance:
                self.cfg["start_balance_eur"] = float(balance)
            self.book.__init__(self.cfg, self.j)
        self.j.log("INFO", f"paper account reset to €{self.book.start:.2f}")
        self.save()

    # -------------------------------------------------------------- status
    def _set(self, agent: str, state: str, note: str = "") -> None:
        self.agent_status[agent] = {"state": state, "note": note, "ts": time.time()}

    def trading_allowed(self) -> str | None:
        if self.manual_pause:
            return "paused by you"
        if self.book.paused_reason:
            return self.book.paused_reason
        hrs = self.cfg.get("active_hours")
        if hrs:
            h = datetime.now().hour
            a, b = hrs
            inside = (a <= h < b) if a < b else (h >= a or h < b)
            if not inside:
                return f"outside active hours {a:02d}:00-{b:02d}:00"
        return None

    # ------------------------------------------------------------- safety
    def safety(self, mint: str, max_age: float = 90) -> tuple[dict | None, bool | None]:
        hit = self.safety_cache.get(mint)
        if hit and time.time() - hit[0] < max_age:
            return hit[1], hit[2]
        rep = rugcheck.report(mint)
        sellable = rugcheck.can_sell(mint) if self.cfg["rugcheck"]["sell_simulation"] else None
        self.safety_cache[mint] = (time.time(), rep, sellable)
        if len(self.safety_cache) > 2000:
            for k in sorted(self.safety_cache, key=lambda k: self.safety_cache[k][0])[:500]:
                self.safety_cache.pop(k, None)
        return rep, sellable

    def _x_mentions(self, symbol: str) -> int | None:
        token = self.cfg["sources"]["x_bearer_token"]
        if not token:
            return None
        cache = self.__dict__.setdefault("_xcache", {})
        hit = cache.get(symbol)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        n = news.x_mentions(symbol, token)
        cache[symbol] = (time.time(), n)
        return n

    # ------------------------------------------------------------- scanner
    def gather(self) -> dict[str, set[str]]:
        src = self.cfg["sources"]
        tags: dict[str, set[str]] = {}

        def merge(t):
            for m, s in t.items():
                tags.setdefault(m, set()).update(s)

        if src["dexscreener"]:
            merge(dexscreener.discover())
        if src["pumpfun"]:
            t, info = pumpfun.discover()
            merge(t)
            for m, i in info.items():
                self.extra.setdefault(m, {}).update(pump=i)
        if src["gmgn"]:
            t, info = gmgn.discover()
            merge(t)
            for m, i in info.items():
                self.extra.setdefault(m, {}).update(gmgn=i)
        for b in self.wallets.recent_buys:
            tags.setdefault(b["mint"], set()).add("wallet_buy")
        return tags

    def scan_once(self) -> None:
        cfg = self.cfg
        self._set("scanner", "scan", "gathering DexScreener / pump.fun / GMGN / wallets")
        tags = self.gather()
        mints = list(tags)[: cfg["scanner"]["max_candidates"] * 2]
        pairs = dexscreener.best_pairs(dexscreener.pairs_for_tokens(mints))
        open_mints = {p["mint"] for p in self.book.positions.values()}
        cands = []
        now = time.time()
        for mint, pair in pairs.items():
            self.checked += 1
            sym = pair["baseToken"].get("symbol", "?")
            c = {"mint": mint, "symbol": sym, "name": pair["baseToken"].get("name"), "url": pair.get("url"),
                 "dex": pair.get("dexId"), "price": f(pair.get("priceUsd")),
                 "liq": f((pair.get("liquidity") or {}).get("usd")), "v5": f((pair.get("volume") or {}).get("m5")),
                 "h1": f((pair.get("priceChange") or {}).get("h1")), "m5": f((pair.get("priceChange") or {}).get("m5")),
                 "age_h": agents.pair_age_hours(pair), "tags": sorted(tags.get(mint, [])), "signals": [],
                 "stage": "scanned", "note": "", "pair": pair}
            cands.append(c)
            why = agents.universe_check(pair, cfg)
            if why:
                c["stage"], c["note"] = "out", why
                continue
            sig = agents.momentum_signals(pair, cfg)
            t = tags.get(mint, set())
            if t & {"dex_boost", "dex_top_boost", "gmgn_kol"}:
                sig.append("kol_boost")
            if "gmgn_smart_money" in t:
                sig.append("smart_money")
            buyers = self.wallets.buyers_within(mint, cfg["scanner"]["smart_money_window_min"])
            if len({b["wallet"] for b in buyers}) >= cfg["scanner"]["smart_money_min_wallets"]:
                sig.append("copy_wallet")
            if len({b["wallet"] for b in buyers}) >= 3:
                sig.append("smart_cluster")
            if news.mentions(self.headlines, sym, pair["baseToken"].get("name", "")):
                sig.append("news")
            xm = self._x_mentions(sym) if sig else None
            if xm and xm >= 50:
                sig.append("x_mentions")
            c["signals"] = sorted(set(sig))
            c["stage"] = "flagged" if len(c["signals"]) >= cfg["scanner"]["min_signals"] else "watching"
            c["buyers"] = buyers
        # holder growth needs safety data; only fetch it for pairs already in the universe
        for c in cands:
            if c["stage"] not in ("flagged", "watching"):
                continue
            if c["stage"] == "watching" and len(c["signals"]) + 1 < cfg["scanner"]["min_signals"]:
                continue
            rep, sellable = self.safety(c["mint"])
            holders = (rep or {}).get("holders") or (self.extra.get(c["mint"], {}).get("gmgn") or {}).get("holders")
            g = self.holders.growth_pct(c["mint"], holders, now)
            c["holders"] = holders
            if g is not None and g >= cfg["scanner"]["holder_growth_pct"]:
                c["signals"].append("holder_growth")
            if len(c["signals"]) >= cfg["scanner"]["min_signals"]:
                c["stage"] = "flagged"
            c["safety"], c["sellable"] = rep, sellable
        flagged = [c for c in cands if c["stage"] == "flagged"]
        nxt = max(flagged, key=lambda c: len(c["signals"]), default=None)
        self._set("scanner", "scan", f"checked {len(pairs)} · flagged {len(flagged)}"
                  + (f" · next ${nxt['symbol']}" if nxt else ""))
        for c in sorted(flagged, key=lambda c: -len(c["signals"])):
            self.consider(c, open_mints)
        self.candidates = sorted(cands, key=lambda c: ({"entered": 0, "flagged": 1, "rejected": 2, "watching": 3}
                                                       .get(c["stage"], 4), -c["v5"]))[:60]

    def consider(self, c: dict, open_mints: set[str], override_pct: float | None = None) -> None:
        cfg = self.cfg
        mint, sym = c["mint"], c["symbol"]
        if mint in open_mints:
            c["stage"], c["note"] = "entered", "already holding"
            return
        if time.time() - self.book.cooldown.get(mint, 0) < 3600:
            c["stage"], c["note"] = "rejected", "cooldown after exit"
            return
        chase = agents.no_chase(c["pair"], cfg)
        if chase:
            c["stage"], c["note"] = "rejected", chase
            if self.j.log_once("FILTERED", chase, sym, mint):
                self.filtered += 1
            return
        self._set("rugcheck", "check", f"checking ${sym}")
        rep, sellable = c.get("safety"), c.get("sellable")
        if rep is None:
            rep, sellable = self.safety(mint)
        ok, why = agents.rug_gate(rep, cfg, sellable, cfg["universe"]["min_holders"])
        if not ok:
            c["stage"], c["note"] = "rejected", why
            if self.j.log_once("FILTERED", why, sym, mint, signals=",".join(c["signals"])):
                self.filtered += 1
            self._set("rugcheck", "block", f"blocked ${sym}: {why}")
            return
        self._set("rugcheck", "pass", f"passed ${sym}")
        if len(self.book.positions) >= cfg["sizer"]["max_open_positions"]:
            c["note"] = f"rug check passed · waiting for a free slot ({' + '.join(c['signals'])})"
            return
        blocked = self.trading_allowed()
        if blocked:
            c["stage"], c["note"] = "rejected", blocked
            return
        n = len(c["signals"]) + (1 if "smart_cluster" in c["signals"] else 0)
        size, note = agents.size_trade(self.book.equity(), self.book.cash, n, self.book.loss_streak,
                                       len(self.book.positions), cfg, override_pct)
        self._set("sizer", "size", note)
        if size <= 0:
            c["stage"], c["note"] = "rejected", note
            self.j.log_once("SKIP", note, sym, mint)
            return
        reason = f"{' + '.join(c['signals'])} · {why}"
        pos = self.book.open(c["pair"], size, c["signals"], reason, rep)
        if pos:
            open_mints.add(mint)
            c["stage"], c["note"] = "entered", f"€{size:.2f} ({note})"
            self._set("executor", "live", f"entered ${sym} €{size:.2f} slip {pos['slip_pct']}%")
            self.save()

    # ------------------------------------------------------------ executor
    def manage_once(self) -> None:
        pos = list(self.book.positions.values())
        if not pos:
            self._set("executor", "flat", "flat · waiting")
            return
        pairs = dexscreener.best_pairs(dexscreener.pairs_for_tokens([p["mint"] for p in pos]))
        for p in pos:
            pair = pairs.get(p["mint"])
            if not pair:
                continue
            dev_sold = False
            if p.get("creator_balance") and self.cfg["executor"]["exit_on_dev_sell"]:
                rep, _ = self.safety(p["mint"], max_age=60)
                if rep and rep.get("creator_balance") is not None and \
                        f(rep["creator_balance"]) < f(p["creator_balance"]) * 0.95:
                    dev_sold = True
            closed = self.book.update(p["id"], f(pair.get("priceUsd")), f((pair.get("liquidity") or {}).get("usd")),
                                      dev_sold)
            if closed:
                self._set("executor", "flat", f"closed ${p['symbol']}: {closed}")
                if closed in ("ladder complete", "trailing stop") and p.get("pair"):
                    threading.Thread(target=self.wallets.discover_from_pair, args=(p["pair"], p["mint"]),
                                     daemon=True).start()
                self.save()
        if self.book.positions:
            s = ", ".join(f"${p['symbol']} {p['last_price'] / p['entry_price']:.2f}x" for p in self.book.positions.values())
            self._set("executor", "live", s)

    # --------------------------------------------------------------- loops
    def _loop(self, name: str, interval: float, fn) -> None:
        while not self.stop.is_set():
            t0 = time.time()
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.j.log("ERROR", f"{name}: {e}")
                traceback.print_exc()
            self.stop.wait(max(1.0, interval - (time.time() - t0)))

    def _equity_tick(self) -> None:
        eq = self.book.record_equity()
        self._set("equity", "update", f"€{eq:.2f} · {len(self.book.trades)} closes")
        self._set("logger", "write", f"line #{self.j.count}")
        now = datetime.now()
        if now.hour == 7 and self.last_recap_day != now.date():
            self.last_recap_day = now.date()
            self.recap = self.book.recap()
            self.j.log("RECAP", f"start €{self.recap['start']:.2f} -> now €{self.recap['end']:.2f} · "
                                f"{self.recap['total_trades']} trades")
        self.save()

    def _fx_tick(self) -> None:
        sol, usd_per_eur = fx.sol_and_fx()
        if sol:
            self.book.sol_usd = sol
        if usd_per_eur:
            self.book.usd_per_eur = usd_per_eur

    def _news_tick(self) -> None:
        src = self.cfg["sources"]
        h = news.rss_headlines(src["news_rss"]) + news.cryptopanic(src["cryptopanic_token"])
        if h:
            self.headlines = h[:300]

    def _wallet_tick(self) -> None:
        self._set("wallets", "scan", f"polling {len(self.wallets.w)} wallets")
        buys = self.wallets.poll_once()
        for b in buys:
            self.j.log("COPY", f"tracked wallet {b['wallet'][:4]}…{b['wallet'][-4:]} bought {b['sol']} SOL",
                       mint=b["mint"])
            if self.cfg["wallets"]["copy_mode"] == "mirror":
                self._mirror(b)
        self._set("wallets", "idle", f"{len(self.wallets.copyable())} copyable / {len(self.wallets.w)} tracked")

    def _mirror(self, buy: dict) -> None:
        pairs = dexscreener.best_pairs(dexscreener.pairs_for_tokens([buy["mint"]]))
        pair = pairs.get(buy["mint"])
        if not pair:
            return
        liq = f((pair.get("liquidity") or {}).get("usd"))
        if liq < self.cfg["universe"]["min_liquidity_usd"]:
            self.j.log_once("FILTERED", f"copy skipped: liquidity ${liq:,.0f}", pair["baseToken"].get("symbol", ""), buy["mint"])
            return
        c = {"mint": buy["mint"], "symbol": pair["baseToken"].get("symbol", "?"), "pair": pair,
             "signals": ["copy_wallet", "mirror"], "stage": "flagged"}
        with self.book.lock:
            self.consider(c, {p["mint"] for p in self.book.positions.values()}, override_pct=buy.get("pct_of_balance"))

    def start(self) -> None:
        sc = self.cfg
        loops = [
            ("fx", 120, self._fx_tick),
            ("scanner", sc["scanner"]["interval_sec"], self.scan_once),
            ("executor", sc["executor"]["interval_sec"], self.manage_once),
            ("equity", 10, self._equity_tick),
            ("news", sc["sources"]["news_interval_sec"], self._news_tick),
            ("wallets", sc["wallets"]["interval_sec"], self._wallet_tick),
        ]
        for name, iv, fn in loops:
            threading.Thread(target=self._loop, args=(name, iv, fn), name=name, daemon=True).start()
        self.j.log("INFO", "6 agents online · rug check gate armed · paper mode")

    # ------------------------------------------------------------ snapshot
    def snapshot(self) -> dict:
        with self.book.lock:
            positions = [dict(p, x=round(p["last_price"] / p["entry_price"], 3),
                              value_eur=round(self.book.mark(p), 4)) for p in self.book.positions.values()]
            trades = list(self.book.trades[-50:])
            curve = list(self.book.curve[-600:])
        cands = [{k: v for k, v in c.items() if k not in ("pair", "safety", "buyers")} |
                 {"safety": {k: (c.get("safety") or {}).get(k) for k in ("top10_pct", "dev_pct", "lp_locked_pct", "holders", "score")}}
                 for c in self.candidates]
        return {
            "now": time.time(), "mode": self.cfg["mode"], "blocked": self.trading_allowed(),
            "stats": self.book.stats(), "agents": self.agent_status, "positions": positions,
            "trades": trades, "curve": curve, "log": self.j.recent(150), "candidates": cands,
            "checked": self.checked, "filtered": self.filtered, "wallets": self.wallets.snapshot(),
            "sources": health.snapshot(), "headlines": self.headlines[:12], "recap": self.recap,
            "config": {k: self.cfg[k] for k in ("universe", "scanner", "rugcheck", "sizer", "executor", "risk")},
        }
