# ChainBot Product Backlog

## Purpose

This file tracks the next productization tasks after the current architecture split, decision audit rollout, and replay console work.

The goal is to turn ChainBot from a research-oriented local trading bot into a safer, explainable, and operable product.

## Current Baseline

The project already has these foundations in place:

- runtime-loaded and validated settings
- app lifecycle and startup maintenance
- modularized services for scoring, watchlist, observation, position, narrative, enrichment, and opportunity flows
- versioned read APIs and authenticated trade-control endpoints
- decision audit storage, filtering, detail replay, and token case timeline
- regression tests covering the main service slices

## P0: Required Before Serious Live Use

- [ ] Replace the remaining dict-heavy runtime payloads with typed domain models end-to-end.
- [x] Add order idempotency keys for manual and automated buy/sell actions.
- [x] Add hard risk guardrails:
  - [x] max daily loss shutdown
  - [x] max consecutive venue/API failure shutdown
  - [x] strategy cooldown after repeated abnormal exits
- [x] Persist structured dependency failures for RPC, PumpPortal, DexScreener, and news feeds.
- [x] Add a trade-action audit trail for manual `/api/buy` and `/api/sell` calls.
- [x] Add exportable decision reports for one token case replay.
- [x] Add database backup and restore scripts for the local SQLite state.

## P1: Product Safety and Explainability

- [ ] Split strategy policy objects more explicitly:
  - [ ] entry policy
  - [ ] exit policy
  - [ ] exposure policy
  - [ ] cooldown policy
- [ ] Move strategy weights and thresholds into a structured strategy config layer.
- [ ] Store more normalized replay metadata for every decision:
  - [ ] strategy mode snapshot
  - [ ] risk policy snapshot
  - [ ] venue execution metadata
- [ ] Add token-level replay comparison views:
  - [ ] score progression
  - [ ] liquidity progression
  - [ ] holder concentration progression
- [x] Add an audit endpoint for one full token case export as JSON.
- [ ] Add feature flags to separate research-only logic from production-safe rules.

## P1: API and Console

- [ ] Add pagination and filters for:
  - [ ] scan feed
  - [ ] opportunities
  - [ ] trade history
  - [ ] news events
- [ ] Add `/api/v1` detail endpoints for:
  - [ ] opportunities
  - [ ] trade history rows
  - [ ] open positions
- [ ] Add operator views in the dashboard:
  - [ ] dependency health summary
  - [ ] worker error history
  - [ ] maintenance cleanup summary
  - [ ] manual trade audit history
- [ ] Add a compact mobile-safe layout for the analytics and replay workbench.

## P2: Engineering and Delivery

- [x] Add linting and formatting commands to the project workflow.
- [x] Add CI for:
  - [x] unit tests
  - [x] import/compile smoke check
  - [x] packaging validation
- [x] Add test fixtures for historical token replay scenarios.
- [ ] Add seeded replay datasets for:
  - [x] blocked entry
  - [x] successful entry
  - [x] partial moonbag exit
  - [x] emergency stop
  - [x] zombie exit
- [x] Add release notes and upgrade notes for schema changes.

## P2: Operations

- [x] Add structured metrics and alert hooks.
- [x] Add environment profiles for:
  - [x] local research
  - [x] paper-trading staging
  - [x] guarded live trading
- [x] Add container and deployment assets.
- [ ] Plan a migration path from SQLite to Postgres for multi-instance operation.

## Suggested Next Build Slices

### Slice A: Live-Safety Hardening

- [x] idempotency keys
- [x] daily loss shutdown
- [x] repeated dependency failure shutdown
- [x] manual trade audit trail

### Slice B: Replay and Audit Export

- [x] token case export endpoint
- [ ] replay comparison metrics
- [ ] downloadable operator report

### Slice C: API and Console Maturity

- [ ] scan/history pagination
- [ ] dependency health panel
- [ ] mobile-safe analytics layout

## Release Gate Checklist

Do not treat the service as production-ready until all of the following are true:

- [x] risk guardrails are enforced automatically
- [x] every trade action is authenticated, auditable, and idempotent
- [x] dependency degradation is visible from the console and API
- [ ] replay data is sufficient to explain every buy, skip, and sell
- [ ] test coverage protects the main trading workflows
- [x] backup and restore procedures are documented and tested

## Status Note

Updated on 2026-05-10:

- Live-safety hardening is in place for idempotency, guardrails, dependency failures, manual trade audit, and replay export.
- Delivery and operations assets now include CI, metrics surfaces, replay seed datasets, backup/restore notes, runtime profiles, and local container deployment assets.
- The largest remaining gaps are typed models, strategy policy extraction, replay comparison analytics, and a SQLite-to-Postgres migration path.
