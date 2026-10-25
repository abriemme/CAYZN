# CAYZN
## Getting started

```bash
just install
just run  # http://127.0.0.1:8000/docs
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

`bookings` is partitioned by month of the service's departure date (`bookings_YYYY_MM`).
The import creates the partitions for the months of the plan; a departed month is archived with
`DROP TABLE bookings_YYYY_MM`. Postgres cannot enforce uniqueness across partitions: the
`Idempotency-Key` and the natural key are unique per departure date.

Whole stack in Docker (`db`, `redis`, `worker`, `api`): `docker compose up --build`, then http://127.0.0.1:8000/docs.
`worker` is a one-shot job: it applies the migrations and imports `files/` into the database and the Redis cache; `api` starts once it has succeeded.
Only `db` and `redis` for a local `just run`: `docker compose up -d db redis`.
