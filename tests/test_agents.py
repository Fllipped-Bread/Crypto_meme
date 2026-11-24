import copy
import time

import pytest

from memebot import agents
from memebot.config import DEFAULTS
from memebot.sources import solana


def cfg():
    c = copy.deepcopy(DEFAULTS)
    c["rugcheck"]["sell_simulation"] = True
    return c


def pair(price=0.001, liq=80_000, v5=40_000, h1=120_000, age_h=1.0, buys=90, sells=20, h1chg=50, m5chg=5,
         mint="Mint1111111111111111111111111111111111111", dex="pumpswap"):
    return {
        "chainId": "solana", "dexId": dex, "pairAddress": "Pool11111111111111111111111111111111111111",
        "url": "https://dexscreener.com/solana/x",
        "baseToken": {"address": mint, "symbol": "TEST", "name": "Test"},
        "priceUsd": str(price), "liquidity": {"usd": liq}, "volume": {"m5": v5, "h1": h1},
        "txns": {"m5": {"buys": buys, "sells": sells}}, "priceChange": {"h1": h1chg, "m5": m5chg},
        "marketCap": 1_000_000, "pairCreatedAt": (time.time() - age_h * 3600) * 1000,
    }


SAFE = {"mint_authority": None, "freeze_authority": None, "top10_pct": 20, "dev_pct": 1, "lp_locked_pct": 100,
        "holders": 800, "score": 300, "rugged": False, "insiders_detected": 0, "danger": [], "creator": "C",
        "creator_balance": 10}


def test_universe_filters():
    u = cfg()["universe"]["fresh"]
    assert agents.universe_check(pair(), u) is None
    assert "age" in agents.universe_check(pair(age_h=7), u)
    assert "liquidity" in agents.universe_check(pair(liq=10_000), u)
    assert "5m volume" in agents.universe_check(pair(v5=1000), u)


def test_strategy_selection_migrated_not_fresh():
    c = cfg()  # defaults: fresh off, migrated on
    assert agents.pick_strategy(pair(age_h=30, v5=8000), c)[0] == "migrated"
    strat, why = agents.pick_strategy(pair(age_h=30, v5=8000, dex="pumpfun"), c)
    assert strat is None and "bonding curve" in why
    assert agents.pick_strategy(pair(age_h=500), c)[0] is None          # migrated > 7 days ago
    c["strategies"]["fresh"] = True
    assert agents.pick_strategy(pair(age_h=1), c)[0] == "fresh"


def test_copy_positions_use_wider_exits():
    b = make_book()
    p = b.open(pair(), 10.0, ["copy_wallet"], "t", SAFE, strategy="copy", copied_from="W")
    assert b.update(p["id"], p["entry_price"] * 0.86, 80_000) is None   # -14%: inside the copy stop of -20%
    assert b.update(p["id"], p["entry_price"] * 1.1, 80_000, now=time.time() + 60 * 60) is None  # no 25 min time stop
    assert b.update(p["id"], p["entry_price"] * 0.79, 80_000) == "hard stop"
    assert b.trades[-1]["copied_from"] == "W"


def test_momentum_signals():
    c = cfg()
    # 1h volume 120k over 12 windows = 10k avg; 5m = 60k -> 6x spike
    s = agents.momentum_signals(pair(v5=60_000, h1=120_000), c)
    assert "volume_spike" in s and "buy_pressure" in s
    assert agents.momentum_signals(pair(v5=20_000, buys=10, sells=30), c) == []


def test_no_chase():
    c = cfg()
    assert agents.no_chase(pair(h1chg=400, m5chg=3), c)
    assert agents.no_chase(pair(h1chg=400, m5chg=-8), c) is None  # pulled back


@pytest.mark.parametrize("change,needle", [
    ({"mint_authority": "X"}, "mint authority"),
    ({"freeze_authority": "X"}, "freeze"),
    ({"top10_pct": 50}, "top 10"),
    ({"dev_pct": 9}, "dev holds"),
    ({"lp_locked_pct": 10}, "LP"),
    ({"holders": 100}, "holders"),
    ({"danger": ["Mutable metadata"]}, "danger"),
])
def test_rug_gate_blocks(change, needle):
    ok, why = agents.rug_gate({**SAFE, **change}, cfg(), True, 300)
    assert not ok and needle in why


def test_rug_gate_pass_and_honeypot():
    assert agents.rug_gate(SAFE, cfg(), True, 300)[0]
    ok, why = agents.rug_gate(SAFE, cfg(), False, 300)
    assert not ok and "honeypot" in why
    assert not agents.rug_gate(None, cfg(), True, 300)[0]


def test_sizer_fits_50_eur():
    c = cfg()
    size, _ = agents.size_trade(50, 50, 2, 0, 0, c)
    assert size == 10.0                       # 20% of 50
    size, _ = agents.size_trade(50, 50, 4, 0, 0, c)
    assert size == 20.0                       # 40% cap
    size, note = agents.size_trade(50, 50, 4, 2, 0, c)
    assert size == 10.0 and "losses" in note  # halved after 2 losses
    assert agents.size_trade(50, 50, 2, 0, 2, c)[0] == 0      # max 2 positions
    assert agents.size_trade(20, 20, 2, 0, 0, c)[0] == 0      # 4 EUR < 5 EUR minimum
    assert agents.size_trade(50, 50, 2, 0, 0, c, override_pct=0.9)[0] == 20.0


def make_book():
    c = cfg()
    b = agents.PaperBook(c, agents.Journal())
    b.usd_per_eur, b.sol_usd = 1.0, 150.0
    return b


def test_entry_costs_are_realistic():
    b = make_book()
    p = b.open(pair(price=0.001, liq=80_000), 10.0, ["a", "b"], "test", SAFE)
    assert b.cash == pytest.approx(40.0)
    # immediately marked at mid price: lost dex fee + 2 network fees + slippage, a few %
    loss = (50 - b.equity()) / 10            # round-trip-ish cost as a share of the 10 EUR ticket
    assert 0.005 < loss < 0.05
    assert p["slip_pct"] < 2.5


def test_take_profit_ladder_and_trailing():
    b = make_book()
    p = b.open(pair(price=0.001), 10.0, ["a", "b"], "t", SAFE)
    e = p["entry_price"]
    assert b.update(p["id"], e * 2.05, 80_000) is None
    assert p["tp_hit"] == 1 and p["tokens"] == pytest.approx(p["tokens_initial"] * 0.6)
    b.update(p["id"], e * 4.2, 80_000)
    assert p["tp_hit"] == 2 and p["tokens"] == pytest.approx(p["tokens_initial"] * 0.3)
    b.update(p["id"], e * 6.0, 80_000)                 # runner keeps going
    assert b.update(p["id"], e * 4.4, 80_000) == "trailing stop"   # -26% from 6x peak
    t = b.trades[-1]
    assert t["pnl_eur"] > 0 and t["max_x"] == 6.0
    assert b.loss_streak == 0


def test_hard_stop_time_stop_lp_pull():
    b = make_book()
    p = b.open(pair(), 10.0, ["a", "b"], "t", SAFE)
    assert b.update(p["id"], p["entry_price"] * 0.87, 80_000) == "hard stop"
    p = b.open(pair(mint="Mint2222222222222222222222222222222222222"), 10.0, ["a"], "t", SAFE)
    assert b.update(p["id"], p["entry_price"] * 1.1, 80_000, now=time.time() + 26 * 60) == "time stop"
    p = b.open(pair(mint="Mint3333333333333333333333333333333333333"), 10.0, ["a"], "t", SAFE)
    assert b.update(p["id"], p["entry_price"], 30_000) == "LP pulled"
    assert b.loss_streak >= 1


def test_daily_drawdown_pause():
    b = make_book()
    b.cash = 34.0  # -32% from 50 peak
    b.record_equity()
    assert b.paused_reason and "drawdown" in b.paused_reason


def test_slippage_cap_blocks_thin_pools():
    b = make_book()
    assert b.open(pair(liq=200), 10.0, ["a"], "t", SAFE) is None


def test_parse_swaps_buy_and_sell():
    owner = "Owner111111111111111111111111111111111111"
    mint = "Tok11111111111111111111111111111111111111"

    def tx(sol_pre, sol_post, tok_pre, tok_post):
        return {"meta": {"err": None, "preBalances": [sol_pre], "postBalances": [sol_post],
                         "preTokenBalances": [{"owner": owner, "mint": mint, "uiTokenAmount": {"uiAmount": tok_pre}}],
                         "postTokenBalances": [{"owner": owner, "mint": mint, "uiTokenAmount": {"uiAmount": tok_post}}]},
                "transaction": {"message": {"accountKeys": [{"pubkey": owner, "signer": True}]}}}

    buy = solana.parse_swaps(tx(2_000_000_000, 1_500_000_000, 0, 1000), owner)
    assert buy == [{"mint": mint, "side": "buy", "tokens": 1000, "sol": 0.5}]
    sell = solana.parse_swaps(tx(1_000_000_000, 1_800_000_000, 1000, 0), owner)
    assert sell[0]["side"] == "sell" and sell[0]["sol"] == pytest.approx(0.8)


def _swaps(results, buy_sol=0.5, hold=600, t0=1_700_000_000):
    out = []
    for i, mult in enumerate(results):
        m = f"M{i}"
        out.append({"mint": m, "side": "buy", "sol": buy_sol, "tokens": 1000, "ts": t0 + i * 3600})
        out.append({"mint": m, "side": "sell", "sol": buy_sol * mult, "tokens": 1000, "ts": t0 + i * 3600 + hold})
    return out


def test_trade_stats_winrate_pnl():
    from memebot.wallets import trade_stats
    st = trade_stats(_swaps([2.0, 0.5, 1.5, 3.0]), now=1_700_000_000 + 86400 * 2)
    assert st["closed"] == 4 and st["wins"] == 3 and st["winrate"] == 0.75
    assert st["pnl_sol"] == pytest.approx(0.5 * (1.0 - 0.5 + 0.5 + 2.0))
    assert st["median_buy"] == 0.5 and st["median_hold_s"] == 600
    # a sell of a coin bought before the history window is ignored
    st = trade_stats([{"mint": "X", "side": "sell", "sol": 5, "tokens": 10, "ts": 1}])
    assert st["closed"] == 0


def test_wallet_classification():
    from memebot.wallets import WalletBook
    c = cfg()
    wb = WalletBook(c, agents.Journal())
    good = _swaps([2, 1.5, 0.7, 2.5, 1.2, 3, 0.8, 1.6])          # 6/8 wins, +PnL
    wb.add("Good111111111111111111111111111111111111", "gmgn")
    w = wb.w["Good111111111111111111111111111111111111"]
    w.update(sol=12.0, swaps=good, profiled=True)
    assert wb.classify(w) == "copy"
    w["swaps"] = _swaps([0.5, 0.6, 2, 0.4, 0.3, 0.8, 0.9, 0.7])   # 1/8 wins
    assert wb.classify(w) == "poor"
    w["swaps"] = good[:4]
    assert wb.classify(w) == "watch"                               # too few closed trades
    w.update(swaps=good, sol=900.0)
    assert wb.classify(w) == "whale"                               # too big for a 50 EUR account
    w.update(sol=12.0, swaps=_swaps([2] * 8, buy_sol=25))
    assert wb.classify(w) == "whale"
    w["swaps"] = _swaps([2] * 8, hold=3)
    assert wb.classify(w) == "bot"                                 # sniper: holds for seconds
    m = wb.w[c["wallets"]["tracked"][0]]
    m.update(sol=5.0, profiled=True)
    assert wb.classify(m) == "copy"                                # your own wallet, no data yet
