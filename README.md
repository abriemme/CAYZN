# CAYZN
## Démarrer

```bash
uv sync
uv run fastapi dev src/cayzn/main.py   # http://127.0.0.1:8000/docs
just check                             # lint, format, types (ty)
just test
just migrate                           # alembic upgrade head
just import-plan files                 # importe trains/stops/ods/prices.csv
```

Nouvelle migration après modification de `src/cayzn/models.py` :
`uv run alembic revision --autogenerate -m "..."`, puis relire le fichier généré.

`DATABASE_URL` (URL Postgres, défaut : `postgresql+psycopg://cayzn:cayzn@localhost:5432/cayzn`, lu depuis l'env, `.env` puis `.env.local`) choisit la base. `cp .env.example .env.local` pour démarrer.

Postgres local : `docker compose up -d`, puis
`DATABASE_URL=postgresql+psycopg://cayzn:cayzn@localhost:5432/cayzn`.
