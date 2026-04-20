# devops-version-api — Project #03

![CI Pipeline](https://github.com/YOUR_USERNAME/devops-version-api/actions/workflows/pipeline.yml/badge.svg)

An extension of Project #02 that adds a PostgreSQL database and a deployment history endpoint.

Every time a new container starts, it writes one row to the `builds` table.  
`GET /builds` returns the full history — proving that containers come and go but data persists through Docker volumes.

---

## Endpoints

| Method | Path       | Description                                          |
|--------|------------|------------------------------------------------------|
| GET    | `/version` | Pipeline metadata baked into this image              |
| GET    | `/health`  | Liveness check — includes database connectivity      |
| GET    | `/builds`  | Full deployment history stored in PostgreSQL         |

### Example responses

```json
GET /version
{
  "commit":     "a1b2c3d4e5f6...",
  "build_time": "2026-04-13T10:30:00Z",
  "runner":     "GitHub Actions #42"
}

GET /health
{
  "status":    "ok",
  "database":  "connected",
  "timestamp": "2026-04-13T11:05:33.221000+00:00"
}

GET /builds
{
  "total": 3,
  "builds": [
    {
      "id": 3,
      "commit_sha":  "a1b2c3d4...",
      "build_time":  "2026-04-13T10:30:00Z",
      "run_number":  "42",
      "recorded_at": "2026-04-13T11:05:00Z"
    },
    ...
  ]
}
```

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  Docker Compose (local) / GitHub Actions (CI) │
│                                              │
│  ┌──────────────────┐   ┌─────────────────┐ │
│  │   Flask API       │──▶│   PostgreSQL    │ │
│  │  (port 5000)     │   │  (port 5432)    │ │
│  │                  │   │                 │ │
│  │  /version        │   │  builds table   │ │
│  │  /health  ───────┼──▶│  (persisted via │ │
│  │  /builds  ───────┼──▶│   Docker volume)│ │
│  └──────────────────┘   └─────────────────┘ │
└──────────────────────────────────────────────┘
```

---

## How the Docker setup works

### Multi-stage Dockerfile

```
Stage 1 — builder:
  ├── Install system build tools (gcc, libpq-dev)
  ├── pip install -r requirements.txt     ← cached layer
  ├── COPY source code
  └── RUN pytest tests/ -v               ← build fails if tests fail

Stage 2 — runtime:
  ├── Fresh python:3.11-slim base (no build tools)
  ├── Install libpq5 (runtime only, ~500KB vs ~30MB for -dev)
  ├── COPY --from=builder site-packages   ← just the installed packages
  ├── COPY --from=builder app.py db.py    ← just the app files
  ├── Create non-root user (appuser)
  ├── ARG/ENV: COMMIT_SHA, BUILD_TIME, RUN_NUMBER
  └── HEALTHCHECK via /health endpoint
```

### Docker Compose dependency chain

```
postgres  →  healthcheck passes  →  api starts  →  api writes to DB
```

The `depends_on: condition: service_healthy` ensures the API never starts
before Postgres is truly ready to accept connections — not just "started".

### Volume persistence

```
docker compose up     →  container A starts, writes row 1 to builds table
docker compose down   →  containers removed, volume KEPT
docker compose up     →  container B starts, writes row 2
GET /builds           →  returns rows 1 and 2 ← proves persistence
docker compose down -v →  volumes deleted, data gone (fresh start)
```

---

## Quick start

```bash
# Clone and enter
git clone https://github.com/YOUR_USERNAME/devops-version-api
cd devops-version-api

# Start everything (Postgres + API)
docker compose up --build

# In another terminal, test the endpoints
curl http://localhost:5000/version
curl http://localhost:5000/health
curl http://localhost:5000/builds

# Restart the API to see a new build record added
docker compose restart api
curl http://localhost:5000/builds   # total should now be 2
```

## Run tests locally (no Docker needed)

```bash
pip install -r requirements.txt
SKIP_DB=true pytest tests/ -v
```

## CI/CD pipeline

Three jobs run in order:

```
unit-test         →  pytest without a real DB (SKIP_DB=true)
    ↓
integration-test  →  pytest with a real Postgres service container
    ↓
build-and-push    →  multi-stage docker build + push to GHCR
                     (only on push to main/develop, not on PRs)
```

---

## Project structure

```
devops-version-api/
├── .github/
│   └── workflows/
│       └── pipeline.yml    # 3-job CI/CD pipeline
├── tests/
│   └── test_app.py         # unit tests + integration tests
├── app.py                  # Flask application + app factory
├── db.py                   # all database logic (psycopg2)
├── Dockerfile              # multi-stage build
├── docker-compose.yml      # local dev: api + postgres + volume
├── requirements.txt
└── .dockerignore
```

---

## Setup for a new GitHub repository

1. Push this code to a new GitHub repo.
2. Go to **Actions** — the pipeline runs automatically on the first push.
3. Go to **Packages** — your image appears at `ghcr.io/YOUR_USERNAME/devops-version-api`.
4. Update the badge URL at the top of this README.
5. Run locally with `docker compose up --build` and hit `/builds` to see the live deployment history.
