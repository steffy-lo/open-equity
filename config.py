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

# ─────────────────────────────────────────────────────────────
# Autonomous Trading Pipeline
# ─────────────────────────────────────────────────────────────

# Execution agent risk parameters
EXEC_MAX_POSITION_PCT:  float = float(os.getenv("EXEC_MAX_POSITION_PCT",  "0.10"))  # 10%
EXEC_MAX_EXPOSURE_PCT:  float = float(os.getenv("EXEC_MAX_EXPOSURE_PCT",  "0.80"))  # 80%
EXEC_STOP_LOSS_PCT:     float = float(os.getenv("EXEC_STOP_LOSS_PCT",     "0.08"))  # 8%
EXEC_TAKE_PROFIT_PCT:   float = float(os.getenv("EXEC_TAKE_PROFIT_PCT",   "0.20"))  # 20%
EXEC_MAX_TRADES_PER_DAY: int  = int(os.getenv("EXEC_MAX_TRADES_PER_DAY",  "3"))
 

# Blog output
BLOG_OUTPUT_DIR: str = os.getenv("BLOG_OUTPUT_DIR", "blogs/trading")
 
# Pipeline scheduler crons (5-field, America/New_York)
RESEARCH_CONTEXT_CRON: str = os.getenv("RESEARCH_CONTEXT_CRON", "0 7 * * 1")      # Mon 7am
ENTRY_PASS_CRON:       str = os.getenv("ENTRY_PASS_CRON",       "35 9 * * 1-5")   # Mon–Fri 9:35am
EXIT_PASS_CRON:        str = os.getenv("EXIT_PASS_CRON",         "45 15 * * 1-5") # Mon–Fri 3:45pm
BLOG_CONTEXT_CRON:     str = os.getenv("BLOG_CONTEXT_CRON",     "0 18 * * 5")     # Sunday 6pm
