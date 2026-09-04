"""The SQLite/Postgres translation layer.

These run on whichever backend the suite is pointed at. To exercise Postgres for
real, start one and run:

    DATABASE_URL=postgresql://... pytest
"""
import json

import pytest

from db import database
from db.connection import (
    POSTGRES, SQLITE, backend, translate_schema, translate_sql,
)


# ── Placeholders ───────────────────────────────────────────────────────────

def test_placeholders_become_postgres_style():
    assert translate_sql("SELECT * FROM users WHERE id=?", False) == \
        "SELECT * FROM users WHERE id=%s"


def test_several_placeholders_all_convert():
    assert translate_sql("VALUES (?, ?, ?)", False) == "VALUES (%s, %s, %s)"


def test_a_question_mark_inside_a_string_is_left_alone():
    """Otherwise a literal would silently become a bind parameter."""
    sql = "SELECT * FROM t WHERE note='really?' AND id=?"
    assert translate_sql(sql, False) == \
        "SELECT * FROM t WHERE note='really?' AND id=%s"


def test_a_percent_in_a_literal_is_escaped_only_when_params_are_bound():
    """psycopg reads % as a placeholder marker, but only when interpolating."""
    sql = "SELECT * FROM t WHERE name LIKE '%ada%' AND id=?"
    assert translate_sql(sql, True) == \
        "SELECT * FROM t WHERE name LIKE '%%ada%%' AND id=%s"
    assert translate_sql(sql, False) == \
        "SELECT * FROM t WHERE name LIKE '%ada%' AND id=%s"


def test_an_escaped_quote_does_not_end_the_string():
    sql = "SELECT * FROM t WHERE name='it''s ok?' AND id=?"
    assert translate_sql(sql, False) == \
        "SELECT * FROM t WHERE name='it''s ok?' AND id=%s"


# ── Schema ─────────────────────────────────────────────────────────────────

def test_autoincrement_becomes_serial():
    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
    assert translate_schema(ddl) == "CREATE TABLE t (id SERIAL PRIMARY KEY, name TEXT)"


def test_the_real_schema_has_no_other_sqlite_only_syntax():
    """One schema file serves both backends, so nothing else may creep in.

    A second schema file would drift; this test is what lets there be one.
    """
    ddl = translate_schema(database.SCHEMA_PATH.read_text()).upper()
    for sqlite_only in ["AUTOINCREMENT", "INSERT OR IGNORE", "INSERT OR REPLACE",
                        "PRAGMA", "WITHOUT ROWID"]:
        assert sqlite_only not in ddl, f"{sqlite_only} does not exist in Postgres"


def test_backend_follows_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert backend() == SQLITE
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    assert backend() == POSTGRES


def test_a_blank_database_url_still_means_sqlite(monkeypatch):
    """An empty secret is what you get from a hosting panel with the field left
    blank, and it must not be read as 'use Postgres'."""
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert backend() == SQLITE


# ── Same behaviour whichever backend is underneath ─────────────────────────

def test_ids_come_back_from_inserts():
    """Postgres has no lastrowid, so both backends use RETURNING."""
    user_id = database.create_user("Ada", "ada-ids@example.com")
    assert isinstance(user_id, int)
    assert database.get_user(user_id)["email"] == "ada-ids@example.com"


def test_the_first_account_is_the_owner_and_later_ones_are_not():
    first = database.create_user("Ada", "first@example.com")
    second = database.create_user("Bob", "second@example.com")
    assert database.get_user(first)["is_owner"]
    assert not database.get_user(second)["is_owner"]


def test_timestamps_survive_json(monkeypatch):
    """SQLite returns text, Postgres returns datetime. Endpoints serialise these
    straight into responses, so the data layer has to agree on one shape."""
    user_id = database.create_user("Ada", "json@example.com")
    run_id = database.start_digest_run(user_id)
    database.finish_digest_run(run_id, "ok", jobs_found=3, jobs_matched=1)

    run = database.get_latest_digest_run(user_id)
    json.dumps(run)  # raises TypeError on a raw datetime
    assert isinstance(run["started_at"], str)


def test_creating_the_same_session_twice_is_harmless():
    """SQLite's INSERT OR IGNORE has no Postgres equivalent; both use ON CONFLICT."""
    database.create_session("sess-1")
    database.create_session("sess-1")
    database.set_session_state("sess-1", {"step": 2})
    assert database.get_session_state("sess-1") == {"step": 2}


def test_upserting_a_job_twice_returns_the_same_row():
    first = database.upsert_job("greenhouse", "abc", "PM", "Anthropic",
                                "https://example.com/1", description="Original")
    second = database.upsert_job("greenhouse", "abc", "PM (updated)", "Anthropic",
                                 "https://example.com/1")
    assert first == second
    # A repeat fetch without a description must not wipe the one already stored.
    jobs = database.get_user_jobs(database.create_user("Ada", "upsert@example.com"))
    assert jobs == []


def test_criteria_round_trip_as_json_lists():
    user_id = database.create_user("Ada", "criteria@example.com")
    database.update_criteria(user_id, {"job_titles": ["PM", "Senior PM"],
                                       "min_salary": 120000})
    criteria = database.get_criteria(user_id)
    assert criteria["job_titles"] == ["PM", "Senior PM"]
    assert criteria["min_salary"] == 120000


def test_migrations_are_safe_to_run_twice():
    """init_db runs on every boot, including against a database that's current."""
    database.init_db()
    database.init_db()
    user_id = database.create_user("Ada", "twice@example.com")
    assert database.get_user(user_id)


@pytest.mark.parametrize("column", ["login_token", "password_hash", "is_owner"])
def test_migrated_columns_exist(column):
    conn = database.get_conn()
    try:
        assert column in conn.columns("users")
    finally:
        conn.close()
