# OpenClaw Autonomous Trading Pipeline, MVP plan

## Goal
Turn `open-equity` from a stateful paper-trading backend into an autonomous trading pipeline that can:
- maintain its own separate paper portfolio
- source candidate tickers
- evaluate signals
- apply explicit risk checks
- produce trade proposals
- optionally auto-execute approved paper trades
- log every decision for later review

## Current baseline
Already present:
- FastAPI server and persistent SQLite state
- watchlist management
- local technical and fundamental screening
- order execution
- portfolio and benchmark tracking
- scheduler jobs for screening and benchmark snapshots

Missing for true autonomy:
- portfolio account separation so the agent trades its own paper book instead of the user's book
- a pipeline state machine for end-to-end decision runs
- explicit risk policy enforcement before order placement
- decision artifacts for explainability and auditability
- proposal queue separate from direct execution
- configurable autonomy modes, for example manual, propose-only, and auto-paper
- candidate sizing and position concentration controls

## Recommended MVP architecture

### 1. Pipeline modes
- `manual`: analysis only, no order proposals
- `propose_only`: generate ranked trade proposals, do not execute
- `auto_paper`: execute eligible paper trades automatically after policy checks

### 2. Pipeline stages
1. Load universe from watchlist
2. Run local screen
3. Filter to actionable buy candidates
4. Build enriched candidate records with current portfolio context
5. Run risk policy checks
6. Generate trade proposals with reasons and size
7. If mode is `auto_paper`, execute approved proposals
8. Persist run summary and proposal outcomes

### 3. Core entities
- `PortfolioAccount`: named paper portfolio, for example `siriv5`
- `PipelineRun`: one autonomous cycle
- `TradeProposal`: one candidate decision from a run

### 4. Risk policy in MVP
- max 15 percent of portfolio in a single position after buy
- no buy if signal is not `buy`
- no buy if confidence below configured threshold
- no buy if latest signal already acted on
- no buy if insufficient cash
- default position sizing as a fraction of total portfolio, capped by remaining concentration room

### 5. New API surface
- `POST /autonomy/run` run one pipeline cycle on demand
- `GET /autonomy/proposals` list recent proposals
- `GET /autonomy/runs` list recent pipeline runs
- `GET /autonomy/config` view autonomy settings

### 6. Scheduler direction
Keep existing screener jobs. Autonomy runner can be added as a separate scheduled job later after MVP validation.

## MVP implementation scope for this pass
- add database models for pipeline runs and trade proposals
- add configurable autonomy settings in `config.py`
- add autonomy service with proposal generation and optional execution
- add API router for autonomy endpoints
- keep decisions local-screen based for now
- split portfolio state by account so the autonomous trader has its own book
- leave external skill orchestration as phase 2

## Phase 2 ideas
- add skill-assisted scoring and valuation overlays
- add sell logic and rebalance logic
- add market session awareness
- add proposal approvals via chat workflow
- add richer performance attribution and strategy analytics
