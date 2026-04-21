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

BENCHMARK_SNAPSHOT_CRON = os.getenv("BENCHMARK_SNAPSHOT_CRON", "0 21 * * 1-5")

DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "open_equity.db"))
WATCHLIST_PATH = str(BASE_DIR / os.getenv("WATCHLIST_PATH", "watchlist.json"))

# ─────────────────────────────────────────────────────────────
# Autonomous Trading Pipeline
# ─────────────────────────────────────────────────────────────

# Execution agent risk parameters
EXEC_MAX_POSITION_PCT: float = float(os.getenv("EXEC_MAX_POSITION_PCT", "0.10"))
EXEC_MAX_EXPOSURE_PCT: float = float(os.getenv("EXEC_MAX_EXPOSURE_PCT", "0.80"))
EXEC_STOP_LOSS_PCT: float = float(os.getenv("EXEC_STOP_LOSS_PCT", "0.08"))
EXEC_TAKE_PROFIT_PCT: float = float(os.getenv("EXEC_TAKE_PROFIT_PCT", "0.20"))
EXEC_MAX_TRADES_PER_DAY: int = int(os.getenv("EXEC_MAX_TRADES_PER_DAY", "3"))

# Blog output. Delivery targets intentionally default to blank so real routing
# stays in the private `.env`, not in repo defaults or examples.
BLOG_OUTPUT_DIR: str = os.getenv("BLOG_OUTPUT_DIR", "blogs/trading")
BLOG_FORWARD_TOPIC: str = os.getenv("BLOG_FORWARD_TOPIC", "")
BLOG_FORWARD_SUMMARY_MAX_CHARS: int = int(os.getenv("BLOG_FORWARD_SUMMARY_MAX_CHARS", "4000"))

# Pipeline update notifications. If a topic is blank, that update type is skipped.
TRADE_UPDATE_CHANNEL: str = os.getenv("TRADE_UPDATE_CHANNEL", "telegram")
TRADE_UPDATE_ACCOUNT_ID: str = os.getenv("TRADE_UPDATE_ACCOUNT_ID", "default")
TRADE_UPDATE_TOPIC: str = os.getenv("TRADE_UPDATE_TOPIC", "")
RESEARCH_UPDATE_CHANNEL: str = os.getenv("RESEARCH_UPDATE_CHANNEL", TRADE_UPDATE_CHANNEL)
RESEARCH_UPDATE_ACCOUNT_ID: str = os.getenv("RESEARCH_UPDATE_ACCOUNT_ID", TRADE_UPDATE_ACCOUNT_ID)
RESEARCH_UPDATE_TOPIC: str = os.getenv("RESEARCH_UPDATE_TOPIC", TRADE_UPDATE_TOPIC)
OPENCLAW_GATEWAY_URL: str = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")

# US session pipeline crons (5-field, America/New_York)
RESEARCH_CONTEXT_CRON: str = os.getenv("RESEARCH_CONTEXT_CRON", "0 7 * * 1")
US_PREOPEN_SCREEN_CRON: str = os.getenv("US_PREOPEN_SCREEN_CRON", "15 9 * * 1-5")
US_MIDDAY_SCREEN_CRON: str = os.getenv("US_MIDDAY_SCREEN_CRON", "45 12 * * 1-5")
ENTRY_PASS_CRON: str = os.getenv("ENTRY_PASS_CRON", "35 9 * * 1-5")
EXIT_PASS_CRON: str = os.getenv("EXIT_PASS_CRON", "45 15 * * 1-5")
BLOG_CONTEXT_CRON: str = os.getenv("BLOG_CONTEXT_CRON", "0 9 * * 6")

# Hong Kong session pipeline crons (5-field, Asia/Hong_Kong)
HK_RESEARCH_CONTEXT_CRON: str = os.getenv("HK_RESEARCH_CONTEXT_CRON", "0 7 * * 1")
HK_PREOPEN_SCREEN_CRON: str = os.getenv("HK_PREOPEN_SCREEN_CRON", "15 9 * * 1-5")
HK_MIDDAY_SCREEN_CRON: str = os.getenv("HK_MIDDAY_SCREEN_CRON", "45 12 * * 1-5")
HK_ENTRY_PASS_CRON_AM: str = os.getenv("HK_ENTRY_PASS_CRON_AM", "30 9 * * 1-5")
HK_EXIT_PASS_CRON_AM: str = os.getenv("HK_EXIT_PASS_CRON_AM", "0 12 * * 1-5")
HK_ENTRY_PASS_CRON_PM: str = os.getenv("HK_ENTRY_PASS_CRON_PM", "0 13 * * 1-5")
HK_EXIT_PASS_CRON_PM: str = os.getenv("HK_EXIT_PASS_CRON_PM", "0 16 * * 1-5")
