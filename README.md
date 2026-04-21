# Day Trading Analysis System

Three connected tools that surface high-probability U.S. equities day trading
ideas and keep risk under control — **no auto-execution**.

1. **Gap-and-Volume Scanner** — which stocks are in play today?
2. **Intraday Setup Recognition Dashboard** — what setup is forming, right now?
3. **Risk Management Engine** — how big should the trade be, and am I allowed to take it?

The MVP is a FastAPI backend with a small vanilla-JS frontend. Everything is
modular so the three tools can later be combined into a single dashboard and
extended with news, journaling, backtesting, and regime detection.

---

## 1. Executive summary

Day trading profitability is mostly about: picking the right stocks, entering
on clean setups, and controlling losers. The three programs correspond directly
to those three jobs.

- **Program 1** narrows the universe from ~8,000 symbols to ~10-30 "in play"
  names using gap % and relative volume, with liquidity and price-band filters.
- **Program 2** watches those names intraday and fires when a named setup
  (ORB, VWAP reclaim/rejection, pullback, level break) triggers. Each signal
  comes with an explicit entry / stop / first target and a plain-English reason.
- **Program 3** turns a proposed setup into a concrete position size, and
  enforces per-trade, per-day, and per-account risk rules before a human
  clicks the trade button.

All three share a `DataProvider` interface, pydantic models, and a FastAPI
surface. The scanner and setup detector are stateless; the risk engine holds
the only mutable trading-day state.

## 2. Recommended architecture (MVP)

```
┌────────────────────────────────────────────────────────────┐
│ Frontend (static HTML/JS)                                  │
│   - scanner table                                          │
│   - setups panel                                           │
│   - risk/size form                                         │
└───────────────────────┬────────────────────────────────────┘
                        │ JSON
┌───────────────────────▼────────────────────────────────────┐
│ FastAPI (backend/main.py, backend/api/routes.py)           │
│                                                            │
│  ┌───────────────┐  ┌─────────────────┐  ┌──────────────┐  │
│  │ Scanner       │  │ Setup Detector  │  │ Risk Engine  │  │
│  │ (stateless)   │  │ (stateless)     │  │ (stateful)   │  │
│  └──────┬────────┘  └────────┬────────┘  └──────┬───────┘  │
│         │                    │                  │          │
│         └──── DataProvider (interface) ─────────┘          │
│              ↑                    ↑                        │
│     MockProvider (dev)   PolygonProvider (prod)            │
└────────────────────────────────────────────────────────────┘
                        │
                        ▼
              SQLite / Postgres (phase 2)
              — scan_results, setup_events, trades
```

Key design principles:

- **Swap vendors without touching logic** — Polygon, Alpaca, IEX, Tradier, etc
  all implement the same `DataProvider` ABC.
- **Mock provider in dev** — the system runs end-to-end today without any
  API keys, which keeps the build loop fast.
- **No scheduler in MVP** — clients poll. When you want true intraday push,
  add a WebSocket endpoint and a background task per watchlist symbol.
- **Risk is pre-trade, not in-trade** — the engine evaluates a proposed
  trade; it doesn't manage open orders. That's broker territory.

## 3. Detailed specs

### Program 1 — Gap-and-Volume Scanner

**Inputs**

| Input | Default | Source |
|---|---|---|
| `min_gap_pct` | 3.0 | UI / settings |
| `min_relative_volume` | 2.0 | UI / settings |
| `min_premarket_volume` | 50,000 | settings |
| `min_price` / `max_price` | 2 / 2,000 | settings |
| `min_avg_dollar_volume` | $10M | settings |
| universe | ~2,000-8,000 US tickers | provider.list_universe() |

**Data sources needed** (pick one vendor for MVP):

- Polygon.io Stocks Starter: universe, prev-day aggs, 20-day history, minute
  bars, snapshot endpoint (premarket). Good single-vendor MVP.
- Alpaca Market Data: similar coverage, free for personal use.
- IEX Cloud: works, but premarket data quality is weaker.
- Nasdaq Basic / SIP: full tape, but expensive.

**Scanner logic (see `backend/scanner/scanner.py`)**

```python
for symbol in universe:
    stats = provider.get_daily_stats(symbol, today)
    if stats.avg_dollar_volume < MIN_ADV:          # liquidity
        continue
    if not (MIN_PRICE <= stats.prev_close <= MAX_PRICE):  # price band
        continue

    snap = provider.get_premarket_snapshot(symbol, today)
    if snap is None or snap.premarket_volume < MIN_PM_VOL:
        continue

    gap_pct = (snap.last - stats.prev_close) / stats.prev_close * 100
    if abs(gap_pct) < MIN_GAP_PCT:
        continue

    expected_pm = stats.avg_volume_20d * 0.05      # ~5% trades premarket
    rvol = snap.premarket_volume / expected_pm
    if rvol < MIN_RVOL:
        continue

    yield rank(gap_pct, rvol, atr_pct, adv)
```

**Ranking formula** (`backend/scanner/ranking.py`):

```
score = 0.35 * clip(|gap%|, 25) / 25 * 100
      + 0.35 * clip(rvol, 15) / 15 * 100
      + 0.15 * clip(atr%, 15) / 15 * 100
      + 0.15 * liquidity_bonus
```

Why: gap alone attracts attention, but without RVOL it's often just a fluke.
ATR% ensures the name actually moves during the day. Liquidity prevents
ranking thin names at the top where fills are hostile.

**Filters**
- Liquidity: 20-day avg dollar volume ≥ $10M.
- Price band: $2 ≤ prev_close ≤ $2,000.
- Premarket volume floor: 50,000 shares.
- Exclude ETFs/ETNs/OTC in a v1.1 filter (symbol metadata).

**Suggested UI**
- Table: `Symbol | Price | Gap% | RVOL | Premkt Vol | ATR | Score | Bias`.
- Color-code: green for up-gaps, red for down-gaps, dim rows below score 40.
- Click row → loads Setups panel for that symbol.
- Controls: min gap slider, min RVOL slider, refresh button.

**Alert conditions**
- New entry into top-10 since last scan.
- RVOL spikes >5x intraday for a symbol already on watchlist.
- Gap closes or widens by ≥30% since last scan (re-ranking signal).

### Program 2 — Intraday Setup Recognition Dashboard

**Pattern detection rules** (each rule is in `backend/setups/detector.py`):

| Setup | Trigger | Entry | Stop | T1 |
|---|---|---|---|---|
| **Opening Range Breakout** | close crosses 5min OR high (long) or low (short) with volume | OR high/low | opposite OR extreme | entry ± 2R |
| **VWAP Reclaim** | price was below VWAP ≥3 bars, latest close back above | VWAP | last 5-bar low | entry + 2R |
| **VWAP Rejection** | bars below VWAP, one wick tags VWAP (<0.3%), red close | last close | last 5-bar high | entry − 2R |
| **Pullback in trend** | ≥7 of last 10 closes above VWAP, last 3 bars kiss but hold VWAP, current bar up-close > prior high | last close | pullback low | entry + 2R |
| **Level Break** | close crosses prior-day high/low | level | last 5-bar low/high | entry ± 2R |

**Required chart/timeframe data**
- 1-minute bars for the current session.
- Previous-day H/L/C for level-break rules.
- Cumulative session VWAP (recomputed on each bar).
- Rolling 10-bar median volume for the volume-confirmation filter.

**Alert logic**
- Each detector returns `None` or a `SetupSignal`. A signal stays "fresh" for
  N bars (configurable; 3 default) to avoid re-firing the same break.
- UI polls `/api/setups/{symbol}` every 10s for active watchlist symbols.

**Setup quality score (0-100)**

```
score = 40 * min(trigger_vol / median_vol(10), 3) / 3
      + 30 * (1 - overlap_density)     # clean structure
      + 30 * close_position_in_bar     # 1 = closed at high (long), 0 = low
```

**Suggested layout**
- Left column: scanner table (same page).
- Right column top: selected symbol's setups as cards (entry/stop/T1/reasons).
- Right column bottom: risk/size form, pre-filled by clicking "use in sizer".

**Keeping false signals low in v1**
1. Require the trigger bar volume ≥ 1.5× the median of the last 10 bars.
2. Require ≥ 6 bars before any signal (09:36 ET minimum).
3. Reject setups whose structural stop > 1.5× intraday ATR — bad R:R traps.
4. Let only one signal per setup type per symbol per "freshness window".

### Program 3 — Risk Management Engine

**Core formulas** (`backend/risk/engine.py`):

```
risk_per_share         = |entry − stop|
dollar_risk_budget     = account_equity × (risk_per_trade_pct / 100)
raw_shares             = floor(dollar_risk_budget / risk_per_share)

max_notional_cap       = account_equity × (max_position_pct_of_equity / 100)
exposure_cap_shares    = floor(max_notional_cap / entry)

shares                 = min(raw_shares, exposure_cap_shares, user_max_shares?)
dollar_risk            = shares × risk_per_share
dollar_exposure        = shares × entry
```

**Inputs (PositionSizeRequest)**
- `account_equity` ($)
- `risk_per_trade_pct` (0.01–5.0)
- `entry`, `stop`, `side`
- `atr` (optional — triggers tight/wide stop warnings)
- `max_shares` (optional hard cap)

**Risk rules**
- Per-trade risk ≤ `max_risk_per_trade_pct` of equity (default 1%).
- Per-position notional ≤ `max_position_pct_of_equity` (default 25%).
- Daily realized + unrealized loss ≤ `max_daily_loss_pct` (default 3%).
- Max concurrent open positions (default 5).
- Revenge-trade cool-off: after N consecutive losers (default 2), block new
  trades for M minutes (default 15).
- Trading-day auto-reset at midnight.

**Sample calculations**

```
Equity: $25,000, risk/trade: 1% → budget: $250
Entry: $50.00, stop: $49.50     → risk/share: $0.50
Raw shares: 500
Max notional: 25% × 25k = $6,250 → exposure cap: 125 shares
→ size = 125 shares, dollar_risk = $62.50 (capped by exposure)
```

```
Equity: $50,000, risk/trade: 0.75% → budget: $375
Entry: $180.00, stop: $178.10      → risk/share: $1.90
Raw shares: 197
Max notional: 25% × 50k = $12,500 → exposure cap: 69 shares
→ size = 69 shares, dollar_risk = $131.10
```

**Recommended UI components**
- Top-bar risk strip (equity, PnL, open positions, loss-left).
- Position-size form inline with setup cards (auto-populates from signals).
- Big red HALT banner with reasons when the engine is in a blocking state.
- "Why am I blocked?" modal listing each triggered rule.

**Account-level protections to include**
- Daily loss hard stop (can't be disabled intraday; requires cool-off).
- Max positions (prevents over-diversification panic).
- Consecutive-loss cool-off (revenge trading).
- Max notional per position (prevents an oversized accidental fill).
- Manual HALT button (discretionary stop-trading switch).

## 4. Tech stack

- **Backend**: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 (phase 2),
  SQLite for dev → Postgres in prod. `httpx` for vendor calls.
- **Frontend (MVP)**: vanilla HTML/CSS/JS — 3 files, no build step. Move to
  React/Next.js + Tailwind when the dashboard grows.
- **Realtime (phase 2)**: FastAPI WebSockets push setup events to the browser.
  Use `arq` or `dramatiq` for intraday scanning workers.
- **Data**: Polygon.io Stocks Starter ($29/mo) or Alpaca Market Data. Both
  cover the US equity surface well enough for day trading.
- **Charts (phase 2)**: TradingView Lightweight Charts — fast, free, and
  sufficient for 1m bars + VWAP overlay.
- **Deployment**: a single container on Fly.io / Render / Railway; SQLite
  volume for phase 1, managed Postgres in phase 2.

## 5. Database / schema outline

SQLAlchemy models are in `backend/db/models.py`. Three tables for MVP:

- `scan_results(id, symbol, trading_day, price, gap_pct, relative_volume,
  premarket_volume, avg_dollar_volume, atr_14d, score, reasons, side_bias,
  created_at)` — one row per scan hit; indexed on `(trading_day, symbol)`.
- `setup_events(id, symbol, trading_day, setup, side, entry, stop, target1,
  risk_per_share, score, reasons, triggered_at)` — append-only.
- `trades(id, symbol, trading_day, side, entry, stop, shares, exit_price,
  pnl, opened_at, closed_at, setup, notes)` — filled by user or broker feed.

Later phases add: `journal_notes`, `news_events`, `watchlist`, `user_prefs`.

## 6. API endpoint outline

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/api/scanner?min_gap_pct=&min_rvol=&top_n=` | run scanner |
| GET | `/api/setups/{symbol}` | detect setups for one symbol |
| POST | `/api/risk/size` | size position from entry/stop |
| POST | `/api/risk/check` | pre-trade check for a proposed dollar risk |
| GET | `/api/risk/status` | current daily risk state |
| POST | `/api/risk/halt` | manual halt-trading |
| POST | `/api/risk/configure` | update risk config at runtime |

Phase 2: `POST /api/watchlist`, `GET /api/watchlist`,
`WS /api/stream/setups?symbols=...`, `POST /api/trades` (record fills).

## 7. UI / dashboard wireframe

```
┌──────────────────────────────────────────────────────────────┐
│ Day Trading Console         [equity $25k · PnL +120 · … ]    │
├─────────────────────────────────────┬────────────────────────┤
│ 1. Gap & Volume Scanner             │ 2. Setups (NVDA)       │
│ [min gap 3%] [min RVOL 2] [Run]     │ ┌────────────────────┐ │
│ ┌──────────────────────────────────┐│ │ ORB long · score88 │ │
│ │ NVDA  820  +4.2% 6.1x 2.1M 18.2  ││ │ entry 822 stop 819 │ │
│ │ TSLA  243  +3.1% 3.0x 1.3M 8.5   ││ │ t1 828             │ │
│ │ AMD    95  −3.5% 2.4x  900k 3.1  ││ │ reasons: …         │ │
│ │ …                                ││ │ [use in sizer →]   │ │
│ └──────────────────────────────────┘│ └────────────────────┘ │
│                                     ├────────────────────────┤
│                                     │ 3. Position Sizer      │
│                                     │ equity 25000           │
│                                     │ risk% 1.0              │
│                                     │ entry 822 stop 819     │
│                                     │ side long [Size it]    │
│                                     │ → 83 shares · $249 risk│
└─────────────────────────────────────┴────────────────────────┘
```

## 8. Step-by-step build roadmap

**Phase 0 — scaffold (this commit)**
- Repo structure, mock data provider, scanner, detector, risk engine, API,
  minimal frontend, unit tests. Runs end-to-end with zero API keys.

**Phase 1 — real data, paper-tradeable**
- Implement `PolygonProvider` (or `AlpacaProvider`). Replace mock in settings.
- Cache 20-day daily stats per symbol (Redis or in-process TTL) to keep the
  scanner under 2-3s.
- Persist `scan_results` and `setup_events` to SQLite.
- Add simple watchlist endpoints and a "last scan" cache.

**Phase 2 — intraday push + journaling**
- Background worker ingests 1m bars for the current watchlist.
- WebSocket stream emits setup events as they fire.
- Trade journal: record open/close + notes; build a daily review page.
- Add prior-day H/L properly (currently proxied from ATR).

**Phase 3 — additional filters + charts**
- News filter: link each symbol to the most recent news catalyst.
- Liquidity analysis: intraday quote spread, dollar volume per 5m.
- Lightweight charts embed with entry/stop/T1 drawn on the bars.

**Phase 4 — backtesting + market regime**
- Replay scanner + setups over historical days.
- Track win rate / expectancy per setup type by gap bucket.
- Market regime detector (trend day / range day / chop) gates which setups
  the dashboard emphasizes.

## 9. Risks, blind spots, and what NOT to build yet

**What to skip in the MVP**
- Options, futures, or crypto. Focus on US equities.
- Auto-execution / broker integration. Different blast radius entirely.
- Fancy ML pattern recognition. Rule-based is good enough and debuggable.
- A "backtest everything" framework. You don't yet know which rules work.
- Multi-user authentication. Assume one trader for v1.

**Risks and blind spots**
- **Data lag / partial quotes premarket.** Low-price illiquid names have
  stale snapshots. Hard liquidity floor + price band mitigates this.
- **Survivorship bias** in mock data — real markets have rug-pulls, halts,
  and circuit breakers. Your backtests must replay halts.
- **VWAP resets** during halts. The cumulative VWAP in the MVP does not
  handle halts — fine for MVP, fix when adding real bars.
- **Look-ahead bias** — never score a setup with data from future bars.
  The current detector is strictly causal, but easy to break later.
- **Over-fitting scanner weights.** The ranking formula has 4 weights; tune
  only after collecting at least 20 trading days of labeled outcomes.
- **False confidence from position sizing.** The risk engine assumes your
  stop holds. Slippage + gaps can double the real loss. Keep per-trade
  risk small and daily-loss limits tight.
- **Broker reality.** Shorting requires borrow; hard-to-borrow names won't
  be short-able at the price shown. Mark a symbol short-available flag
  once you have a broker integration.

## 10. Questions to ask — and the assumptions I made to keep moving

1. Which data vendor will you pay for first? *Assumed: Polygon.io Stocks
   Starter for production, mock for dev.*
2. Account size and realistic risk per trade? *Assumed: $25k, 1% risk/trade,
   3% daily loss cap, 5 open positions max.*
3. Long and short? *Assumed: yes, both, with side-bias from the gap.*
4. Do you want a tight 5-bar ORB or 15-bar ORB? *Assumed: 5-bar.*
5. Target R-multiple for T1? *Assumed: 2R.*
6. Premarket share of daily volume for RVOL normalization? *Assumed: 5%.*
7. Universe — Russell 3000 only, or full NYSE+Nasdaq? *Assumed: provider's
   full active-equity list, filtered by liquidity floor.*
8. Do you want alerts by sound, browser, Discord, SMS? *Assumed: none in
   MVP; browser UI only, poll every ~10s.*
9. Is the goal personal use, or multi-user? *Assumed: personal use.*

Flip any of these in `backend/config.py` and re-run — the system is
config-driven end-to-end.

---

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
# open http://localhost:8000
```

Running the tests:

```bash
pytest -q
```

Switch to a real data vendor:

```bash
export APP_DATA_PROVIDER=polygon
export APP_POLYGON_API_KEY=...
```

## Repo layout

```
backend/
  main.py               # FastAPI app
  config.py             # env-driven settings
  api/routes.py         # HTTP surface
  data/                 # provider ABC + mock/polygon impls
  scanner/              # Program 1 — gap & volume
  setups/               # Program 2 — setup detection + indicators
  risk/                 # Program 3 — risk engine
  db/models.py          # SQLAlchemy (phase 2)
  models/schemas.py     # shared pydantic models
frontend/
  index.html, styles.css, app.js
tests/
  test_scanner.py, test_setups.py, test_risk.py
```
