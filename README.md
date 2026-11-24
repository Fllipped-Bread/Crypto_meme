# MemeBot Terminal

A Windows app (`MemeBot.exe`) that scans new Solana meme coins on live data and **paper-trades** them with a 50 € starting balance. It works like the "overnight bot" setup: six agents, each with one job, and a live dashboard in your browser that shows why every decision was made.

> **Paper mode only.** No wallet is connected and no real money moves. All market data is live, and every trade is a simulated fill at the live price, with Solana fees and slippage included.

```
SCANNER  ->  RUG CHECK  ->  SIZER  ->  EXECUTOR  ->  LOGGER  ->  EQUITY
 flags        gate: no       % of       paper fills,   every       balance, curve,
 only         PASS, no       equity,    TP ladder,     decision    -30% daily
              trade          never      stops          has a       drawdown pause
                             fixed                     reason
                    + COPY WALLETS (small wallets sized like yours, no whales)
```

## Getting the .exe

1. Open the repo on GitHub, go to the **Actions** tab, and open the latest **Build Windows .exe** run.
2. Download the **MemeBot-windows** artifact (a zip containing `MemeBot.exe`).
3. Put `MemeBot.exe` in its own folder and double-click it. Your browser opens the dashboard at `http://127.0.0.1:8765`.

Windows SmartScreen may warn you because the .exe is unsigned. Click "More info", then "Run anyway".

On first launch the app creates these files next to the .exe:

| File | What it is |
|---|---|
| `config.json` | every filter and setting (edit it, then restart) |
| `state.json` | paper balance, open positions, trade history, tracked wallets (survives restarts) |
| `night.log` | one line per ENTER / EXIT / FILTERED / COPY decision, each with its reason |

To run from source instead: `pip install -r requirements.txt`, then `python run.py`.

## Live data sources

| Source | Used for | Key needed |
|---|---|---|
| **DexScreener** | new token profiles, boosts (paid promotion / KOL proxy), price, liquidity, 5m/1h volume, buys/sells, pair age | no |
| **pump.fun** | live coins, graduated (migrated) coins, king-of-the-hill | no (unofficial API) |
| **GMGN** | 1h swap rank, holder count, smart-money and KOL counts | no (unofficial; Cloudflare often blocks it, and the bot carries on without it) |
| **RugCheck.xyz** | mint/freeze authority, top-10 holders, dev %, LP locked/burned, insider clusters, holder count | no |
| **Jupiter** | sell-quote simulation (no route = honeypot) | no |
| **Solana RPC** | copy-wallet trades, wallet balances, fallback safety data | no (public RPC is slow; a free Helius URL is much better) |
| **News RSS** (CoinTelegraph, CoinDesk, Decrypt) | headline mentions of a token | no |
| **X (Twitter)** | `$TICKER` mentions per hour | optional `x_bearer_token` (X API is paid) |
| **CryptoPanic** | hot crypto news | optional free token |
| **CoinGecko** | SOL price and EUR/USD rate | no |

**Phantom** is a wallet, not a data feed. It has no public API for trending tokens, so the dashboard links each token to its Phantom page (along with DexScreener, GMGN, pump.fun, RugCheck and X). Phantom matters later, when a live-trading mode is added.

The **data sources** panel shows each source as green or red. Hover over a red one to see why it failed. If a source is down, the bot keeps running on the others.

## The rules (defaults, all editable in `config.json`)

**Universe**
- Pair launched or migrated in the last **6 h** (pump.fun, PumpSwap, Raydium, Meteora, Orca)
- Liquidity ≥ **$40k**, 5-minute volume ≥ **$25k**, ≥ **300 holders**

**Scanner signals** (a token needs **2 or more** to be flagged)
- `volume_spike`: 5m volume is more than 5× the average 5-minute volume of the last hour
- `buy_pressure`: at least 70% buys in the last 5 minutes
- `holder_growth`: holder count up 10% or more since first seen
- `copy_wallet` / `smart_cluster`: 1 fitted copy wallet bought in the last 10 minutes (3 or more counts double)
- `smart_money`: GMGN smart-money count of 3 or more
- `kol_boost`: DexScreener boost or GMGN KOL
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
- Exits at once if liquidity drops 40% (LP pulled) or the dev wallet sells

**Equity**
- If equity falls 30% from the day's peak, new entries pause until the next day
- A recap prints at 07:00 (or click **Recap**): start vs end balance, best and worst trade, total trades, top 3 coins

## Copy trading: wallets that fit your balance

The wallet you gave (`BdLxn8cYq3FHNUqEN9dE2CYz2wc7BwCjL3gDrpsQRZ7y`) is tracked by default. Add more from the dashboard.

Each wallet is profiled from its own on-chain swaps:
- **whale**: more than 60 SOL in the wallet, or a median buy over 3 SOL. Ignored.
- **dust**: median buy under 0.02 SOL. Ignored.
- **copy**: small wallet whose trade size fits yours. Its buys become signals.
- **watch**: an auto-discovered wallet that is still being scored

**Auto-discover**: when one of the bot's trades runs (finishes its ladder or hits the trailing stop), the bot reads the earliest buyers it can reach in that pool and starts watching them. Every buy a wallet makes is scored 30 minutes later (up 10% or more counts as a hit). After 5 scored buys with a hit rate of 50% or more, the wallet is promoted to **copy**.

`copy_mode`:
- `"signal"` (default): a copy-wallet buy counts as one scanner signal, so another signal is still needed
- `"mirror"`: enter when a copy wallet buys (rug check still required), sized to the **same % of balance** the wallet used. If it put 5% of its SOL in, you put 5% of your euros in (clamped to 20–40%).

## Tips

- Set `"solana_rpc"` to a free [Helius](https://www.helius.dev/) RPC URL. The public RPC is heavily rate-limited, which makes wallet tracking slow.
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

Run the tests with `pytest -q`. Every push builds `MemeBot.exe` on GitHub Actions. Pushing a tag like `v1.0.0` also attaches the .exe to a GitHub Release.
