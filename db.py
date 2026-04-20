"""
db.py — database connection and queries.

All SQL lives here. app.py never imports psycopg2 directly,
which makes unit-testing straightforward: just swap this module out.
"""

import os
import time
import logging
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ── connection ────────────────────────────────────────────────────────────────

def get_connection():
    """
    Open a new connection using DATABASE_URL from the environment.

    Format: postgresql://USER:PASSWORD@HOST:PORT/DBNAME
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(database_url)


def wait_for_db(retries: int = 10, delay: float = 2.0) -> None:
    """
    Block until the database is reachable, or raise after `retries` attempts.

    Called once at app startup so the container fails fast with a clear
    message instead of crashing silently on the first request.
    """
    for attempt in range(1, retries + 1):
        try:
            conn = get_connection()
            conn.close()
            logger.info("Database is ready.")
            return
        except psycopg2.OperationalError as exc:
            logger.warning(
                "Database not ready (attempt %d/%d): %s", attempt, retries, exc
            )
            time.sleep(delay)

    raise RuntimeError(
        f"Could not connect to the database after {retries} attempts. "
        "Check DATABASE_URL and ensure the DB container is healthy."
    )


# ── schema ────────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS builds (
    id          SERIAL PRIMARY KEY,
    commit_sha  TEXT        NOT NULL,
    build_time  TEXT        NOT NULL,
    run_number  TEXT        NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

def init_db() -> None:
    """Create the builds table if it does not already exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()


# ── queries ───────────────────────────────────────────────────────────────────

def record_build(commit_sha: str, build_time: str, run_number: str) -> None:
    """
    Insert one row for the current container into the builds table.

    Called once at startup — this is how each deployment leaves a footprint
    in the database, visible via GET /builds.
    """
    sql = """
        INSERT INTO builds (commit_sha, build_time, run_number)
        VALUES (%s, %s, %s)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (commit_sha, build_time, run_number))
        conn.commit()


def get_all_builds() -> list[dict]:
    """
    Return every row in the builds table, newest first.

    Each row is a plain dict so app.py can pass it directly to jsonify().
    """
    sql = """
        SELECT id, commit_sha, build_time, run_number,
               recorded_at AT TIME ZONE 'UTC' AS recorded_at
        FROM builds
        ORDER BY id DESC
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    # Convert to plain dicts and stringify the timestamp
    return [
        {
            "id":          row["id"],
            "commit_sha":  row["commit_sha"],
            "build_time":  row["build_time"],
            "run_number":  row["run_number"],
            "recorded_at": row["recorded_at"].isoformat() + "Z",
        }
        for row in rows
    ]


def check_db_health() -> bool:
    """Return True if the DB is reachable, False otherwise."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False
