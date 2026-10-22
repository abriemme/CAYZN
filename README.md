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

Local Postgres and Redis: `docker compose up -d`.
