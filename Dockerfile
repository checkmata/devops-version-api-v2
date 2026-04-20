# ─── STAGE 1: builder ──────────────────────────────────────────────────────
#
# Purpose: install dependencies and run the full unit test suite.
# This stage is NEVER shipped — Docker discards it after the build.
# Any test failure here aborts the build, so broken code is physically
# unable to reach the container registry.
#
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system packages needed to compile psycopg2-binary
# (libpq-dev is the PostgreSQL client library header)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# ── dependency layer (cached aggressively) ──────────────────────────────────
# Copy requirements BEFORE the source code.
# Docker caches this layer separately.
# Changing app.py does NOT re-trigger pip install — only requirements.txt does.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── source layer ────────────────────────────────────────────────────────────
COPY . .

# Run unit tests inside the build (SKIP_DB=true → no real DB needed).
# The builder stage fails — and the image is not produced — if tests fail.
RUN SKIP_DB=true python -m pytest tests/ -v


# ─── STAGE 2: runtime ──────────────────────────────────────────────────────
#
# Purpose: the lean production image.
# Starts from a clean base — no gcc, no test files, no pip cache,
# no source files that aren't the application itself.
#
FROM python:3.11-slim AS runtime

WORKDIR /app

# psycopg2-binary needs libpq at runtime too (just the shared library, not the headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── pipeline metadata ────────────────────────────────────────────────────────
# These three ARGs are injected by the GitHub Actions pipeline:
#   docker build \
#     --build-arg COMMIT_SHA=$GITHUB_SHA \
#     --build-arg BUILD_TIME=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
#     --build-arg RUN_NUMBER=$GITHUB_RUN_NUMBER ...
#
# They are baked into the image as ENV variables so the app can read them
# with os.getenv() at any point — even years after the image was built.
ARG COMMIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG RUN_NUMBER=unknown

ENV COMMIT_SHA=${COMMIT_SHA}
ENV BUILD_TIME=${BUILD_TIME}
ENV RUN_NUMBER=${RUN_NUMBER}

# ── copy only what production needs ─────────────────────────────────────────
# Installed Python packages from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages \
                    /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/flask \
                    /usr/local/bin/flask

# Application source (no tests, no Dockerfile, no compose files)
COPY --from=builder /app/app.py  .
COPY --from=builder /app/db.py   .

# ── security: non-root user ──────────────────────────────────────────────────
# Running as root inside a container is a significant security risk.
# If the process is compromised, the attacker gets root inside the container.
# A non-root user limits the blast radius.
RUN adduser --disabled-password --gecos "" appuser
USER appuser

# Document the port (does not publish it — that happens at docker run / compose)
EXPOSE 5000

# ── health check ─────────────────────────────────────────────────────────────
# Docker (and Kubernetes) uses this to determine whether the container
# is truly ready to serve traffic, not just "started".
# The /health endpoint also checks DB connectivity, so a healthy container
# here means the app AND the database are both working.
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]
