# Architecture overview

## Key components

- React + Vite front-end (`frontend/`)
- FastAPI service for the API layer (`api.py`, mounted in `main.py`)
- Supabase Auth (authentication)
- PostgreSQL (ride log + training data)
- Strava API (optional, training data import)

## Data flow

1. React app calls FastAPI endpoints under `/api`.
2. FastAPI reads/writes ride/plan data via `services.py` and `db_store.py`.
3. Strava sync and strength standards are optional modules that enrich the data layer.
