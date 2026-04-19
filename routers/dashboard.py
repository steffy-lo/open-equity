from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlmodel import Session
from database import get_session
from services.portfolio_engine import get_portfolio_state, get_benchmark_alpha
from services.screener import get_latest_signals, load_watchlist
from services.scheduler import get_scheduler_status
from sqlmodel import select, col
from database import Trade, Signal
import json

router = APIRouter(tags=["Dashboard"])


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(session: Session = Depends(get_session)):
    """Live monitoring dashboard — auto-refreshes every 30 seconds."""

    # ── Data ─────────────────────────────────────────────────
    portfolio  = get_portfolio_state(session)
    benchmark  = get_benchmark_alpha(session)
    signals    = get_latest_signals(session, limit=100)
    watchlist  = load_watchlist()
    scheduler  = get_scheduler_status()

    trades = session.exec(
        select(Trade).order_by(col(Trade.timestamp).desc()).limit(20)
    ).all()

    trade_rows = ""
    for t in trades:
        pnl_class = ""
        side_badge = (
            '<span class="badge badge-buy">BUY</span>'
            if t.side == "buy"
            else '<span class="badge badge-sell">SELL</span>'
        )
        trade_rows += f"""
        <tr>
          <td>{t.timestamp.strftime('%m/%d %H:%M')}</td>
          <td><strong>{t.ticker}</strong></td>
          <td>{side_badge}</td>
          <td>{t.qty:g}</td>
          <td>${t.fill_price:,.2f}</td>
          <td>${t.qty * t.fill_price:,.0f}</td>
          <td class="note-cell">{t.note or '—'}</td>
          <td><span class="skill-tag">{t.skill_used or '—'}</span></td>
        </tr>"""

    signal_rows = ""
    for s in signals[:30]:
        sig   = s["signal"]
        conf  = s["confidence"]
        color = {"buy": "signal-buy", "flag": "signal-flag", "neutral": "signal-neutral"}.get(sig, "")
        bar_w = int(conf * 100)
        signal_rows += f"""
        <tr>
          <td><strong>{s['ticker']}</strong></td>
          <td><span class="signal-badge {color}">{sig.upper()}</span></td>
          <td>
            <div class="conf-bar"><div class="conf-fill" style="width:{bar_w}%"></div></div>
            <span class="conf-val">{conf:.2f}</span>
          </td>
          <td class="note-cell">{s['reason'][:80]}{'…' if len(s['reason'])>80 else ''}</td>
          <td><span class="skill-tag">{s['skill_used']}</span></td>
          <td>{'✅' if s['acted_on'] else '—'}</td>
          <td>{s['timestamp'][:16].replace('T',' ')}</td>
        </tr>"""

    pos_rows = ""
    for p in portfolio["positions"]:
        pnl    = p["unrealized_pnl"]
        pct    = p["unrealized_pct"]
        color  = "pos-green" if pnl >= 0 else "pos-red"
        arrow  = "▲" if pnl >= 0 else "▼"
        pos_rows += f"""
        <tr>
          <td><strong>{p['ticker']}</strong></td>
          <td>{p['qty']:g}</td>
          <td>${p['avg_cost']:,.2f}</td>
          <td>${p['current_price']:,.2f}</td>
          <td>${p['market_value']:,.0f}</td>
          <td class="{color}">{arrow} ${abs(pnl):,.2f} ({pct:+.2f}%)</td>
          <td class="{color}">${p['realized_pnl']:,.2f}</td>
        </tr>"""

    # Benchmark section
    if "alpha_pct" in benchmark:
        alpha_val   = benchmark["alpha_pct"]
        alpha_color = "#22c55e" if alpha_val >= 0 else "#ef4444"
        alpha_block = f"""
        <div class="stat-card">
          <div class="stat-label">Alpha vs {benchmark.get('alpha_label','SPY').split()[-1]}</div>
          <div class="stat-value" style="color:{alpha_color}">{benchmark['alpha_label']}</div>
          <div class="stat-sub">since {benchmark['period'].split('→')[0].strip()}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Portfolio Return</div>
          <div class="stat-value">{benchmark['portfolio_return_pct']:+.2f}%</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">SPY Return</div>
          <div class="stat-value">{benchmark['spy_return_pct']:+.2f}%</div>
        </div>"""
    else:
        alpha_block = """
        <div class="stat-card" style="grid-column:span 3">
          <div class="stat-label">Benchmark Alpha</div>
          <div class="stat-value" style="font-size:1rem;color:#94a3b8">
            Tracking starts after first nightly snapshot
          </div>
        </div>"""

    # Scheduler jobs
    job_pills = ""
    for j in scheduler.get("jobs", []):
        job_pills += f'<span class="job-pill">⏱ {j["name"]} → {j["next_run"][:16]}</span>'

    total_ret    = portfolio["total_return_pct"]
    ret_color    = "#22c55e" if total_ret >= 0 else "#ef4444"
    buy_count    = sum(1 for s in signals if s["signal"] == "buy")
    flag_count   = sum(1 for s in signals if s["signal"] == "flag")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>OpenClaw Dashboard</title>
<style>
  :root {{
    --bg:       #0f172a;
    --surface:  #1e293b;
    --border:   #334155;
    --text:     #e2e8f0;
    --muted:    #94a3b8;
    --accent:   #6366f1;
    --green:    #22c55e;
    --red:      #ef4444;
    --yellow:   #f59e0b;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px; padding: 24px;
  }}
  h1 {{ font-size: 1.4rem; font-weight: 700; color: #fff; }}
  h2 {{ font-size: 1rem; font-weight: 600; color: var(--muted);
        text-transform: uppercase; letter-spacing: .06em; margin-bottom: 12px; }}
  .header {{ display:flex; justify-content:space-between; align-items:center;
             margin-bottom:24px; }}
  .refresh-note {{ font-size:.75rem; color:var(--muted); }}
  .logo {{ display:flex; align-items:center; gap:10px; }}
  .logo-dot {{ width:10px; height:10px; border-radius:50%;
               background:var(--green); animation: pulse 2s infinite; }}
  @keyframes pulse {{
    0%,100% {{ opacity:1; }} 50% {{ opacity:.4; }}
  }}

  /* Stat bar */
  .stat-bar {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
               gap:12px; margin-bottom:24px; }}
  .stat-card {{ background:var(--surface); border:1px solid var(--border);
                border-radius:10px; padding:16px; }}
  .stat-label {{ font-size:.72rem; color:var(--muted); text-transform:uppercase;
                 letter-spacing:.05em; margin-bottom:6px; }}
  .stat-value {{ font-size:1.5rem; font-weight:700; }}
  .stat-sub {{ font-size:.72rem; color:var(--muted); margin-top:4px; }}

  /* Signal summary pills */
  .pill-bar {{ display:flex; gap:8px; margin-bottom:24px; flex-wrap:wrap; }}
  .pill {{ border-radius:999px; padding:4px 14px; font-size:.78rem; font-weight:600; }}
  .pill-buy  {{ background:#14532d; color:var(--green); }}
  .pill-flag {{ background:#451a03; color:var(--yellow); }}

  /* Scheduler */
  .scheduler-bar {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:24px; }}
  .job-pill {{ background:var(--surface); border:1px solid var(--border);
               border-radius:6px; padding:5px 12px; font-size:.72rem; color:var(--muted); }}

  /* Tables */
  .card {{ background:var(--surface); border:1px solid var(--border);
           border-radius:12px; overflow:hidden; margin-bottom:24px; }}
  .card-header {{ padding:16px 20px; border-bottom:1px solid var(--border); }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ padding:10px 14px; text-align:left; font-size:.72rem; font-weight:600;
        color:var(--muted); text-transform:uppercase; letter-spacing:.04em;
        border-bottom:1px solid var(--border); background:var(--bg); }}
  td {{ padding:10px 14px; border-bottom:1px solid #1e293b; vertical-align:top; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:rgba(99,102,241,.06); }}
  .note-cell {{ font-size:.78rem; color:var(--muted); max-width:280px; }}

  /* Badges */
  .badge {{ border-radius:4px; padding:2px 7px; font-size:.72rem; font-weight:700; }}
  .badge-buy  {{ background:#14532d; color:var(--green); }}
  .badge-sell {{ background:#450a0a; color:var(--red); }}

  .signal-badge {{ border-radius:4px; padding:2px 8px; font-size:.72rem; font-weight:700; }}
  .signal-buy     {{ background:#14532d; color:var(--green); }}
  .signal-flag    {{ background:#451a03; color:var(--yellow); }}
  .signal-neutral {{ background:#1e293b; color:var(--muted); }}

  .skill-tag {{ background:#1e293b; border:1px solid var(--border);
                border-radius:4px; padding:1px 6px; font-size:.68rem; color:var(--muted); }}

  /* Confidence bar */
  .conf-bar {{ display:inline-block; width:60px; height:6px; background:#1e293b;
               border-radius:3px; vertical-align:middle; margin-right:6px; }}
  .conf-fill {{ height:100%; border-radius:3px; background:var(--accent); }}
  .conf-val {{ font-size:.78rem; color:var(--muted); vertical-align:middle; }}

  /* P&L colours */
  .pos-green {{ color:var(--green); }}
  .pos-red   {{ color:var(--red);   }}

  /* Watchlist */
  .ticker-grid {{ display:flex; flex-wrap:wrap; gap:6px; padding:16px; }}
  .ticker-chip {{ background:var(--bg); border:1px solid var(--border);
                  border-radius:6px; padding:4px 10px; font-size:.78rem;
                  font-weight:600; font-family:monospace; }}

  .empty {{ color:var(--muted); font-style:italic; padding:20px; text-align:center; }}
  .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  @media(max-width:900px) {{ .two-col {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>

<div class="header">
  <div class="logo">
    <div class="logo-dot"></div>
    <h1>OpenClaw Dashboard</h1>
  </div>
  <span class="refresh-note">Auto-refresh every 30s</span>
</div>

<!-- ── Stat bar ─────────────────────────────────────────── -->
<div class="stat-bar">
  <div class="stat-card">
    <div class="stat-label">Total Value</div>
    <div class="stat-value">${portfolio['total_value']:,.0f}</div>
    <div class="stat-sub">Started at ${portfolio['starting_cash']:,.0f}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Total Return</div>
    <div class="stat-value" style="color:{ret_color}">{total_ret:+.2f}%</div>
    <div class="stat-sub">${portfolio['total_pnl']:+,.2f} P&L</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Cash</div>
    <div class="stat-value">${portfolio['cash']:,.0f}</div>
    <div class="stat-sub">{portfolio['position_count']} open positions</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Equity</div>
    <div class="stat-value">${portfolio['equity']:,.0f}</div>
    <div class="stat-sub">Unrealized {portfolio['unrealized_pnl']:+,.0f}</div>
  </div>
  {alpha_block}
</div>

<!-- ── Signal summary ─────────────────────────────────── -->
<div class="pill-bar">
  <span class="pill pill-buy">🟢 {buy_count} BUY signals</span>
  <span class="pill pill-flag">🟡 {flag_count} FLAGS</span>
  <span class="pill" style="background:#1e293b;color:var(--muted)">
    📋 {len(watchlist)} on watchlist
  </span>
</div>

<!-- ── Scheduler ──────────────────────────────────────── -->
<div class="scheduler-bar">
  {"".join(f'<span class="job-pill">⏱ {j["name"]} → {j["next_run"][:16]}</span>' for j in scheduler.get("jobs", []))}
</div>

<!-- ── Two-column layout ──────────────────────────────── -->
<div class="two-col">

  <!-- Positions -->
  <div class="card">
    <div class="card-header"><h2>Open Positions</h2></div>
    {"<table><thead><tr><th>Ticker</th><th>Qty</th><th>Avg Cost</th><th>Price</th><th>Value</th><th>Unrealized P&L</th><th>Realized</th></tr></thead><tbody>" + pos_rows + "</tbody></table>" if pos_rows else '<div class="empty">No open positions</div>'}
  </div>

  <!-- Latest Signals -->
  <div class="card">
    <div class="card-header"><h2>Latest Signals</h2></div>
    {"<table><thead><tr><th>Ticker</th><th>Signal</th><th>Confidence</th><th>Reason</th><th>Skill</th><th>Acted</th><th>Time</th></tr></thead><tbody>" + signal_rows + "</tbody></table>" if signal_rows else '<div class="empty">No signals yet — run POST /screen or wait for nightly job</div>'}
  </div>

</div>

<!-- ── Trade History ──────────────────────────────────── -->
<div class="card">
  <div class="card-header"><h2>Recent Trades (last 20)</h2></div>
  {"<table><thead><tr><th>Time</th><th>Ticker</th><th>Side</th><th>Qty</th><th>Fill</th><th>Notional</th><th>Note</th><th>Skill</th></tr></thead><tbody>" + trade_rows + "</tbody></table>" if trade_rows else '<div class="empty">No trades yet</div>'}
</div>

<!-- ── Watchlist ──────────────────────────────────────── -->
<div class="card">
  <div class="card-header"><h2>Watchlist ({len(watchlist)} tickers)</h2></div>
  <div class="ticker-grid">
    {"".join(f'<span class="ticker-chip">{t}</span>' for t in watchlist) or '<span class="empty">Empty — add via PUT /watchlist</span>'}
  </div>
</div>

</body>
</html>"""

    return HTMLResponse(content=html)
