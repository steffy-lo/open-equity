SCAN_PRESETS: dict[str, dict] = {
    "quality_broad": {
        "label": "quality broad market scan",
        "description": "Broader off-watchlist quality leaders across software, payments, industrials, healthcare, consumer, and infrastructure.",
        "universe": "US and global quality large-cap leaders outside the watchlist",
        "tickers": [
            "UBER", "BKNG", "SHOP", "MELI", "ADBE", "PANW", "CRWD", "SNOW", "TTD", "INTU",
            "V", "MA", "JPM", "GS", "LLY", "NVO", "COST", "WMT", "GE", "CAT",
            "ETN", "TT", "DE", "URI", "LIN",
        ],
    },
    "ai_infra": {
        "label": "AI infra momentum scan",
        "description": "AI, cloud, semiconductor, and infrastructure leaders outside the watchlist.",
        "universe": "US and global AI, cloud, semiconductor, and infrastructure leaders outside the watchlist",
        "tickers": [
            "NVDA", "MSFT", "GOOGL", "AMZN", "META", "AVGO", "TSM", "ASML", "AMD", "AAPL",
            "ORCL", "CRM", "NOW", "MU", "ANET",
        ],
    },
    "fintech_growth": {
        "label": "fintech and platform growth scan",
        "description": "Fintech, internet platform, and transaction-layer names outside the watchlist.",
        "universe": "Off-watchlist fintech and platform growth leaders",
        "tickers": [
            "SQ", "PYPL", "COIN", "HOOD", "MELI", "SHOP", "UBER", "DASH", "SPOT", "NFLX",
            "INTU", "ADYEN.AS", "NU", "SOFI", "SE",
        ],
    },
}


SCAN_MODES: dict[str, dict] = {
    "breakout": {
        "label": "breakout",
        "description": "Prefers names close to highs with rising volume and momentum.",
    },
    "pullback": {
        "label": "pullback",
        "description": "Prefers quality names pulling back from highs but rebuilding momentum.",
    },
    "quality_value_momentum": {
        "label": "quality value momentum",
        "description": "Prefers reasonable valuation with improving trend and growth support.",
    },
}


def list_scan_presets() -> list[dict]:
    return [
        {
            "name": name,
            "label": preset["label"],
            "description": preset["description"],
            "ticker_count": len(preset["tickers"]),
            "universe": preset["universe"],
        }
        for name, preset in SCAN_PRESETS.items()
    ]


def list_scan_modes() -> list[dict]:
    return [
        {
            "name": name,
            "label": mode["label"],
            "description": mode["description"],
        }
        for name, mode in SCAN_MODES.items()
    ]


def get_scan_preset(name: str) -> dict:
    if name not in SCAN_PRESETS:
        raise KeyError(name)
    return SCAN_PRESETS[name]


def get_scan_mode(name: str) -> dict:
    if name not in SCAN_MODES:
        raise KeyError(name)
    return SCAN_MODES[name]
