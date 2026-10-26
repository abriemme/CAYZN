# CAYZN
## Getting started

```bash
just install
just run  # http://127.0.0.1:8000/docs
just worker                            # runs the jobs queued by POST /bookings
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

Each booking created, or cancellation applied, queues its service for a forecast and optimization job,
in the same transaction (`pending_forecasts` table, NOTIFY on commit). A service waits at most once and runs
on one worker at a time: bookings arriving meanwhile are read by the waiting job. The job stores a forecast
(`forecasts`), then the recommended prices it leads to (`recommended_prices`); both algorithms are simple
placeholder heuristics (`src/cayzn/revenue_management.py`). Failed jobs are only logged, never retried:
the service's next booking queues it again. Run several `just worker` to process services in parallel.

Local Postgres and Redis: `docker compose up -d`.
