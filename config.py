from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


PORT = int(os.getenv("PORT", "5000"))
STARTING_CASH = float(os.getenv("STARTING_CASH", "100000"))
TRADING_FEE_PCT = float(os.getenv("TRADING_FEE_PCT", "0.001"))
BENCHMARK_TICKER = os.getenv("BENCHMARK_TICKER", "SPY")
PRICE_CACHE_TTL = int(os.getenv("PRICE_CACHE_TTL", "60"))
MIN_SIGNAL_CONFIDENCE = float(os.getenv("MIN_SIGNAL_CONFIDENCE", "0.70"))

SCREEN_SCHEDULE_CRON = os.getenv("SCREEN_SCHEDULE_CRON", "0 20 * * 1-5")
SCREEN_INTRADAY_CRON = os.getenv("SCREEN_INTRADAY_CRON", "*/15 9-16 * * 1-5")
BENCHMARK_SNAPSHOT_CRON = os.getenv("BENCHMARK_SNAPSHOT_CRON", "0 21 * * 1-5")

DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "open_equity.db"))
WATCHLIST_PATH = str(BASE_DIR / os.getenv("WATCHLIST_PATH", "watchlist.json"))

AUTONOMY_MODE = os.getenv("AUTONOMY_MODE", "propose_only")
AUTONOMY_MAX_POSITION_PCT = float(os.getenv("AUTONOMY_MAX_POSITION_PCT", "0.15"))
AUTONOMY_POSITION_SIZE_PCT = float(os.getenv("AUTONOMY_POSITION_SIZE_PCT", "0.05"))
AUTONOMY_MIN_CONFIDENCE = float(os.getenv("AUTONOMY_MIN_CONFIDENCE", str(MIN_SIGNAL_CONFIDENCE)))
AUTONOMY_MAX_NEW_BUYS_PER_RUN = int(os.getenv("AUTONOMY_MAX_NEW_BUYS_PER_RUN", "3"))
AUTONOMY_COOLDOWN_MINUTES = int(os.getenv("AUTONOMY_COOLDOWN_MINUTES", "180"))
AUTONOMY_MAX_DAILY_NEW_BUYS = int(os.getenv("AUTONOMY_MAX_DAILY_NEW_BUYS", "4"))
AUTONOMY_MAX_DAILY_NOTIONAL_PCT = float(os.getenv("AUTONOMY_MAX_DAILY_NOTIONAL_PCT", "0.20"))
AUTONOMY_ACCOUNT_NAME = os.getenv("AUTONOMY_ACCOUNT_NAME", "siriv5")
AUTONOMY_SOURCE_TICKERS = [
    "UBER", "BKNG", "SHOP", "MELI", "ADBE", "PANW", "CRWD", "SNOW", "TTD", "INTU",
    "V", "MA", "JPM", "GS", "LLY", "NVO", "COST", "WMT", "GE", "CAT",
    "ETN", "TT", "DE", "URI", "LIN", "NVDA", "MSFT", "GOOGL", "AMZN", "META",
    "AVGO", "TSM", "ASML", "AMD", "AAPL", "ORCL", "CRM", "NOW", "MU", "ANET",
    "PYPL", "COIN", "HOOD", "DASH", "SPOT", "NFLX", "ADYEN.AS", "NU", "SOFI", "SE",
]
