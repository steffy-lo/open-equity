"""
OpenClaw Paper Trading Server
==============================
A locally-hosted FastAPI server on port 5000 that acts as the
stateful backbone for OpenClaw's paper trading and equity screening workflows.

Endpoints:
  POST /order          — submit a buy/sell
  GET  /portfolio      — positions + P&L
  GET  /benchmark      — portfolio alpha vs SPY
  GET  /price/:ticker  — live price + fundamentals (Yahoo Finance)
  GET  /technicals/:ticker — RSI, SMA, MACD, volume ratio
  POST /price/batch    — bulk price fetch for up to 50 tickers
  GET  /history        — trade log (OpenClaw's persistent memory)
  POST /screen         — trigger screening or ingest ClaWHub skill signals
  GET  /signals        — latest screener output
  GET  /signals/:ticker — signal history for one ticker
  GET  /watchlist      — view watchlist
  PUT  /watchlist      — manage watchlist
  GET  /scheduler      — background job status

Start: python main.py   or   uvicorn main:app --port 5000 --reload
Docs:  http://localhost:5000/docs
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import PORT
from database import init_db
from services.scheduler import start_scheduler, stop_scheduler
from routers import orders, portfolio, prices, history, screener as screener_router, dashboard, autonomy

# ─────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Lifespan: startup + shutdown
# ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting OpenClaw Paper Trading Server...")
    init_db()
    logger.info("✅ Database initialised")
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("👋 Server shut down cleanly.")


# ─────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "OpenClaw Paper Trading Server",
    description = (
        "Stateful paper trading backbone for OpenClaw agents. "
        "Integrates with ClaWHub skills: TradingView Screener, "
        "Equity Valuation Framework, Fundamental Stock Analysis, "
        "and China Stock Analysis."
    ),
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# Allow OpenClaw / Telegram bridge to call from any origin on localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ─────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────
app.include_router(orders.router)
app.include_router(portfolio.router)
app.include_router(prices.router)
app.include_router(history.router)
app.include_router(screener_router.router)
app.include_router(dashboard.router)
app.include_router(autonomy.router)


# ─────────────────────────────────────────────────────────────
# Health / root
# ─────────────────────────────────────────────────────────────
@app.get("/", tags=["Meta"], summary="Server health check")
def root():
    return {
        "status":  "running",
        "server":  "OpenClaw Paper Trading Server v1.0.0",
        "docs":    "http://localhost:5000/docs",
        "routes": {
            "order":      "POST /order",
            "portfolio":  "GET  /portfolio",
            "benchmark":  "GET  /benchmark",
            "price":      "GET  /price/{ticker}",
            "technicals": "GET  /technicals/{ticker}",
            "history":    "GET  /history",
            "screen":     "POST /screen",
            "signals":    "GET  /signals",
            "watchlist":  "GET|PUT /watchlist",
            "scheduler":  "GET  /scheduler",
            "dashboard":  "GET  /dashboard",
            "autonomy_run": "POST /autonomy/run",
            "autonomy_run_summary": "POST /autonomy/run/summary",
            "autonomy_runs": "GET  /autonomy/runs",
            "autonomy_proposals": "GET  /autonomy/proposals",
            "autonomy_trade_plans": "GET  /autonomy/trade-plans",
            "autonomy_derivative_ideas": "GET  /autonomy/derivative-ideas",
        },
    }


# ─────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
