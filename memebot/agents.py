"""The six agents from the overnight prompt, as plain testable logic.

SCANNER   flags tokens (never trades)
RUG CHECK gates every entry (nothing executes without a PASS)
SIZER     sizes from current equity, never a fixed ticket
EXECUTOR  paper fills with a realistic Solana cost model, TP ladder, stops
LOGGER    every ENTER / EXIT / FILTERED line with a reason
EQUITY    balance, equity curve, daily drawdown guard
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path


def f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# =============================================================== LOGGER
class Journal:
    def __init__(self, path: Path | None = None, keep: int = 600):
        self.lines: deque = deque(maxlen=keep)
        self.path = path
        self.count = 0
        self._lock = threading.Lock()
        self._seen: dict[tuple, float] = {}

    def log_once(self, kind: str, reason: str, symbol: str = "", mint: str = "", every: float = 900, **extra):
        """Like log(), but the same kind+token+reason is written at most once per ``every`` seconds."""
        key = (kind, mint, reason)
        now = time.time()
        if now - self._seen.get(key, 0) < every:
            return None
        self._seen[key] = now
        if len(self._seen) > 5000:
            self._seen = {k: v for k, v in self._seen.items() if now - v < every}
        return self.log(kind, reason, symbol, mint, **extra)

    def log(self, kind: str, reason: str, symbol: str = "", mint: str = "", **extra) -> dict:
        e = {"ts": time.time(), "kind": kind, "symbol": symbol, "mint": mint, "reason": reason, **extra}
        with self._lock:
            self.count += 1
            e["n"] = self.count
            self.lines.append(e)
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as fh:
                        t = datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M:%S")
                        tail = " ".join(f"{k}={v}" for k, v in extra.items())
                        fh.write(f"{t} {kind:<8} {('$' + symbol) if symbol else '':<14} {reason} {tail}\n")
                except OSError:
                    pass
        return e

    def recent(self, n: int = 200) -> list[dict]:
        with self._lock:
            return list(self.lines)[-n:]


# ============================================================== SCANNER
def pair_age_hours(pair: dict, now: float | None = None) -> float | None:
    created = pair.get("pairCreatedAt")
    if not created:
        return None
    return ((now or time.time()) - created / 1000) / 3600


def universe_check(pair: dict, cfg: dict, now: float | None = None) -> str | None:
    """None if the pair is in the tradeable universe, else the reason it is not."""
    u = cfg["universe"]
    age = pair_age_hours(pair, now)
    if age is None:
        return "unknown pair age"
    if age > u["max_pair_age_hours"]:
        return f"pair age {age:.1f}h > {u['max_pair_age_hours']}h"
    liq = f((pair.get("liquidity") or {}).get("usd"))
    if liq < u["min_liquidity_usd"]:
        return f"liquidity ${liq:,.0f} < ${u['min_liquidity_usd']:,}"
    v5 = f((pair.get("volume") or {}).get("m5"))
    if v5 < u["min_volume_m5_usd"]:
        return f"5m volume ${v5:,.0f} < ${u['min_volume_m5_usd']:,}"
    mcap = f(pair.get("marketCap") or pair.get("fdv"))
    if mcap and mcap > u["max_mcap_usd"]:
        return f"mcap ${mcap:,.0f} too large"
    dex = pair.get("dexId", "")
    if u.get("dex_ids") and dex not in u["dex_ids"]:
        return f"dex {dex} not in universe"
    if not f(pair.get("priceUsd")):
        return "no USD price"
    return None


def momentum_signals(pair: dict, cfg: dict, now: float | None = None) -> list[str]:
    """Signals computable from DexScreener pair data alone."""
    s = cfg["scanner"]
    out = []
    vol = pair.get("volume") or {}
    v5, h1 = f(vol.get("m5")), f(vol.get("h1"))
    age_min = max(5.0, (pair_age_hours(pair, now) or 1.0) * 60)
    windows = min(12.0, age_min / 5)  # number of 5-min windows in the last hour (young pairs have fewer)
    avg5 = h1 / windows if windows else 0
    if avg5 > 0 and v5 > s["volume_spike_mult"] * avg5:
        out.append("volume_spike")
    tx = (pair.get("txns") or {}).get("m5") or {}
    buys, sells = f(tx.get("buys")), f(tx.get("sells"))
    if buys + sells >= 20 and buys / (buys + sells) >= s["min_buy_ratio"]:
        out.append("buy_pressure")
    return out


def no_chase(pair: dict, cfg: dict) -> str | None:
    pc = pair.get("priceChange") or {}
    h1, m5 = f(pc.get("h1")), f(pc.get("m5"))
    if h1 >= cfg["scanner"]["no_chase_h1_pct"] and m5 >= 0:
        return f"no chase: +{h1:.0f}% in 1h without a pullback"
    return None


class HolderHistory:
    """Remembers holder counts per mint to measure holder growth."""

    def __init__(self):
        self.first: dict[str, tuple[float, int]] = {}

    def growth_pct(self, mint: str, holders: int | None, now: float | None = None) -> float | None:
        if not holders:
            return None
        now = now or time.time()
        if mint not in self.first or now - self.first[mint][0] > 1800:
            self.first[mint] = (now, holders)
            return None
        t0, h0 = self.first[mint]
        if now - t0 < 60 or not h0:
            return None
        return (holders - h0) / h0 * 100


# ============================================================ RUG CHECK
def rug_gate(rep: dict | None, cfg: dict, sellable: bool | None, min_holders: int) -> tuple[bool, str]:
    r = cfg["rugcheck"]
    if rep is None:
        return False, "no safety data (rugcheck + RPC unavailable)"
    if rep.get("rugged"):
        return False, "flagged rugged"
    if r["require_no_mint_authority"] and rep.get("mint_authority"):
        return False, "mint authority still open"
    if r["require_no_freeze_authority"] and rep.get("freeze_authority"):
        return False, "freeze authority set"
    if rep.get("top10_pct") is not None and rep["top10_pct"] > r["max_top10_pct"]:
        return False, f"top 10 holders {rep['top10_pct']:.0f}%"
    if rep.get("dev_pct") is not None and rep["dev_pct"] > r["max_dev_pct"]:
        return False, f"dev holds {rep['dev_pct']:.1f}%"
    if rep.get("lp_locked_pct") is not None and rep["lp_locked_pct"] < r["min_lp_locked_pct"]:
        return False, f"LP only {rep['lp_locked_pct']:.0f}% locked/burned"
    if rep.get("score") is not None and rep["score"] > r["max_rugcheck_score"]:
        return False, f"rugcheck score {rep['score']}"
    if rep.get("danger"):
        return False, "danger: " + ", ".join(rep["danger"][:2])
    if rep.get("insiders_detected", 0) >= 3:
        return False, f"{rep['insiders_detected']} insider clusters (bundled launch)"
    if rep.get("holders") is not None and rep["holders"] < min_holders:
        return False, f"only {rep['holders']} holders"
    if r["sell_simulation"] and sellable is False:
        return False, "sell simulation failed (honeypot)"
    parts = [f"top10 {rep.get('top10_pct')}%"]
    if rep.get("dev_pct") is not None:
        parts.append(f"dev {rep['dev_pct']:.1f}%")
    if rep.get("lp_locked_pct") is not None:
        parts.append(f"LP {rep['lp_locked_pct']:.0f}%")
    return True, "PASS " + " · ".join(parts)


# ================================================================ SIZER
def size_trade(equity: float, cash: float, n_signals: int, loss_streak: int, open_positions: int,
               cfg: dict, override_pct: float | None = None) -> tuple[float, str]:
    """Returns (size_eur, note). size 0 means skip."""
    s = cfg["sizer"]
    if open_positions >= s["max_open_positions"]:
        return 0.0, f"max {s['max_open_positions']} open positions"
    if override_pct is not None:
        pct = min(s["max_pct"], max(s["min_pct"], override_pct))
    else:
        steps = max(0, n_signals - cfg["scanner"]["min_signals"])
        pct = min(s["max_pct"], s["min_pct"] + 0.10 * steps)
    note = f"{pct * 100:.0f}% eq"
    if loss_streak >= s["loss_streak_cut"]:
        pct *= s["loss_streak_factor"]
        note += f" ×{s['loss_streak_factor']} after {loss_streak} losses"
    size = min(equity * pct, cash - s["reserve_eur"])
    if size < s["min_trade_eur"]:
        return 0.0, f"size €{max(size, 0):.2f} below min €{s['min_trade_eur']}"
    return round(size, 2), note


# ======================================================= EXECUTOR + EQUITY
class PaperBook:
    """Paper account in EUR. Prices come in USD from DexScreener."""

    def __init__(self, cfg: dict, journal: Journal):
        self.cfg = cfg
        self.j = journal
        self.start = cfg["start_balance_eur"]
        self.cash = self.start
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.curve: list[tuple[float, float]] = [(time.time(), self.start)]
        self.loss_streak = 0
        self.day = datetime.now().strftime("%Y-%m-%d")
        self.day_start = self.start
        self.day_peak = self.start
        self.paused_reason: str | None = None
        self.cooldown: dict[str, float] = {}
        self.usd_per_eur = 1.08
        self.sol_usd = 150.0
        self.lock = threading.RLock()

    # ------------------------------------------------------------- costs
    def _dex_fee_pct(self, dex: str) -> float:
        fees = self.cfg["executor"]["dex_fee_pct"]
        return fees.get(dex, fees["default"])

    def impact_pct(self, usd: float, liq_usd: float) -> float:
        """Constant-product price impact for a trade against a pool worth liq_usd (both sides)."""
        side = max(liq_usd / 2, 1.0)
        return usd / (side + usd) * 100

    def _net_fee_usd(self) -> float:
        return self.cfg["executor"]["network_fee_sol"] * self.sol_usd

    # ------------------------------------------------------------ equity
    def mark(self, pos: dict) -> float:
        return pos["tokens"] * pos["last_price"] / self.usd_per_eur

    def equity(self) -> float:
        with self.lock:
            return self.cash + sum(self.mark(p) for p in self.positions.values())

    def record_equity(self) -> float:
        with self.lock:
            eq = self.equity()
            now = time.time()
            if not self.curve or now - self.curve[-1][0] > 30:
                self.curve.append((now, round(eq, 4)))
                self.curve = self.curve[-3000:]
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self.day:
                self.day, self.day_start, self.day_peak = today, eq, eq
                if self.paused_reason and self.paused_reason.startswith("daily drawdown"):
                    self.paused_reason = None
                    self.j.log("INFO", "new day: drawdown pause lifted")
            self.day_peak = max(self.day_peak, eq)
            limit = self.cfg["risk"]["daily_drawdown_pct"]
            if not self.paused_reason and eq <= self.day_peak * (1 - limit / 100):
                self.paused_reason = f"daily drawdown -{limit:.0f}% from peak €{self.day_peak:.2f}"
                self.j.log("PAUSE", self.paused_reason)
            return eq

    # ------------------------------------------------------------- entry
    def open(self, pair: dict, size_eur: float, signals: list[str], reason: str, safety: dict | None) -> dict | None:
        ex = self.cfg["executor"]
        price = f(pair.get("priceUsd"))
        liq = f((pair.get("liquidity") or {}).get("usd"))
        mint = pair["baseToken"]["address"]
        sym = pair["baseToken"].get("symbol", "?")
        if price <= 0:
            return None
        with self.lock:
            size_usd = size_eur * self.usd_per_eur
            n = max(1, int(ex["split_entries"]))
            slip = self.impact_pct(size_usd / n, liq) + 0.3
            if slip > ex["max_slippage_pct"]:
                self.j.log("FILTERED", f"slippage {slip:.1f}% > {ex['max_slippage_pct']}%", sym, mint)
                return None
            fee_usd = size_usd * self._dex_fee_pct(pair.get("dexId", "")) / 100 + self._net_fee_usd() * n
            fill = price * (1 + slip / 100)
            tokens = (size_usd - fee_usd) / fill
            self.cash -= size_eur
            pos = {
                "id": uuid.uuid4().hex[:8], "mint": mint, "symbol": sym, "name": pair["baseToken"].get("name"),
                "pair": pair.get("pairAddress"), "dex": pair.get("dexId"), "url": pair.get("url"),
                "entry_price": fill, "mark_entry": price, "last_price": price, "peak_price": price,
                "tokens": tokens, "tokens_initial": tokens, "cost_eur": size_eur, "proceeds_eur": 0.0,
                "opened_at": time.time(), "tp_hit": 0, "entry_liq": liq, "last_liq": liq,
                "signals": signals, "reason": reason, "slip_pct": round(slip, 2), "fees_usd": round(fee_usd, 4),
                "creator": (safety or {}).get("creator"), "creator_balance": (safety or {}).get("creator_balance"),
            }
            self.positions[pos["id"]] = pos
            eq = self.equity()
            self.j.log("ENTER", reason, sym, mint, size_eur=round(size_eur, 2),
                       pct=round(size_eur / max(eq, 1e-9) * 100), price=price, slip=round(slip, 2),
                       signals=",".join(signals))
            return pos

    # -------------------------------------------------------------- exit
    def _sell(self, pos: dict, frac: float, price: float, liq: float, why: str, kind: str = "EXIT") -> float:
        frac = min(1.0, max(0.0, frac))
        tokens = pos["tokens"] if frac >= 0.999 else pos["tokens"] * frac
        gross_usd = tokens * price
        slip = self.impact_pct(gross_usd, liq) + 0.3 if liq > 0 else 50.0
        fee = gross_usd * self._dex_fee_pct(pos.get("dex", "")) / 100 + self._net_fee_usd()
        net_usd = max(0.0, gross_usd * (1 - slip / 100) - fee)
        eur = net_usd / self.usd_per_eur
        pos["tokens"] -= tokens
        pos["proceeds_eur"] += eur
        self.cash += eur
        self.j.log(kind, why, pos["symbol"], pos["mint"], sold_pct=round(frac * 100), eur=round(eur, 2),
                   price=price, x=round(price / pos["entry_price"], 2))
        return eur

    def _close(self, pos: dict, why: str) -> dict:
        pnl = pos["proceeds_eur"] - pos["cost_eur"]
        t = {
            "mint": pos["mint"], "symbol": pos["symbol"], "opened_at": pos["opened_at"], "closed_at": time.time(),
            "cost_eur": round(pos["cost_eur"], 4), "proceeds_eur": round(pos["proceeds_eur"], 4),
            "pnl_eur": round(pnl, 4), "pnl_pct": round(pnl / pos["cost_eur"] * 100, 2) if pos["cost_eur"] else 0,
            "exit_reason": why, "signals": pos["signals"], "max_x": round(pos["peak_price"] / pos["entry_price"], 2),
            "url": pos.get("url"),
        }
        self.trades.append(t)
        self.positions.pop(pos["id"], None)
        self.loss_streak = self.loss_streak + 1 if pnl < 0 else 0
        self.cooldown[pos["mint"]] = time.time()
        self.j.log("CLOSED", f"{why} · PnL €{pnl:+.2f} ({t['pnl_pct']:+.1f}%)", pos["symbol"], pos["mint"],
                   pnl_eur=round(pnl, 2))
        return t

    def update(self, pos_id: str, price: float, liq: float, dev_sold: bool = False, now: float | None = None) -> str | None:
        """Apply exit rules to one position at a new price. Returns the exit reason if fully closed."""
        ex = self.cfg["executor"]
        now = now or time.time()
        with self.lock:
            pos = self.positions.get(pos_id)
            if not pos or price <= 0:
                return None
            pos["last_price"], pos["last_liq"] = price, liq
            pos["peak_price"] = max(pos["peak_price"], price)
            x = price / pos["entry_price"]

            if dev_sold and ex["exit_on_dev_sell"]:
                self._sell(pos, 1.0, price, liq, "dev sold · exit")
                return self._close(pos, "dev sold")["exit_reason"]
            if pos["entry_liq"] and liq < pos["entry_liq"] * (1 - ex["lp_pull_exit_pct"] / 100):
                self._sell(pos, 1.0, price, liq, f"liquidity pulled ({liq:,.0f} vs {pos['entry_liq']:,.0f}) · exit")
                return self._close(pos, "LP pulled")["exit_reason"]
            if x <= 1 - ex["hard_stop_pct"] / 100:
                self._sell(pos, 1.0, price, liq, f"hard stop -{ex['hard_stop_pct']}%")
                return self._close(pos, "hard stop")["exit_reason"]

            ladder = ex["take_profit"]
            while pos["tp_hit"] < len(ladder) and x >= ladder[pos["tp_hit"]][0]:
                mult, frac_of_initial = ladder[pos["tp_hit"]]
                frac = min(1.0, pos["tokens_initial"] * frac_of_initial / pos["tokens"]) if pos["tokens"] else 1
                self._sell(pos, frac, price, liq, f"take profit {mult:g}x · sell {frac_of_initial * 100:.0f}%", "TP")
                pos["tp_hit"] += 1
            if pos["tokens"] <= pos["tokens_initial"] * 1e-6:
                return self._close(pos, "ladder complete")["exit_reason"]

            if pos["tp_hit"] > 0:
                trail = pos["peak_price"] * (1 - ex["trailing_stop_pct"] / 100)
                if price <= trail:
                    self._sell(pos, 1.0, price, liq,
                               f"runner trailing stop -{ex['trailing_stop_pct']}% from peak {pos['peak_price'] / pos['entry_price']:.1f}x")
                    return self._close(pos, "trailing stop")["exit_reason"]
            elif now - pos["opened_at"] >= ex["time_stop_min"] * 60:
                self._sell(pos, 1.0, price, liq, f"time stop {ex['time_stop_min']} min")
                return self._close(pos, "time stop")["exit_reason"]
        return None

    def force_close(self, pos_id: str, why: str) -> None:
        with self.lock:
            pos = self.positions.get(pos_id)
            if pos:
                self._sell(pos, 1.0, pos["last_price"], pos["last_liq"], why)
                self._close(pos, why)

    # ------------------------------------------------------------ stats
    def stats(self) -> dict:
        with self.lock:
            wins = [t for t in self.trades if t["pnl_eur"] > 0]
            losses = [t for t in self.trades if t["pnl_eur"] <= 0]
            eq = self.equity()
            return {
                "start": self.start, "cash": round(self.cash, 4), "equity": round(eq, 4),
                "pnl_eur": round(eq - self.start, 4), "pnl_pct": round((eq / self.start - 1) * 100, 2),
                "trades": len(self.trades), "wins": len(wins), "losses": len(losses),
                "win_rate": round(len(wins) / len(self.trades) * 100, 1) if self.trades else None,
                "loss_streak": self.loss_streak, "day_start": round(self.day_start, 4),
                "day_peak": round(self.day_peak, 4), "paused": self.paused_reason,
                "usd_per_eur": self.usd_per_eur, "sol_usd": self.sol_usd,
            }

    def recap(self) -> dict:
        with self.lock:
            ts = sorted(self.trades, key=lambda t: t["pnl_eur"])
            by_coin: dict[str, float] = {}
            for t in self.trades:
                by_coin[t["symbol"]] = by_coin.get(t["symbol"], 0) + t["pnl_eur"]
            top = sorted(by_coin.items(), key=lambda kv: kv[1], reverse=True)[:3]
            return {
                "start": self.start, "end": round(self.equity(), 2), "total_trades": len(self.trades),
                "best": ts[-1] if ts else None, "worst": ts[0] if ts else None,
                "top_coins": [{"symbol": k, "pnl_eur": round(v, 2)} for k, v in top],
            }

    # ------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        with self.lock:
            return {
                "start": self.start, "cash": self.cash, "positions": self.positions, "trades": self.trades,
                "curve": self.curve, "loss_streak": self.loss_streak, "day": self.day,
                "day_start": self.day_start, "day_peak": self.day_peak, "paused_reason": self.paused_reason,
                "cooldown": self.cooldown,
            }

    def load(self, d: dict) -> None:
        with self.lock:
            for k in ("start", "cash", "positions", "trades", "loss_streak", "day", "day_start",
                      "day_peak", "paused_reason", "cooldown"):
                if k in d:
                    setattr(self, k, d[k])
            self.curve = [tuple(x) for x in d.get("curve", self.curve)]


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, default=list)
    tmp.replace(path)
