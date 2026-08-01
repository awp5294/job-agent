"""Test configuration.

Sets the environment before `server` is imported, since the module reads config
at import time. Living at the repo root also puts the project on sys.path.
"""
import os
import tempfile

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("BASE_URL", "http://testserver")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("REQUIRE_INVITE", "1")
# The APScheduler cron job has nothing to do in a test run.
os.environ.setdefault("ENABLE_SCHEDULER", "0")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
