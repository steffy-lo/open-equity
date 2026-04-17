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
