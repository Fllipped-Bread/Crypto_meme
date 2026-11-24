# MemeBot Terminal

A Windows app (`MemeBot.exe`) that **copies proven wallets** and trades **migrated** Solana meme coins on live data, as **paper trades** with a 50 € starting balance. It works like the "overnight bot" setup: six agents, each with one job, and a live dashboard in your browser that shows why every decision was made.

> **Paper mode only.** No wallet is connected and no real money moves. All market data is live, and every trade is a simulated fill at the live price, with Solana fees and slippage included.

```
SCANNER  ->  RUG CHECK  ->  SIZER  ->  EXECUTOR  ->  LOGGER  ->  EQUITY
 flags        gate: no       % of       paper fills,   every       balance, curve,
 only         PASS, no       equity,    TP ladder,     decision    -30% daily
              trade          never      stops          has a       drawdown pause
                             fixed                     reason
                    + COPY WALLETS (small wallets sized like yours, no whales)
```

## Put it on your desktop and run it

Open **PowerShell** (Start menu, type `powershell`, press Enter), paste this line and press Enter:

```powershell
irm https://raw.githubusercontent.com/Fllipped-Bread/Crypto_meme/claude/brave-hopper-etvpla/install-desktop.ps1 | iex
```

This creates a `MemeBot` folder and a `MemeBot` shortcut on your desktop, then starts the bot. Your browser opens the dashboard at `http://127.0.0.1:8765`. Later, double-click the shortcut to start it. Run the same line again to update; your settings and paper history are kept.

If Windows SmartScreen warns you (the .exe is unsigned), click "More info", then "Run anyway".

Manual download instead: <https://github.com/Fllipped-Bread/Crypto_meme/releases/download/latest/MemeBot.exe>. Put it in its own folder and double-click it.

The app keeps its files next to the .exe:

| File | What it is |
|---|---|
| `config.json` | every filter and setting (edit it, then restart) |
| `state.json` | paper balance, open positions, trade history, tracked wallets (survives restarts) |
| `night.log` | one line per ENTER / EXIT / FILTERED / COPY decision, each with its reason |

To run from source instead: `pip install -r requirements.txt`, then `python run.py`.

## Live data sources

| Source | Used for | Key needed |
|---|---|---|
| **Jupiter** | trending tokens (1h) | no |
| **DexScreener** | token profiles, boosts (paid promotion / KOL proxy), price, liquidity, 5m/1h volume, buys/sells, pair age | no |
| **pump.fun** | live coins, graduated (migrated) coins, king-of-the-hill | no (unofficial API) |
| **GMGN** | top-PnL wallet leaderboard, 1h swap rank, holder count, smart-money and KOL counts | no (unofficial; Cloudflare often blocks it, and the bot carries on without it) |
| **RugCheck.xyz** | mint/freeze authority, top-10 holders, dev %, LP locked/burned, insider clusters, holder count | no |
| **Jupiter** | sell-quote simulation (no route = honeypot) | no |
| **Solana RPC** | copy-wallet trades, wallet balances, fallback safety data | no (public RPC is slow; a free Helius URL is much better) |
| **News RSS** (CoinTelegraph, CoinDesk, Decrypt) | headline mentions of a token | no |
| **X (Twitter)** | `$TICKER` mentions per hour | optional `x_bearer_token` (X API is paid) |
| **CryptoPanic** | hot crypto news | optional free token |
| **CoinGecko** | SOL price and EUR/USD rate | no |

**Phantom** is a wallet, not a data feed. It has no public API for trending tokens, so the dashboard links each token to its Phantom page (along with DexScreener, GMGN, pump.fun, RugCheck and X). Phantom matters later, when a live-trading mode is added.

The **data sources** panel shows each source as green or red. Hover over a red one to see why it failed. If a source is down, the bot keeps running on the others.

## Strategies

Set in `config.json` under `"strategies"`:

| Strategy | Default | What it trades |
|---|---|---|
| `copy` | **on** | Enters when a proven wallet (good win rate and PnL, small enough to match you) buys, and exits when that wallet sells. |
| `migrated` | **on** | Coins that graduated from pump.fun or another launchpad into a real DEX pool (PumpSwap, Raydium, Meteora, Orca) in the last 7 days. Needs mcap ≥ $150k, liquidity ≥ $30k and ≥ 500 holders. Coins still on a bonding curve are skipped. |
| `fresh` | off | Brand-new pairs under 6 hours old (the original overnight setup) |

## The rules (defaults, all editable in `config.json`)

**Scanner signals** (a token needs **2 or more** to be flagged)
- `volume_spike`: 5m volume is more than 5× the average 5-minute volume of the last hour
- `buy_pressure`: at least 70% buys in the last 5 minutes
- `holder_growth`: holder count up 10% or more since first seen
- `copy_wallet` / `smart_cluster`: 1 fitted copy wallet bought in the last 10 minutes (3 or more counts double)
- `smart_money`: GMGN smart-money count of 3 or more
- `kol_boost`: DexScreener boost or GMGN KOL
- `trending`: on Jupiter's 1h trending list
- `fresh_migration`: graduated from pump.fun less than 1 hour ago
- `news` / `x_mentions`: token named in headlines, or 50+ tweets in the last hour
- No chasing: skip a coin that is already up 300% in the last hour and still rising

**Rug check** (nothing executes without a PASS)
- Mint authority revoked, no freeze authority
- Top 10 holders under 35%, dev under 5%, LP at least 90% burned or locked
- Fewer than 3 insider clusters (filters bundled launches), no "danger" risks on RugCheck
- Jupiter sell quote exists (not a honeypot)

**Sizer** (built for 50 €)
- 20 to 40% of current equity, scaled by the number of signals. Never a fixed ticket; profits compound.
- At most **2 open positions**, minimum **5 €** per trade (smaller trades lose too much to fees), 1 € kept back for network fees
- Size cut by 50% after 2 losses in a row

**Executor** (paper fills with real costs)
- Entry split into 2 fills. The trade is skipped if estimated slippage is over 2.5%.
- Costs per swap: DEX fee (pump.fun 1.25%, PumpSwap 0.3%, Raydium 0.25%), network + priority fee, and price impact from the pool's liquidity
- Take-profit ladder: sell **40% at 2x**, **30% at 4x**; the last 30% runs with a **25% trailing stop**
- Hard stop **−12%**. Time stop **25 min** if 2x hasn't been reached.
- Migrated trades: hard stop −15%, time stop 90 min. Copy trades: hard stop −20%, time stop 6 h, plus an exit when the copied wallet sells.
- Exits at once if liquidity drops 40% (LP pulled) or the dev wallet sells

**Equity**
- If equity falls 30% from the day's peak, new entries pause until the next day
- A recap prints at 07:00 (or click **Recap**): start vs end balance, best and worst trade, total trades, top 3 coins

## Copy trading: good wallets that fit your balance

The bot doesn't take a win rate on anyone's word, GMGN's included. It reads each wallet's own trades from the chain (up to 80 past transactions), then works out:

- **closed trades**: coins it bought and then sold (90% or more of the position)
- **win rate**: share of closed trades that returned more SOL than they cost
- **PnL**: total SOL made or lost on closed trades
- median buy size, median hold time, trades per day, and SOL balance

| Status | Meaning | Copied? |
|---|---|---|
| **copy** | win rate ≥ 55%, PnL ≥ +0.5 SOL, at least 6 closed trades | yes |
| watch | fewer than 6 closed trades so far | not yet |
| poor | enough trades, but win rate or PnL below the bar | no |
| whale | more than 60 SOL, or median buy over 3 SOL (doesn't fit a 50 € account) | no |
| bot | more than 300 trades a day, or holds for under 20 s (sniper / MEV) | no |
| dust | median buy under 0.02 SOL | no |

**Where wallets come from**
- Your list. `BdLxn8cYq3FHNUqEN9dE2CYz2wc7BwCjL3gDrpsQRZ7y` is included by default; add more from the dashboard. Your own wallets are copied until their history proves them poor.
- The **GMGN 7-day PnL leaderboard** (hourly, when GMGN answers). Whales and low win rates are dropped first, and the rest are verified on-chain.
- **Traders of coins that ran**: when a migrated coin is up 100% or more in 6 hours, the bot collects wallets that traded it and profiles them.

**Mirroring** (`copy_mode: "mirror"`, the default)
- When a **copy** wallet buys, the bot enters too, if the coin passes the rug check and has at least $20k liquidity. Buys noticed more than 3 minutes late are skipped.
- The bot puts in the **same share of its balance** that the wallet used: 5% of the wallet's SOL means 5% of your euros, clamped to 20–40% and at least 5 €.
- When that wallet sells the coin, the bot sells too.

## Tips

- Set `"solana_rpc"` to a free [Helius](https://www.helius.dev/) RPC URL. **Strongly recommended for copy trading.** The public RPC is heavily rate-limited, so profiling wallets and noticing their buys is slow.
- To trade only overnight, set `"active_hours": [23, 7]`.
- **Reset paper** on the dashboard starts over with any balance.

## Honest warnings

- Most fresh meme coins go to zero. Filters cut the obvious rugs; they can't make the strategy profitable. Run paper mode for a good while and look at the log before you think about real money.
- pump.fun and GMGN endpoints are unofficial and can change or block requests at any time.
- Paper fills assume you get the live DexScreener price plus modeled slippage. Real fills on fast coins are often worse.
- This is not financial advice.

## Development

```
memebot/
  agents.py      scanner signals, rug gate, sizer, paper executor, journal (pure logic, unit tested)
  engine.py      wires agents to live data, background loops
  wallets.py     copy-wallet tracking, whale filter, scoring, discovery
  server.py      local dashboard server (127.0.0.1 only)
  web/index.html dashboard
  sources/       dexscreener, pumpfun, gmgn, rugcheck, solana, news, fx
tests/           pytest
```

Run the tests with `pytest -q`. Every push builds `MemeBot.exe` on GitHub Actions. Each push also updates the `latest` download that `install-desktop.ps1` uses.
