#!/bin/bash
# Daily trading results summary script
# Fetches today's trades and sends a summary to the Trades topic

set -euo pipefail

# Configuration
OPEN_EQUITY_DIR="/home/ubuntu/.openclaw/workspace/open-equity"
OPEN_EQUISTY_URL="http://127.0.0.1:5000"
TELEGRAM_CHAT="telegram:-1003765209717"
TELEGRAM_THREAD="428"
OPENCLAW_BIN="/home/ubuntu/.npm-global/bin/openclaw"

# Change to the open-equity directory
cd "$OPEN_EQUITY_DIR"

# Get today's date in YYYY-MM-DD format (UTC)
TODAY=$(date -u +%Y-%m-%d)

# Fetch trades from history (we'll filter for today's trades in the response)
TRADES_RESPONSE=$(curl -s "${OPEN_EQUISTY_URL}/history?limit=100" || echo "{}")

# Extract today's trades
TODAYS_TRADES=$(echo "$TRADES_RESPONSE" | python3 -c "
import sys, json, datetime
try:
    data = json.load(sys.stdin)
    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')
    todays_trades = []
    for trade in data.get('trades', []):
        trade_date = trade.get('timestamp', '')[:10]  # Extract YYYY-MM-DD
        if trade_date == today_str:
            todays_trades.append(trade)
    print(json.dumps(todays_trades))
except Exception as e:
    print(json.dumps([]))
")

# Fetch portfolio status
PORTFOLIO_RESPONSE=$(curl -s "${OPEN_EQUISTY_URL}/portfolio" || echo '{}')

# Format the summary message
if [ "$TODAYS_TRADES" = "[]" ] || [ -z "$TODAYS_TRADES" ]; then
    MESSAGE="📈 Daily Trading Results ($(date)): No trades executed today."
else
    TRADE_COUNT=$(echo "$TODAYS_TRADES" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")
    
    # Build trade details
    TRADE_DETAILS=$(echo "$TODAYS_TRADES" | python3 -c "
import sys, json
trades = json.load(sys.stdin)
if not trades:
    print('No trades')
else:
    lines = []
    for trade in trades:
        ticker = trade.get('ticker', 'UNKNOWN')
        side = trade.get('side', 'UNKNOWN').upper()
        qty = trade.get('qty', 0)
        price = trade.get('fill_price', 0)
        order_id = trade.get('order_id', '????')
        lines.append(f'• {side} {qty} {ticker} @ \${price:.2f} (ID: {order_id})')
    print('\\n'.join(lines))
    ")
    
    # Get portfolio value for context
    PORTFOLIO_VALUE=$(echo "$PORTFOLIO_RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'{d.get(\"total_value\", 0):.2f}')" 2>/dev/null || echo "0.00")
    
    MESSAGE="📈 Daily Trading Results ($(date)): $TRADE_COUNT trade(s) executed today.
$TRADE_DETAILS

💰 Portfolio Value: \$$PORTFOLIO_VALUE"
fi

# Send the message via OpenClaw CLI
"$OPENCLAW_BIN" message send --channel telegram --target "$TELEGRAM_CHAT" --thread-id "$TELEGRAM_THREAD" --message "$MESSAGE"
