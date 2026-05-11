# SeaBright Insight

FastAPI company directory for SeaBright Insight market research records.

## Local Run

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[test]"
.\.venv\Scripts\uvicorn company_app.main:app --reload
```

Open `http://127.0.0.1:8000/companies`.

## Production Run

Use an environment file based on `.env.example`. In production, enrichment is disabled by default so app startup is fast and predictable. Enable `AUTO_ENRICH_COMPANIES=1` only when the deployment can perform outbound enrichment calls.

```bash
pip install .
APP_ENV=production AUTO_ENRICH_COMPANIES=0 \
gunicorn company_app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --access-logfile - \
  --error-logfile -
```

Health check:

```bash
curl http://127.0.0.1:8000/healthz
```

## Docker

```bash
docker build -t seabright-insight .
docker run --rm -p 8000:8000 -v seabright-data:/app/data --env-file .env.example seabright-insight
```

## Configuration

`DATABASE_URL` can point to any SQLAlchemy-supported database URL. If it is not set, the app uses SQLite at `SQLITE_PATH`.

`AUTO_CREATE_TABLES` and `AUTO_MIGRATE` are enabled by default for the current lightweight SQLite deployment. For managed production databases, run migrations deliberately and set them to `0`.

`/healthz` verifies the app process and database connection.
