"""
tests/test_app.py

Two test groups:

  Unit tests (no database required):
    The app is created with skip_db=True so pytest can run
    anywhere — your laptop, GitHub Actions without a service container,
    or inside the Docker builder stage.

  Integration tests (require DATABASE_URL in the environment):
    These are skipped automatically when DATABASE_URL is not set.
    In GitHub Actions, a postgres service container provides the URL.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    """Create a test app with the database layer disabled."""
    from app import create_app
    return create_app(skip_db=True)


@pytest.fixture
def client(app):
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ═════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — no database needed
# ═════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:

    def test_returns_200(self, client):
        assert client.get("/health").status_code == 200

    def test_status_field_is_ok(self, client):
        data = client.get("/health").get_json()
        assert data["status"] == "ok"

    def test_database_field_present(self, client):
        data = client.get("/health").get_json()
        assert "database" in data

    def test_timestamp_field_is_iso_string(self, client):
        data = client.get("/health").get_json()
        ts = data["timestamp"]
        assert isinstance(ts, str) and len(ts) > 10


class TestVersionEndpoint:

    def test_returns_200(self, client):
        assert client.get("/version").status_code == 200

    def test_has_all_required_fields(self, client):
        data = client.get("/version").get_json()
        assert "commit"     in data
        assert "build_time" in data
        assert "runner"     in data

    def test_runner_field_format(self, client):
        data = client.get("/version").get_json()
        assert data["runner"].startswith("GitHub Actions #")

    def test_reads_commit_sha_from_env(self, monkeypatch):
        monkeypatch.setenv("COMMIT_SHA", "deadbeef1234")
        from app import create_app
        import importlib, app as app_module
        importlib.reload(app_module)
        a = app_module.create_app(skip_db=True)
        with a.test_client() as c:
            data = c.get("/version").get_json()
        assert data["commit"] == "deadbeef1234"

    def test_reads_run_number_from_env(self, monkeypatch):
        monkeypatch.setenv("RUN_NUMBER", "77")
        import importlib, app as app_module
        importlib.reload(app_module)
        a = app_module.create_app(skip_db=True)
        with a.test_client() as c:
            data = c.get("/version").get_json()
        assert data["runner"] == "GitHub Actions #77"


class TestBuildsEndpoint:

    def test_returns_200_when_skip_db(self, client):
        assert client.get("/builds").status_code == 200

    def test_returns_empty_list_when_skip_db(self, client):
        data = client.get("/builds").get_json()
        assert data == []

    def test_returns_builds_with_db(self):
        """Simulate a real DB response by mocking all db calls."""
        fake_builds = [
            {
                "id": 2,
                "commit_sha": "abc123",
                "build_time": "2026-04-13T10:00:00Z",
                "run_number": "42",
                "recorded_at": "2026-04-13T10:01:00Z",
            },
            {
                "id": 1,
                "commit_sha": "fff000",
                "build_time": "2026-04-12T08:00:00Z",
                "run_number": "41",
                "recorded_at": "2026-04-12T08:01:00Z",
            },
        ]
        from app import create_app as _create_app
        with patch("db.wait_for_db"), \
             patch("db.init_db"), \
             patch("db.record_build"), \
             patch("db.get_all_builds", return_value=fake_builds), \
             patch("db.check_db_health", return_value=True):
            a = _create_app(skip_db=False)
            a.config["TESTING"] = True
            with a.test_client() as c:
                data = c.get("/builds").get_json()
        assert data["total"] == 2
        assert data["builds"][0]["commit_sha"] == "abc123"

    def test_health_returns_503_when_db_down(self):
        """Health endpoint must return 503 when the database is unreachable."""
        with patch("db.wait_for_db"), \
             patch("db.init_db"), \
             patch("db.record_build"), \
             patch("db.check_db_health", return_value=False):
            from app import create_app
            a = create_app(skip_db=False)
            a.config["TESTING"] = True
            with a.test_client() as c:
                response = c.get("/health")
        assert response.status_code == 503
        data = response.get_json()
        assert data["status"] == "degraded"
        assert data["database"] == "unreachable"


class TestRouting:

    def test_unknown_route_returns_404(self, client):
        assert client.get("/nonexistent").status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — require a real PostgreSQL database
# Skipped automatically when DATABASE_URL is not set.
# ═════════════════════════════════════════════════════════════════════════════

INTEGRATION = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping integration tests",
)


@INTEGRATION
class TestDatabaseIntegration:

    @pytest.fixture
    def db_client(self):
        """App with real DB for integration tests."""
        import db as db_module
        db_module.wait_for_db(retries=5, delay=1.0)
        db_module.init_db()
        from app import create_app
        a = create_app(skip_db=False)
        a.config["TESTING"] = True
        with a.test_client() as c:
            yield c

    def test_health_shows_db_connected(self, db_client):
        data = db_client.get("/health").get_json()
        assert data["database"] == "connected"
        assert data["status"] == "ok"

    def test_builds_returns_at_least_one_record(self, db_client):
        """Startup records this build, so /builds must have >= 1 row."""
        data = db_client.get("/builds").get_json()
        assert data["total"] >= 1

    def test_builds_record_has_correct_shape(self, db_client):
        data = db_client.get("/builds").get_json()
        build = data["builds"][0]
        assert "id"          in build
        assert "commit_sha"  in build
        assert "build_time"  in build
        assert "run_number"  in build
        assert "recorded_at" in build

    def test_version_and_builds_commit_match(self, db_client):
        """The commit in /version must match the most recent /builds record."""
        version_data = db_client.get("/version").get_json()
        builds_data  = db_client.get("/builds").get_json()
        latest_build = builds_data["builds"][0]
        assert version_data["commit"] == latest_build["commit_sha"]
