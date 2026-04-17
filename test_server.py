"""
test_server.py
==============
Smoke-tests every endpoint on the running server.
Run AFTER starting the server: python main.py

Usage:
    python test_server.py
    python test_server.py --base http://localhost:5000
"""
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

BASE = "http://localhost:5000"


# ─────────────────────────────────────────────────────────────
# HTTP helpers (stdlib only — no extra deps)
# ─────────────────────────────────────────────────────────────

def _req(method: str, path: str, body=None) -> tuple:
    """Returns (status_code, response_dict)"""
    url  = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as exc:
        return 0, {"error": str(exc)}


def GET(path):   return _req("GET",  path)
def POST(path, body=None): return _req("POST", path, body)
def PUT(path, body=None):  return _req("PUT",  path, body)


# ─────────────────────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────────────────────

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

results = []

def test(label: str, status: int, body: dict, expect_status=200, check=None):
    ok = status == expect_status
    if ok and check:
        try:
            ok = check(body)
        except Exception as exc:
            ok = False
            body["_check_error"] = str(exc)

    icon = PASS if ok else FAIL
    results.append(ok)
    print(f"  {icon}  {label}")
    if not ok:
        print(f"       status={status}  body={json.dumps(body)[:200]}")
    return body


def section(title: str):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ─────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────

def run():
    print("\n🧪  OpenClaw Paper Trading Server — Smoke Test")
    print(f"    Target: {BASE}\n")

    # ── Health ────────────────────────────────────────────────
    section("Health")
    s, b = GET("/")
    test("GET /  →  server running", s, b,
         check=lambda b: b.get("status") == "running")

    # ── Market data ───────────────────────────────────────────
    section("Market Data  (requires internet)")

    s, b = GET("/price/AAPL")
    test("GET /price/AAPL  →  returns price", s, b,
         check=lambda b: isinstance(b.get("price"), float) and b["price"] > 0)

    price_aapl = b.get("price", 150.0)

    s, b = GET("/technicals/AAPL")
    test("GET /technicals/AAPL  →  RSI present", s, b,
         check=lambda b: "rsi_14" in b and b["rsi_14"] is not None)

    s, b = POST("/price/batch", ["MSFT", "NVDA"])
    test("POST /price/batch  →  2 results", s, b,
         check=lambda b: isinstance(b, list) and len(b) == 2)

    s, b = GET("/price/TOTALLY_FAKE_XXXX")
    test("GET /price/INVALID_TICKER  →  404", s, b, expect_status=404)

    # ── Watchlist ─────────────────────────────────────────────
    section("Watchlist")

    s, b = GET("/watchlist")
    test("GET /watchlist  →  returns tickers", s, b,
         check=lambda b: "tickers" in b and isinstance(b["tickers"], list))

    s, b = PUT("/watchlist", {"tickers": ["TEST1", "TEST2"], "action": "add"})
    test("PUT /watchlist (add)  →  tickers added", s, b,
         check=lambda b: "TEST1" in b.get("tickers", []))

    s, b = PUT("/watchlist", {"tickers": ["TEST1", "TEST2"], "action": "remove"})
    test("PUT /watchlist (remove)  →  tickers removed", s, b,
         check=lambda b: "TEST1" not in b.get("tickers", []))

    # ── Screener ──────────────────────────────────────────────
    section("Screener")

    s, b = POST("/screen", {"tickers": ["AAPL", "MSFT"]})
    test("POST /screen (local, 2 tickers)  →  results returned", s, b,
         check=lambda b: b.get("screen", {}).get("scanned") == 2)

    s, b = POST("/screen", {
        "signals": [
            {
                "ticker":          "NVDA",
                "signal":          "buy",
                "confidence":      0.87,
                "reason":          "Breakout + volume surge test",
                "skill_used":      "tradingview-screener",
                "price_at_signal": 875.20,
            },
            {
                "ticker":          "PFE",
                "signal":          "flag",
                "confidence":      0.72,
                "reason":          "Yield trap risk — div yield 9.1%",
                "skill_used":      "fundamental-stock-analysis",
                "price_at_signal": 28.50,
            },
        ]
    })
    test("POST /screen (ingest 2 skill signals)  →  2 ingested", s, b,
         check=lambda b: b.get("ingested", {}).get("ingested") == 2)

    s, b = GET("/signals")
    test("GET /signals  →  signals present", s, b,
         check=lambda b: isinstance(b.get("signals"), list))

    s, b = GET("/signals?signal_type=buy")
    test("GET /signals?signal_type=buy  →  filtered", s, b,
         check=lambda b: all(sig["signal"] == "buy" for sig in b.get("signals", [{"signal":"buy"}])))

    s, b = GET("/signals/NVDA")
    test("GET /signals/NVDA  →  history returned", s, b,
         check=lambda b: b.get("ticker") == "NVDA" and b.get("count", 0) > 0)

    # ── Orders ────────────────────────────────────────────────
    section("Orders & Portfolio")

    s, b = GET("/portfolio")
    test("GET /portfolio (pre-trade)  →  cash = starting balance", s, b,
         check=lambda b: b.get("cash", 0) > 0 and b.get("position_count") == 0)

    starting_cash = b.get("cash", 100_000)

    # BUY
    s, b = POST("/order", {
        "ticker":     "AAPL",
        "side":       "buy",
        "qty":        5,
        "note":       "Test buy — smoke test",
        "skill_used": "test_runner",
    })
    test("POST /order (buy 5 AAPL)  →  filled", s, b,
         check=lambda b: b.get("status") == "filled" and b.get("fill_price", 0) > 0)

    fill_price = b.get("fill_price", price_aapl)
    cash_after = b.get("cash_remaining", 0)

    s, b = GET("/portfolio")
    test("GET /portfolio (post-buy)  →  AAPL position exists", s, b,
         check=lambda b: any(p["ticker"] == "AAPL" for p in b.get("positions", [])))

    # Insufficient cash guard
    s, b = POST("/order", {"ticker": "AAPL", "side": "buy", "qty": 99_999})
    test("POST /order (insufficient cash)  →  400", s, b, expect_status=400)

    # SELL
    s, b = POST("/order", {
        "ticker": "AAPL",
        "side":   "sell",
        "qty":    5,
        "note":   "Test sell — smoke test",
    })
    test("POST /order (sell 5 AAPL)  →  filled", s, b,
         check=lambda b: b.get("status") == "filled")

    # Over-sell guard
    s, b = POST("/order", {"ticker": "AAPL", "side": "sell", "qty": 100})
    test("POST /order (over-sell)  →  400", s, b, expect_status=400)

    # Invalid side
    s, b = POST("/order", {"ticker": "AAPL", "side": "hold", "qty": 1})
    test("POST /order (invalid side)  →  422", s, b, expect_status=422)

    # ── History ───────────────────────────────────────────────
    section("Trade History")

    s, b = GET("/history")
    test("GET /history  →  trades present", s, b,
         check=lambda b: b.get("count", 0) > 0)

    s, b = GET("/history?ticker=AAPL")
    test("GET /history?ticker=AAPL  →  AAPL trades only", s, b,
         check=lambda b: all(t["ticker"] == "AAPL" for t in b.get("trades", [{"ticker":"AAPL"}])))

    s, b = GET("/history?side=buy")
    test("GET /history?side=buy  →  buy trades only", s, b,
         check=lambda b: all(t["side"] == "buy" for t in b.get("trades", [{"side":"buy"}])))

    # ── Benchmark ─────────────────────────────────────────────
    section("Benchmark")

    s, b = GET("/benchmark")
    # Either returns alpha data or a "not enough data" message — both valid
    test("GET /benchmark  →  responds without error", s, b,
         check=lambda b: "message" in b or "alpha_pct" in b)

    # ── Scheduler ─────────────────────────────────────────────
    section("Scheduler")

    s, b = GET("/scheduler")
    test("GET /scheduler  →  running with 3 jobs", s, b,
         check=lambda b: b.get("running") is True and len(b.get("jobs", [])) == 3)

    # ── Summary ───────────────────────────────────────────────
    passed = sum(results)
    total  = len(results)
    rate   = passed / total * 100

    print(f"\n{'═'*55}")
    print(f"  Result: {passed}/{total} passed  ({rate:.0f}%)")
    if passed == total:
        print("  🎉  All tests passed — server is ready for OpenClaw!")
    else:
        failed = total - passed
        print(f"  ⚠️   {failed} test(s) failed — review output above.")
    print(f"{'═'*55}\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:5000",
                        help="Server base URL (default: http://localhost:5000)")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    run()
