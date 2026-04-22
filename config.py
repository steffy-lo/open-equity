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
BASIC_MIN_SIGNAL_CONFIDENCE = float(os.getenv("BASIC_MIN_SIGNAL_CONFIDENCE", "0.70"))
MOMENTUM_MIN_SIGNAL_CONFIDENCE = float(os.getenv("MOMENTUM_MIN_SIGNAL_CONFIDENCE", "0.58"))
EXEC_MIN_SIGNAL_CONFIDENCE = float(
    os.getenv(
        "EXEC_MIN_SIGNAL_CONFIDENCE",
        str(min(BASIC_MIN_SIGNAL_CONFIDENCE, MOMENTUM_MIN_SIGNAL_CONFIDENCE)),
    )
)

BENCHMARK_SNAPSHOT_CRON = os.getenv("BENCHMARK_SNAPSHOT_CRON", "0 21 * * 1-5")

DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "open_equity.db"))
WATCHLIST_PATH = str(BASE_DIR / os.getenv("WATCHLIST_PATH", "watchlist.json"))
US_WATCHLIST_PATH = str(BASE_DIR / os.getenv("US_WATCHLIST_PATH", "watchlist.us.json"))
HK_WATCHLIST_PATH = str(BASE_DIR / os.getenv("HK_WATCHLIST_PATH", "watchlist.hk.json"))

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

# Optional momentum discovery scan, used to find off-watchlist US/HK candidates.
MOMENTUM_SCREENER_ENABLED: bool = os.getenv("MOMENTUM_SCREENER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
MOMENTUM_SCREENER_PYTHON: str = os.getenv(
    "MOMENTUM_SCREENER_PYTHON",
    str(BASE_DIR.parent / "skills" / "tradingview-screener" / ".venv" / "bin" / "python3"),
)
MOMENTUM_SCAN_SCRIPT: str = os.getenv("MOMENTUM_SCAN_SCRIPT", str(BASE_DIR / "scripts" / "momentum_scan.py"))
MOMENTUM_SCAN_LIMIT: int = int(os.getenv("MOMENTUM_SCAN_LIMIT", "8"))
MOMENTUM_MIN_PRICE: float = float(os.getenv("MOMENTUM_MIN_PRICE", "3"))
MOMENTUM_MIN_MARKET_CAP: float = float(os.getenv("MOMENTUM_MIN_MARKET_CAP", "1000000000"))
MOMENTUM_MIN_AVG_VOLUME: float = float(os.getenv("MOMENTUM_MIN_AVG_VOLUME", "500000"))
MOMENTUM_FLAG_RSI_MAX: float = float(os.getenv("MOMENTUM_FLAG_RSI_MAX", "84"))

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
