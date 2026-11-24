"""Configuration: defaults tuned for a ~50 EUR paper account.

A ``config.json`` next to the executable overrides any of these values.
It is created on first launch so you can edit it.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

CONFIG_VERSION = 2

DEFAULTS: dict = {
    "config_version": CONFIG_VERSION,
    # ---------------------------------------------------------------- account
    "mode": "paper",                 # only "paper" is implemented; no real funds move
    "start_balance_eur": 50.0,
    "dashboard_port": 8765,
    "open_browser": True,
    "active_hours": None,            # e.g. [23, 7] to only open trades 23:00-07:00 local; None = always

    # ------------------------------------------------------------- strategies
    # copy     : mirror buys of proven wallets (good win rate + PnL, small enough to fit 50 EUR)
    # migrated : coins that graduated from pump.fun / launchpads to a real DEX pool
    # fresh    : brand-new pairs (< 6h). Off by default.
    "strategies": {"copy": True, "migrated": True, "fresh": False},

    # --------------------------------------------------------------- universe
    "universe": {
        "fresh": {
            "max_pair_age_hours": 6,
            "min_liquidity_usd": 40000,
            "min_volume_m5_usd": 25000,
            "min_holders": 300,
            "min_mcap_usd": 0,
            "max_mcap_usd": 20000000,
            "dex_ids": ["raydium", "pumpswap", "meteora", "pumpfun", "orca", "launchlab", "meteoradbc"],
        },
        "migrated": {
            "max_pair_age_hours": 168,   # migrated within the last 7 days
            "min_liquidity_usd": 30000,
            "min_volume_m5_usd": 5000,
            "min_holders": 500,
            "min_mcap_usd": 150000,
            "max_mcap_usd": 50000000,
            "dex_ids": ["pumpswap", "raydium", "meteora", "orca"],
        },
        "copy": {
            "max_pair_age_hours": 720,
            "min_liquidity_usd": 20000,
            "min_volume_m5_usd": 0,
            "min_holders": 150,
            "min_mcap_usd": 50000,
            "max_mcap_usd": 100000000,
            "dex_ids": [],
        },
    },

    # per-strategy exit overrides (on top of "executor")
    "exits": {
        "fresh": {},
        "migrated": {"time_stop_min": 90, "hard_stop_pct": 15.0},
        "copy": {"time_stop_min": 360, "hard_stop_pct": 20.0},
    },

    # ---------------------------------------------------------------- scanner
    "scanner": {
        "interval_sec": 20,
        "volume_spike_mult": 5.0,     # m5 volume > 5x the average 5-minute volume of the last hour
        "min_buy_ratio": 0.70,        # buys / (buys + sells) over the last 5 minutes
        "holder_growth_pct": 10.0,    # holders grew >=10% since we first saw the token (within 30 min)
        "smart_money_window_min": 10, # tracked wallets that bought within N minutes
        "smart_money_min_wallets": 1, # 1 fitted wallet = copy signal; 3+ = cluster (counts as 2 signals)
        "min_signals": 2,
        "no_chase_h1_pct": 300.0,     # skip if already +300% in the last hour and still going up (no pullback)
        "max_candidates": 90,
    },

    # -------------------------------------------------------------- rug check
    "rugcheck": {
        "max_dev_pct": 5.0,
        "max_top10_pct": 35.0,
        "min_lp_locked_pct": 90.0,
        "require_no_mint_authority": True,
        "require_no_freeze_authority": True,
        "max_rugcheck_score": 5000,   # rugcheck.xyz raw risk score (lower is safer)
        "sell_simulation": True,      # ask Jupiter for a sell quote; no route = honeypot
    },

    # ------------------------------------------------------------------ sizer
    "sizer": {
        "min_pct": 0.20,
        "max_pct": 0.40,
        "max_open_positions": 2,
        "min_trade_eur": 5.0,         # below this, fees eat the trade
        "reserve_eur": 1.0,           # keep for network fees
        "loss_streak_cut": 2,         # after 2 losses in a row ...
        "loss_streak_factor": 0.5,    # ... size down 50%
    },

    # --------------------------------------------------------------- executor
    "executor": {
        "interval_sec": 5,
        "max_slippage_pct": 2.5,
        "split_entries": 2,
        "take_profit": [[2.0, 0.40], [4.0, 0.30]],   # sell 40% at 2x, 30% at 4x, rest runs
        "trailing_stop_pct": 25.0,     # runner trailing stop from its peak
        "hard_stop_pct": 12.0,
        "time_stop_min": 25,           # exit if TP1 not reached after 25 min
        "lp_pull_exit_pct": 40.0,      # exit if liquidity drops 40% from entry
        "exit_on_dev_sell": True,
        # paper cost model (Solana): network + priority fee per swap, and DEX fee
        "network_fee_sol": 0.0003,
        "dex_fee_pct": {"pumpfun": 1.25, "pumpswap": 0.30, "raydium": 0.25, "meteora": 0.30, "default": 0.50},
    },

    # ------------------------------------------------------------------ risk
    "risk": {
        "daily_drawdown_pct": 30.0,    # pause for the day if equity is -30% from the day's peak
    },

    # ----------------------------------------------------------- copy trading
    "wallets": {
        "interval_sec": 30,
        "tracked": ["BdLxn8cYq3FHNUqEN9dE2CYz2wc7BwCjL3gDrpsQRZ7y"],
        "copy_mode": "mirror",         # "mirror" = enter when a copy wallet buys (rug check still applies)
                                       # "signal" = a copy-wallet buy only counts as one scanner signal
        "mirror_sells": True,          # exit when the wallet we copied sells that coin
        "mirror_max_delay_sec": 180,   # ignore buys we notice later than this (price already moved)
        # "fits my balance" - no whales:
        "max_wallet_sol": 60.0,
        "min_median_buy_sol": 0.02,
        "max_median_buy_sol": 3.0,
        # "good wallet" - measured from its own on-chain trades:
        "min_closed_trades": 6,
        "min_winrate": 0.55,
        "min_pnl_sol": 0.5,
        "max_trades_per_day": 300,     # more = bot / MEV
        "min_median_hold_sec": 20,     # less = sniper bot
        "profile_tx": 80,              # transactions read when profiling a wallet
        "auto_discover": True,         # find wallets: GMGN leaderboard + top traders of coins that ran
        "discover_every_min": 10,
        "max_tracked": 40,
    },

    # -------------------------------------------------------- data sources
    "sources": {
        "dexscreener": True,
        "jupiter_trending": True,
        "pumpfun": True,
        "gmgn": True,                 # best effort: gmgn.ai is behind Cloudflare and may refuse
        "news_rss": [
            "https://cointelegraph.com/rss",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://decrypt.co/feed",
        ],
        "news_interval_sec": 300,
        "solana_rpc": "https://api.mainnet-beta.solana.com",  # put a Helius/QuickNode URL here for speed
        "x_bearer_token": "",         # optional X API v2 bearer token (paid plan) for $TICKER mention counts
        "cryptopanic_token": "",      # optional free CryptoPanic API token
    },
}


def app_dir() -> Path:
    """Folder that holds config.json, state.json and logs (next to the .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(os.environ.get("MEMEBOT_HOME", Path.cwd()))


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict:
    path = path or app_dir() / "config.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        if user.get("config_version", 1) >= CONFIG_VERSION:
            return _merge(DEFAULTS, user)
        # settings layout changed: keep the old file as a backup and write the new defaults
        try:
            path.replace(path.with_name(f"config.v{user.get('config_version', 1)}.bak.json"))
        except OSError:
            pass
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2)
    except OSError:
        pass
    return copy.deepcopy(DEFAULTS)
