# CAYZN
## Démarrer

```bash
uv sync
uv run fastapi dev src/cayzn/main.py   # http://127.0.0.1:8000/docs
just check                             # lint, format, types (ty)
just test
```

`DATABASE_URL` (défaut : `sqlite:///./cayzn.db`) choisit la base SQLAlchemy.
