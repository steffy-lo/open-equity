import json
import logging
import os
import subprocess
from typing import Any

from config import (
    MOMENTUM_MIN_AVG_VOLUME,
    MOMENTUM_MIN_MARKET_CAP,
    MOMENTUM_MIN_PRICE,
    MOMENTUM_SCAN_LIMIT,
    MOMENTUM_SCAN_SCRIPT,
    MOMENTUM_SCREENER_ENABLED,
    MOMENTUM_SCREENER_PYTHON,
)
from services.markets import Market, normalize_market_tickers

logger = logging.getLogger(__name__)


def discover_momentum_candidates(
    market: Market,
    *,
    exclude_tickers: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if not MOMENTUM_SCREENER_ENABLED:
        return []

    if not os.path.exists(MOMENTUM_SCREENER_PYTHON):
        logger.info(f"[momentum] Screener python not found at {MOMENTUM_SCREENER_PYTHON}; skipping.")
        return []

    if not os.path.exists(MOMENTUM_SCAN_SCRIPT):
        logger.info(f"[momentum] Scan script not found at {MOMENTUM_SCAN_SCRIPT}; skipping.")
        return []

    normalized_excludes = normalize_market_tickers(exclude_tickers or [], market)
    cmd = [
        MOMENTUM_SCREENER_PYTHON,
        MOMENTUM_SCAN_SCRIPT,
        "--market",
        market,
        "--limit",
        str(limit or MOMENTUM_SCAN_LIMIT),
        "--exclude",
        json.dumps(normalized_excludes),
        "--min-price",
        str(MOMENTUM_MIN_PRICE),
        "--min-market-cap",
        str(MOMENTUM_MIN_MARKET_CAP),
        "--min-avg-volume",
        str(MOMENTUM_MIN_AVG_VOLUME),
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=90)
        payload = json.loads(result.stdout.strip() or "[]")
        candidates = []
        for item in payload:
            ticker = item.get("ticker")
            if not ticker:
                continue
            normalized = normalize_market_tickers([ticker], market)
            if not normalized:
                continue
            enriched = dict(item)
            enriched["ticker"] = normalized[0]
            candidates.append(enriched)
        return candidates
    except Exception as exc:
        logger.warning(f"[momentum] Discovery scan failed for {market}: {exc}")
        return []
