"""The boot-time check that tells you why nothing is happening."""
import server


def test_a_fully_configured_deployment_reports_nothing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    monkeypatch.setattr(server, "SECRET_KEY", "a-real-random-secret")
    monkeypatch.setattr(server, "BASE_URL", "https://jobs.example.com")
    monkeypatch.setattr(server, "mailbox_configured", lambda: True)
    # A hosted deploy isn't fully configured without a database that outlives it.
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@host/db")
    assert server.startup_report() == []


def test_missing_api_key_is_called_out(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(server, "mailbox_configured", lambda: True)
    assert any("ANTHROPIC_API_KEY" in p for p in server.startup_report())


def test_default_secret_key_is_called_out(monkeypatch):
    monkeypatch.setattr(server, "SECRET_KEY", "change-me-in-production")
    monkeypatch.setattr(server, "mailbox_configured", lambda: True)
    assert any("SECRET_KEY" in p for p in server.startup_report())


def test_no_mailbox_is_called_out(monkeypatch):
    monkeypatch.setattr(server, "mailbox_configured", lambda: False)
    report = server.startup_report()
    assert any("No mailbox configured" in p for p in report)
    # Name the settings that fix it, and what breaks until they're set.
    assert any("SMTP_USER" in p for p in report)
    assert any("replies" in p for p in report)


def test_localhost_base_url_is_called_out(monkeypatch):
    monkeypatch.setattr(server, "BASE_URL", "http://localhost:8000")
    monkeypatch.setattr(server, "mailbox_configured", lambda: True)
    assert any("BASE_URL" in p for p in server.startup_report())


# ── The hosted-without-a-database trap ─────────────────────────────────────

def test_a_hosted_deploy_without_postgres_is_called_out(monkeypatch):
    """Works on day one, loses every account on the next redeploy. Nothing else
    in the app would ever mention it, so boot has to."""
    monkeypatch.setattr(server, "BASE_URL", "https://job-agent.replit.app")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    report = " ".join(server.startup_report())
    assert "DATABASE_URL" in report
    assert "redeploy" in report


def test_no_such_warning_once_postgres_is_configured(monkeypatch):
    monkeypatch.setattr(server, "BASE_URL", "https://job-agent.replit.app")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@host/db")
    assert not any("DATABASE_URL" in p for p in server.startup_report())


def test_running_locally_is_not_nagged_about_postgres(monkeypatch):
    """A SQLite file is the right answer on a laptop."""
    monkeypatch.setattr(server, "BASE_URL", "http://localhost:8000")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert not any("DATABASE_URL" in p for p in server.startup_report())
