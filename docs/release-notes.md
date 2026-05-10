# Release Notes

## Productization Acceptance Pass

This release adds:

- runtime safety guardrails for daily loss, dependency failures, and abnormal restart cooldown
- manual and automated trade idempotency
- manual trade audit persistence and APIs
- dependency failure persistence and operator visibility
- replay export APIs and metrics surfaces
- SQLite backup and restore scripts
- CI, lint/format workflow, and packaging validation
- container and environment profile assets
- Prometheus-ready `/metrics` output and JSON metrics at `/api/v1/metrics`
- seeded replay datasets for blocked entry, successful entry, moonbag exit, emergency stop, and zombie exit
