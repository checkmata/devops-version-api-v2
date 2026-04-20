"""
app.py — DevOps Version API  (Project #03 edition)

Three endpoints:
  GET /version  →  pipeline metadata baked into this image
  GET /health   →  liveness + database connectivity
  GET /builds   →  full deployment history stored in PostgreSQL

The app itself has no business logic.
It reads environment variables and delegates all DB work to db.py.
"""

import os
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify
import db

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── build metadata (injected at image build time via Docker build-args) ────────
COMMIT_SHA = os.getenv("COMMIT_SHA", "unknown")
BUILD_TIME = os.getenv("BUILD_TIME", "unknown")
RUN_NUMBER = os.getenv("RUN_NUMBER", "unknown")

# ── app factory ───────────────────────────────────────────────────────────────

def create_app(skip_db: bool = False) -> Flask:
    """
    Application factory.

    skip_db=True is used during unit tests so the app starts without
    needing a real PostgreSQL instance.
    """
    app = Flask(__name__)

    if not skip_db:
        logger.info("Waiting for database...")
        db.wait_for_db()
        logger.info("Initialising schema...")
        db.init_db()
        logger.info("Recording this deployment...")
        db.record_build(COMMIT_SHA, BUILD_TIME, RUN_NUMBER)
        logger.info(
            "Ready. commit=%s build_time=%s run=%s",
            COMMIT_SHA, BUILD_TIME, RUN_NUMBER,
        )

    # ── routes ────────────────────────────────────────────────────────────────

    @app.route("/version")
    def version():
        """Return the pipeline metadata baked into this image."""
        return jsonify({
            "commit":     COMMIT_SHA,
            "build_time": BUILD_TIME,
            "runner":     f"GitHub Actions #{RUN_NUMBER}",
        })

    @app.route("/health")
    def health():
        """
        Liveness check.

        Reports database connectivity so Docker / Kubernetes knows
        whether the container is truly ready to serve traffic.
        """
        db_ok = db.check_db_health() if not skip_db else True
        payload = {
            "status":    "ok" if db_ok else "degraded",
            "database":  "connected" if db_ok else "unreachable",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        status_code = 200 if db_ok else 503
        return jsonify(payload), status_code

    @app.route("/builds")
    def builds():
        """
        Return the full deployment history from PostgreSQL.

        Every time a new container starts, it writes one row to the builds
        table.  This endpoint proves:
          1. The container can reach the database.
          2. Data persists across container restarts (Docker volume).
          3. Each new deployment adds a new record.
        """
        if skip_db:
            return jsonify([])

        rows = db.get_all_builds()
        return jsonify({
            "total":  len(rows),
            "builds": rows,
        })

    return app


# ── entry point ───────────────────────────────────────────────────────────────

app = create_app(skip_db=os.getenv("SKIP_DB", "false").lower() == "true")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
