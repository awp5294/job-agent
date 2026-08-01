"""The boot-time check that tells you why nothing is happening."""
import server


def test_a_fully_configured_deployment_reports_nothing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    monkeypatch.setattr(server, "SECRET_KEY", "a-real-random-secret")
    monkeypatch.setattr(server, "BASE_URL", "https://jobs.example.com")
    monkeypatch.setattr(server, "oauth_configured", lambda: True)
    assert server.startup_report() == []


def test_missing_api_key_is_called_out(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(server, "oauth_configured", lambda: True)
    assert any("ANTHROPIC_API_KEY" in p for p in server.startup_report())


def test_default_secret_key_is_called_out(monkeypatch):
    monkeypatch.setattr(server, "SECRET_KEY", "change-me-in-production")
    monkeypatch.setattr(server, "oauth_configured", lambda: True)
    assert any("SECRET_KEY" in p for p in server.startup_report())


def test_no_email_at_all_is_called_out(monkeypatch):
    monkeypatch.setattr(server, "oauth_configured", lambda: False)
    monkeypatch.setattr(server, "smtp_configured", lambda: False)
    report = server.startup_report()
    assert any("No email configured" in p for p in report)


def test_smtp_only_warns_that_replies_wont_work(monkeypatch):
    monkeypatch.setattr(server, "oauth_configured", lambda: False)
    monkeypatch.setattr(server, "smtp_configured", lambda: True)
    assert any("replies" in p for p in server.startup_report())


def test_localhost_base_url_is_called_out(monkeypatch):
    monkeypatch.setattr(server, "BASE_URL", "http://localhost:8000")
    monkeypatch.setattr(server, "oauth_configured", lambda: True)
    assert any("BASE_URL" in p for p in server.startup_report())
