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

### Add tickers to watchlist
```bash
curl -X PUT http://localhost:5000/watchlist \
  -H "Content-Type: application/json" \
  -d '{"tickers":["TSM","ASML","005930.KS"],"action":"add"}'
```

### View latest signals
```bash
curl "http://localhost:5000/signals?signal_type=buy"
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

---

## Background Jobs

| Job | Schedule (ET) | What it does |
|---|---|---|
| Nightly screen | 8pm Mon–Fri | Full watchlist scan, stores signals |
| Intraday screen | Every 15min 9am–4pm Mon–Fri | Re-scans recent buy candidates + open positions |
| Benchmark snapshot | 9pm Mon–Fri | Stores daily portfolio value vs SPY |

Adjust schedules in `.env` using standard 5-field cron syntax.

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
4. Test with: "Screen my watchlist and tell me top buy candidates"

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
