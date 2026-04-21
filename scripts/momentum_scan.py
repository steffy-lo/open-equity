#!/usr/bin/env python3
"""Run a broad momentum scan via tvscreener and emit JSON candidates."""

import argparse
import json

import tvscreener as tvs
from tvscreener.field import Market


MARKET_MAP = {
    "US": Market.AMERICA,
    "HK": Market.HONGKONG,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Momentum discovery scan")
    parser.add_argument("--market", choices=sorted(MARKET_MAP.keys()), required=True)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--exclude", default="[]", help="JSON array of tickers to exclude")
    parser.add_argument("--min-price", type=float, default=3.0)
    parser.add_argument("--min-market-cap", type=float, default=1_000_000_000)
    parser.add_argument("--min-avg-volume", type=float, default=500_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    exclude = {str(t).upper().strip() for t in json.loads(args.exclude or "[]") if str(t).strip()}

    screener = tvs.StockScreener()
    screener.set_markets(MARKET_MAP[args.market])
    screener.where(tvs.StockField.PRICE > args.min_price)
    screener.where(tvs.StockField.MARKET_CAPITALIZATION > args.min_market_cap)
    screener.where(tvs.StockField.AVERAGE_VOLUME_30_DAY > args.min_avg_volume)
    screener.select(
        tvs.StockField.NAME,
        tvs.StockField.PRICE,
        tvs.StockField.CHANGE_PERCENT,
        tvs.StockField.VOLUME,
        tvs.StockField.MARKET_CAPITALIZATION,
        tvs.StockField.RELATIVE_STRENGTH_INDEX_14,
        tvs.StockField.SIMPLE_MOVING_AVERAGE_50,
        tvs.StockField.SIMPLE_MOVING_AVERAGE_200,
    )
    df = screener.get()
    if df.empty:
        print("[]")
        return

    columns = {col.lower(): col for col in df.columns}
    name_col = columns["name"]
    change_col = columns["change %"]
    price_col = columns["price"]
    volume_col = columns["volume"]
    market_cap_col = columns["market capitalization"]
    rsi_col = columns["relative strength index (14)"]
    sma50_col = columns["simple moving average (50)"]
    sma200_col = columns["simple moving average (200)"]

    df = df.sort_values(by=change_col, ascending=False)

    results = []
    for _, row in df.iterrows():
        ticker = str(row[name_col]).upper().strip()
        if not ticker or ticker in exclude:
            continue
        sma50 = row[sma50_col]
        sma200 = row[sma200_col]
        results.append(
            {
                "ticker": ticker,
                "price": float(row[price_col]),
                "change_percent": float(row[change_col]),
                "volume": int(row[volume_col]),
                "market_cap": float(row[market_cap_col]),
                "rsi": float(row[rsi_col]),
                "sma50": None if sma50 != sma50 else float(sma50),
                "sma200": None if sma200 != sma200 else float(sma200),
            }
        )
        if len(results) >= args.limit:
            break

    print(json.dumps(results))


if __name__ == "__main__":
    main()
