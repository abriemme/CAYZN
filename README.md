# CAYZN
## Getting started

```bash
just install
just run  # http://127.0.0.1:8000/docs
just worker                            # runs the forecast and optimization jobs
just request-all                       # queues every service still to depart (daily, after the import)
just check                             # lint, format, types (ty)
just test                              # requires Postgres and Redis (throwaway cayzn_test_* databases)
just migrate                           # alembic upgrade head
just migrate-check                     # alembic check: migrations up to date with the models
just import-plan files                 # import trains/stops/ods/prices.csv, reload the Redis cache
```

New migration after changing `src/cayzn/models.py`:
`uv run alembic revision --autogenerate -m "..."`, then review the generated file.

`DATABASE_URL` (Postgres URL, default: `postgresql+psycopg://cayzn:cayzn@localhost:5432/cayzn`, read from the environment, then `.env`, then `.env.local`) selects the database. Run `cp .env.example .env.local` to get started.

`REDIS_URL` (default: `redis://localhost:6379/0`): cache `(service, date, origin, destination) → (service_id, od_id)`
used by `POST /bookings`. Loaded on API startup if missing, reloaded after each import.
Each API process keeps found ODs in an in-memory LRU in front of Redis.

`POST /bookings` is idempotent: a booking is identified by the `Idempotency-Key` header when sent,
otherwise by (OD, sale time, price, currency). A redelivery returns the existing booking (200);
one carrying `cancel_datetime` cancels it.

Forecast and optimization jobs are requested daily for every service still to depart (`just request-all`,
run by `just import-plan` after the nightly import) and whenever an analyst changes a price
(`compute_requests` table, NOTIFY on commit). A service waits at most once and runs on one worker at a time.
The job stores a run (`forecasts`: when it was computed, the bookings it read), its expected bookings by OD
and day-x (`forecast_points`), then the recommended prices (`recommended_prices`, `forecasts.optimized_at`);
both algorithms are simple placeholder heuristics (`src/cayzn/revenue_management.py`). Failed jobs are only
logged, never retried: the next request runs a new one. Run several `just worker` to process services in parallel.

The analyst's API, each response carrying a `freshness` block (when the last forecast and optimization were
computed, `stale` if bookings arrived since, `pending` if a job is waiting; all null without a run):

- `GET /services/{id}/ods`: the grid, each OD's current price and the price recommended by the last optimization.
- `PUT /ods/{id}/price` with `{"price": 42}`: change the price; add `"forecast_id"` (from the grid) to apply
  that run's recommendation, which the price must equal (409 otherwise). Every change is kept in
  `price_changes` (`GET /ods/{id}/price-history`), including the nightly import's; `ods.price` stays the current one.
- `GET /services/{id}/loading-curve?od_id=`: cumulative bookings by day-x observed so far (cancellations
  counted at the day they were cancelled), then the last forecast for the day-x values not yet observed.

Local Postgres and Redis: `docker compose up -d`.

# Roadmap: ideas not implemented

Decisions made to keep the scope in check; each line says what would trigger it.

## Data
- Partitioning `bookings` by departure month: reads are indexed by service and
  do not depend on volume; introduce it for purging and maintenance beyond a
  few hundred million rows (cost: composite PK, loss of incoming FKs).
- Loading curve aggregates maintained incrementally (aggregate table or
  TimescaleDB continuous aggregates) if the windowed query exceeds ~50 ms on
  large services.
- Purge old runs: keep N runs per service, archive the rest.

## Computation
- Recompute triggered by a price change (today: recommendation marked
  `stale`, no automatic rerun).
- Prioritize the queue by proximity to departure (a train leaving tomorrow
  before a train in 4 months).
- Serialize per service with an advisory lock instead of concurrency 1 once
  there is more than one worker.

## API
- Batch ingest (`POST /bookings/batch`) for catch-ups from the sending system.
- Redis cache of frontend responses (loading curve, grid) invalidated at the
  end of each run.
- Authentication between systems (HMAC signature on the webhook, token on the
  frontend API).

## Operations
- Prometheus metrics (ingest throughput, p99 latency, run duration, queue
  depth) via the Remoulade middleware and a `/metrics` endpoint on the API.
- Partman or a nightly job for partitions if the first item is done.
- Answers to the CI / monitoring / scaling / SLA questions: see `docs/interview.md`.
