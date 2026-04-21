# OpenClaw Paper Trading Server

A locally-hosted FastAPI server (port 5000) acting as the stateful backbone
for OpenClaw's paper trading and equity screening workflows.

## Stack
- **FastAPI** — API framework
- **SQLite + SQLModel** — zero-config persistent storage
- **yfinance** — free market data proxy
- **APScheduler** — background screening jobs (no Redis needed)

---

## Setup

```bash
# 1. Clone / copy this folder
cd open-equity

# 2. Create virtualenv
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env to set STARTING_CASH, fee %, etc.

# 5. Start server
python main.py
# → Running on http://localhost:5000
# → Docs at   http://localhost:5000/docs
```

---

## API Quick Reference

### Submit an order
```bash
curl -X POST http://localhost:5000/order \
  -H "Content-Type: application/json" \
  -d '{"ticker":"NVDA","side":"buy","qty":5,"note":"Breakout signal","skill_used":"tradingview-screener"}'
```

### Check portfolio
```bash
curl http://localhost:5000/portfolio
```

### Get price + fundamentals
```bash
curl http://localhost:5000/price/AAPL
```

### Trigger a screen on specific tickers
```bash
curl -X POST http://localhost:5000/screen \
  -H "Content-Type: application/json" \
  -d '{"tickers":["NVDA","AAPL","MSFT","GOOGL","META"]}'
```

### Trigger a watchlist screen explicitly
```bash
curl -X POST http://localhost:5000/screen \
  -H "Content-Type: application/json" \
  -d '{"use_watchlist":true,"screen_scope":"watchlist"}'
```

### Push signals from ClaWHub skills
```bash
curl -X POST http://localhost:5000/screen \
  -H "Content-Type: application/json" \
  -d '{
    "signals": [
      {
        "ticker": "NVDA",
        "signal": "buy",
        "confidence": 0.87,
        "reason": "Breakout + volume 2.3x + RSI 63 + above SMA200",
        "skill_used": "tradingview-screener",
        "price_at_signal": 875.20
      }
    ]
  }'
```

### Push TradingView Screener results from a custom universe outside the watchlist
```bash
curl -X POST http://localhost:5000/screen \
  -H "Content-Type: application/json" \
  -d '{
    "screen_scope": "custom_universe",
    "screen_label": "AI infra mega-cap scan",
    "universe": "US large-cap AI and semiconductor names outside the watchlist",
    "signals": [
      {
        "ticker": "NVDA",
        "signal": "buy",
        "confidence": 0.87,
        "reason": "Breakout + volume 2.3x + RSI 63 + above SMA200",
        "skill_used": "tradingview-screener",
        "price_at_signal": 875.20
      }
    ]
  }'
```

Those signals are stored with screen metadata so OpenClaw can later filter for:
- watchlist runs
- custom universe scans
- named scan themes via `screen_label`

### Add tickers to watchlist
```bash
curl -X PUT http://localhost:5000/watchlist \
  -H "Content-Type: application/json" \
  -d '{"tickers":["TSM","ASML","005930.KS"],"action":"add"}'
```

### View latest signals
```bash
curl "http://localhost:5000/signals?signal_type=buy"
curl "http://localhost:5000/signals?screen_scope=custom_universe"
curl "http://localhost:5000/signals?screen_label=AI%20infra%20mega-cap%20scan"
```

### View trade history (OpenClaw's memory)
```bash
curl "http://localhost:5000/history?limit=20"
curl "http://localhost:5000/history?ticker=NVDA"
```

### Portfolio alpha vs SPY
```bash
curl http://localhost:5000/benchmark
```

### Manual autonomous research and pipeline triggers

```bash
# Research context
curl "http://localhost:5000/research/context?market=US"
curl "http://localhost:5000/research/context?market=HK"

# Latest market-specific research brief
curl "http://localhost:5000/research/latest?market=US"
curl "http://localhost:5000/research/latest?market=HK"

# US market execution
curl -X POST "http://localhost:5000/pipeline/entry"
curl -X POST "http://localhost:5000/pipeline/exit"

# Hong Kong market execution
curl -X POST "http://localhost:5000/pipeline/entry?market=HK"
curl -X POST "http://localhost:5000/pipeline/exit?market=HK"
```

`GET /pipeline/status` returns separate last-run results for US and HK entry and exit passes.

### Autonomous trade updates to Telegram

The execution agent can forward every autonomous buy or sell to an OpenClaw-routed Telegram target. HK tickers are tagged as `Market: HK` and use `HK$` in the execution price line.

Configure in `.env`:

```bash
TRADE_UPDATE_CHANNEL=telegram
TRADE_UPDATE_ACCOUNT_ID=default
TRADE_UPDATE_TOPIC=telegram:-1003765209717:428
RESEARCH_UPDATE_CHANNEL=telegram
RESEARCH_UPDATE_ACCOUNT_ID=default
RESEARCH_UPDATE_TOPIC=telegram:-1003765209717:428
```

Trade updates include ticker, side, size, execution price, reason or note, and resulting cash or position context.
Research brief updates send a short market summary with strategy, themes, watchlist adds/removes, and key risk.

---

## Background Jobs

### US session pipeline

| Job | Schedule (ET) | What it does |
|---|---|---|
| Research context | 7:00am Monday | Prepares weekly US research context |
| Pre-open screen | 9:15am Mon–Fri | Builds fresh US signals before the entry pass |
| Entry pass | 9:35am Mon–Fri | Consumes stored buy signals and executes eligible entries |
| Midday screen | 12:45pm Mon–Fri | Refreshes US signals during the trading session |
| Exit pass | 3:45pm Mon–Fri | Checks stop-loss and take-profit exits |
| Benchmark snapshot | 9:00pm Mon–Fri | Stores daily portfolio value vs SPY |

### HK session pipeline

| Job | Schedule (HKT) | What it does |
|---|---|---|
| Research context | 7:00am Monday | Prepares weekly HK research context |
| Pre-open screen | 9:15am Mon–Fri | Builds fresh HK signals before the AM entry pass |
| Entry pass (AM) | 9:30am Mon–Fri | Consumes stored HK buy signals and executes eligible entries |
| Exit pass (AM) | 12:00pm Mon–Fri | Checks HK lunch-session exits |
| Midday screen | 12:45pm Mon–Fri | Refreshes HK signals before the PM session |
| Entry pass (PM) | 1:00pm Mon–Fri | Consumes refreshed HK buy signals and executes eligible entries |
| Exit pass (PM) | 4:00pm Mon–Fri | Checks HK close-session exits |

Adjust schedules in `.env` using standard 5-field cron syntax.

### Autonomous research generation

The server-side scheduler only prepares context. The actual research brief must be generated by OpenClaw and posted back to the API.
The weekly blog context also includes `market_breakdown.US` and `market_breakdown.HK` sections so the review can cover both markets cleanly.

Recommended flow per market:
1. `GET /research/context?market=US|HK`
2. Generate a JSON brief that matches `output_contract`
3. Submit it with `python3 scripts/submit_research_brief.py --market US|HK`
4. On ingest, a concise research update is sent to the configured pipeline topic

The context payload now includes:
- `output_contract.schema` for the exact JSON shape
- `output_contract.rules` for formatting and market-specific ticker handling
- normalized HK ticker expectations like `0700.HK`

On ingest, the server also normalizes market tickers before applying watchlist changes, so bare HK codes like `700` become `0700.HK`.

---

## Signal Scoring (local screener)

The local screener is the fallback / complement to ClaWHub skills.

| Dimension | Max score | Criteria |
|---|---|---|
| Breakout proximity | 0.25 | Price within 1.5–10% of 52w high |
| Volume surge | 0.20 | Volume 1.4×–2.5× 20-day average |
| RSI zone | 0.20 | RSI 58–70 (momentum), <35 (oversold reversal) |
| Trend alignment | 0.25 | Above SMA20/50/200, MACD bullish |
| Fundamental bonus | 0.10 | P/E 8–22, revenue/EPS growth |

**FLAG gates** (checked before buy scoring):
- RSI > 78 (extreme overbought)
- Dividend yield > 8% (yield trap risk)
- Negative free cash flow
- Debt/equity > 350%
- Price > 30% below 52w high AND below SMA200
- P/E > 80

Confidence ≥ 0.70 → BUY signal surfaced to OpenClaw.

---

## OpenClaw Integration

1. Copy the contents of `OPENCLAW_SYSTEM_PROMPT.md` into your OpenClaw agent's system prompt on ClawHub
2. Install these skills on your OpenClaw agent:
   - `https://clawhub.ai/lukebaze/tradingview-screener`
   - `https://clawhub.ai/ndtchan/equity-valuation-framework`
   - `https://clawhub.ai/nickfiorani/fundamental-stock-analysis`
   - `https://clawhub.ai/paulshe/china-stock-analysis`
3. Ensure OpenClaw's tool execution environment can reach `http://localhost:5000`
4. Test with:
   - "Screen my watchlist and tell me top buy candidates"
   - "Use TradingView Screener to find AI infra breakouts outside my watchlist"

---

## File Structure
```
open-equity/
├── main.py                      # FastAPI app + entry point
├── config.py                    # All settings from .env
├── database.py                  # SQLModel tables + session factory
├── requirements.txt
├── .env.example
├── watchlist.json               # Editable ticker list
├── OPENCLAW_SYSTEM_PROMPT.md    # Paste this into OpenClaw
├── routers/
│   ├── orders.py                # POST /order
│   ├── portfolio.py             # GET /portfolio, GET /benchmark
│   ├── prices.py                # GET /price/:ticker, GET /technicals/:ticker
│   ├── history.py               # GET /history
│   └── screener.py              # POST /screen, GET /signals, watchlist
└── services/
    ├── market_data.py           # yfinance wrapper + TTL cache
    ├── portfolio_engine.py      # Order execution + P&L engine
    ├── screener.py              # Signal scoring + watchlist + ingestion
    └── scheduler.py             # APScheduler background jobs
```
