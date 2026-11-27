# Futures Strategy Tester

Server-rendered Flask backtester for the Strategy Index v3.1 futures strategies. It uses Binance USDT-M perpetual klines (the public daily files on `data.binance.vision`), caches them, and reports a TradingView-style overview, an equity curve with a drawdown underlay, a price chart with entry and exit markers, and the full trade list.

Signal math is the v3.1 engine, run in Node from `js/v31`. Those files are the indicator and fill logic only. They do not contain a database URL, a webhook, or a key.

## Run

```bash
pip install -r requirements.txt
python3 -m gunicorn -w 1 -b 0.0.0.0:8765 app:app
```

`python3 app.py` starts the same app for local debugging. Open http://127.0.0.1:8765 . Choose a strategy, a venue and a symbol. Size `k` defaults to 8 (the live Pivex combo). The daily HAMA filter defaults to on and applies to Pivex entries. Starting balance defaults to $10,000. Commission defaults to 0.04% per side, which is what the engines charge. Slippage defaults to 0 because the v3.1 fills are exact.

```bash
python3 -m pytest
python3 -m backtest.report
```

`python3 -m backtest.report` prints net profit %, profit factor, max drawdown % and trade count for every strategy on BTC, ETH and SOL, once per venue that strategy runs on.

The first run for a symbol downloads about 70 days of 1-minute klines (40 days of warmup plus the 30-day test) from the public Binance archive at `data.binance.vision`. Nothing market-data is committed. Later runs on the same machine read `data/cache/`. Warmup only seeds indicators. The account starts flat at the test window, so a fill from the warmup is not a reported trade. Node 18 or newer is required locally. On Render the build script unpacks Node 22 into `.node/`.

## Render

Python 3.12.3 (`.python-version`). One gunicorn worker, so a free instance's 512 MB is enough for a single backtest. Market data is downloaded on the first Run and is not in the git tree. The Python image has no Node, and the signal engines need it, so the build installs both.

Build command:

```bash
pip install -r requirements.txt && bash scripts/install-node.sh
```

Start command:

```bash
gunicorn -w 1 -t 600 -b 0.0.0.0:$PORT app:app
```

## Backtest period

The **Backtest period** dropdown picks 1 month, 3 months, 6 months, 1 year or 3 years. 1–12 month periods end at the close of the last published UTC day on `data.binance.vision`; the start is that end minus 30, 91, 182 or 365 days, with 40 days of indicator warmup before it. 3 years starts on 2023-12-01 and ends at the last published day. 1 month is the old default window. Run and Start both use it; Ranking keeps its fixed 1-year board. Run is a background job (`POST /run/start`, poll `/run/<id>`, then `/run/<id>/view`) so a 1-year run is not cut off by a request timeout. Long charts switch to a coarser candle timeframe (at most 20,000 bars).

## HAMA 1h + Turtle 5m Combo

Binance only, Single run tab only (not in Start or Ranking). Entries are the official Reversal Turtle MTF 5m signals from `js/v31/combo.js`, kept only when the last completed 1h HAMA Stable bar (4h colour lock, 1h EMA 200 gate) is on the same side (`js/v31/combo_ht.js`). Exits are walked on 1m bars in `backtest/combo_ht.py`: official Turtle stop, half off at +3% then breakeven on the rest, rest to +6%, no add, stop first when a stop and a target share a minute. Fixed $10,000 book per trade.

## Venues

Pivex is single-entry. A position gets one full-size fill, one stop and one target. Adds and extra grid levels are off. A partial close is still a close, not a new entry. Pivex combo size is `unit × weight × k`.

Binance keeps the full multi-entry logic: the Turtle +3% add, the S5 add and partial TP1, and the DG, ER and HAMA Grid+Count ladders. Binance is not scaled by `k`. Where v3.1 has a Binance card, that card is used (S5 $22.09 doubling to 5×, DG flat $1,000 a level, ER $0.360 a step up to 10). Otherwise Binance uses the strategy's unit size.

DG, ER and both HAMA Grid+Count strategies are Binance-only. Every other strategy can be run on either venue, so the table has a Pivex single-entry row and a Binance row side by side.

## Strategies

| Strategy | What it does |
| --- | --- |
| Pivex Micro · 1h | Seq5 on 1h, stop 1.5 ATR, target 3 ATR. BTC daily RSI blocks longs below 30 and shorts above 70. One position at a time. |
| Reversal Turtle MTF · 5m | 30m Turtle bias. Binance enters half and adds the other half at +3%, with TP1 then TP2. Pivex enters the whole book once and uses the 6% target. |
| ML Lorentz · 5m | Lorentzian kNN, stop 3%, target 3.1%, 300-bar time stop. One position. |
| HAMA 30m | HAMA Stable, 1h lock, EMA200 filter, swing stop. Flat risk. The live book is ETH and BNB. |
| HAMA 1h | Same exits, up to 8 loss steps. The live app leaves this off until its box is checked. It stays selectable here. |
| S5 15m | Extreme reversal plus the 30m Turtle bias. Binance can add and TP1 closes only the first tranche. Pivex does not add. |
| SOL 1m Morning HAMA | 08:00 Kyiv day filter, ±1% to ±3%. Fixed $40,000, $0.06 spread, −$100 stop. Not scaled by k. |
| DG 1h/1m | Binance only. Flat $1,000 a level, 8 levels. An unload closes only the newest level. |
| ER 15m/1m | Binance only. Thirds-carry grid, $0.360 a step, up to 10 steps. |
| HAMA Grid+Count 5m and 1m | Binance only. $1,000 a level, colour-count filter. |

BTC, ETH and SOL are always in the symbol list. Each strategy also lists its live coins.

Three research strategies (HAMA Scale-in Ladder, 1h Alpha Wave Scalper + HAMA, DG Grid + flow) are precomputed on BTC, ETH, SOL, BNB and XRP for 1 year (2025-11-27 → 2026-11-27) and 3 years (2023-12-01 → now). They appear in the strategy list and the Ranking tab (switch the Ranking period to 3 years). P&L % is the sum of per-trade % of a $100 unit; fees of 0.04%/side are already in the nets.

## Layout

- `app.py` — Flask UI
- `backtest/v31.py` — loads klines and runs the Node engines
- `js/v31/` — extracted v3.1 signal engines
- `backtest/config.py` — venues, coins, and the written rules
- `backtest/data.py` — futures kline download, cache, resample
- `tests/` — indicators, risk cards, and engine parity
