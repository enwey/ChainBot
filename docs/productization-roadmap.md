# ChainBot Productization Roadmap

## Current Assessment

ChainBot already has the right raw ingredients for a trading product:

- live market ingestion
- strategy and execution separation
- a persistent store
- an operator dashboard

But it is still closer to a research bot than a mature product. The biggest gaps are:

- one large `TradingEngine` mixes orchestration, scoring, risk, and runtime state
- blocking network calls still sit inside async loops
- configuration was previously import-time and weakly validated
- operational APIs lacked lifecycle, health, and admin safety boundaries
- there were no automated tests protecting product behavior

## What This Iteration Improves

This codebase now has a stronger foundation for product work:

- runtime configuration is loaded at startup and validated explicitly
- public binds require an `ADMIN_API_TOKEN`
- the app has a lifecycle object instead of a loose bootstrap script
- market data, executor, and news clients are closed cleanly on shutdown
- health and status endpoints expose runtime state instead of a fixed `"ok"`
- the hottest blocking snapshot fetches are moved off the asyncio event loop
- basic regression tests cover config validation and admin API protection

These changes do not finish the productization journey, but they make the next phases much safer.

## Target Architecture

The medium-term target should be a modular service with clear bounded contexts:

1. Ingestion
   Responsibilities: market streams, metadata fetch, news/KOL feeds, normalization.

2. Strategy
   Responsibilities: token scoring, watchlist transitions, entry qualification, adaptive mode selection.

3. Risk
   Responsibilities: portfolio exposure, position sizing, drawdown controls, kill switches, policy rules.

4. Execution
   Responsibilities: paper/live routing, venue abstraction, slippage policy, idempotent order submission.

5. Portfolio
   Responsibilities: positions, wallet state, realized/unrealized PnL, trade history.

6. API and Operator Console
   Responsibilities: read APIs, authenticated control APIs, status/health, observability surfaces.

## Recommended Delivery Phases

### Phase 1: Stabilize the Core Service

- split `TradingEngine` into orchestrator plus focused services:
  - `SignalScorer`
  - `WatchlistService`
  - `PositionManager`
  - `AdaptiveStrategyService`
- move all blocking HTTP/DB hot paths behind async-safe adapters
- add a typed domain model instead of passing unstructured dicts everywhere
- standardize structured error handling and audit logging

### Phase 2: Make Trading Logic Safer

- formalize entry rules, exit rules, and risk rules into separate policy objects
- persist signal decisions and rule outcomes for explainability
- add idempotency keys for manual and automated trade actions
- add circuit breakers:
  - max daily loss
  - repeated venue/API failure shutdown
  - strategy cooldown after consecutive abnormal exits

### Phase 3: Productize the API Surface

- version the API as `/api/v1/...`
- move mutating actions to authenticated `POST` endpoints only
- add pagination and filter support for scan/history endpoints
- expose operator-grade readiness, latency, and dependency status
- add role boundaries for read-only vs trade-control access

### Phase 4: Productize Operations

- add metrics, tracing, and alerting
- support multiple runtime profiles:
  - local research
  - paper-trading staging
  - live trading production
- add migration tooling for SQLite to Postgres if multi-user or multi-instance is needed
- package the service with container and deployment manifests

## Logic Refactor Priorities

If we keep improving the trading logic, the highest-value sequence is:

1. replace dict-based signals and positions with typed models
2. isolate scoring weights into a strategy config object
3. make every buy/sell decision reproducible from stored inputs
4. backtest the policy set on historical snapshots before touching live capital
5. separate research features from production rules with feature flags

## Product Principles

To make this a mature product instead of a clever script, we should hold the line on a few principles:

- every production decision should be explainable
- every mutating API should be authenticated and auditable
- every external dependency should have a degraded-mode story
- every strategy change should be testable before release
- every runtime should be observable from health, status, and logs alone

## Suggested Next Build Slice

The next meaningful implementation slice is:

- extract typed domain models
- split `TradingEngine`
- move risk rules into a dedicated module
- add a small but real test matrix for scoring and exit logic

That is the point where ChainBot starts feeling like a product platform instead of a single trading script.
