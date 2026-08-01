"""SQLite data layer.

Every function opens a short-lived connection. That's fine at this scale and keeps
things safe across the threadpool that FastAPI uses for sync work.
"""
import json
import os
import sqlite3
import uuid
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Resolved lazily so tests (and DB_PATH changes) take effect without reimporting.
_db_path: str | None = None

# Columns callers are allowed to write. Table names and column names are
# interpolated into SQL, so they must never come from user input.
USER_FIELDS = {
    "name", "email", "gmail_credentials", "resume_text", "linkedin_url",
    "phone", "auto_apply", "invite_token", "login_token", "is_owner",
}
CRITERIA_FIELDS = {
    "job_titles", "min_salary", "max_salary", "locations", "remote_preference",
    "seniority_levels", "industries_include", "industries_exclude",
    "keywords_include", "keywords_exclude", "greenhouse_companies", "lever_companies",
}
USER_JOB_FIELDS = {
    "score", "score_reason", "status", "digest_sent_at", "digest_batch",
    "digest_position", "selected_at", "applied_at", "cover_letter_text", "notes",
}
JOB_FIELDS = {
    "source", "external_id", "title", "company", "company_domain", "location",
    "salary_min", "salary_max", "remote_type", "description", "apply_url", "posted_at",
}

JSON_CRITERIA_FIELDS = [
    "job_titles", "locations", "seniority_levels",
    "industries_include", "industries_exclude",
    "keywords_include", "keywords_exclude",
    "greenhouse_companies", "lever_companies",
]


def set_db_path(path: str):
    global _db_path
    _db_path = path


def db_path() -> str:
    return _db_path or os.getenv("DB_PATH", "jobagent.db")


def get_conn():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _filter(kwargs: dict, allowed: set, what: str) -> dict:
    bad = set(kwargs) - allowed
    if bad:
        raise ValueError(f"unknown {what} field(s): {sorted(bad)}")
    return kwargs


def init_db(path: str | None = None):
    """Create the schema and apply column migrations. Safe to call repeatedly."""
    if path:
        set_db_path(path)
    conn = get_conn()
    conn.executescript(SCHEMA_PATH.read_text())

    # Additive migrations for databases created before these columns existed.
    for table, column, ddl in [
        ("users", "login_token", "ALTER TABLE users ADD COLUMN login_token TEXT"),
        ("users", "is_owner", "ALTER TABLE users ADD COLUMN is_owner INTEGER DEFAULT 0"),
        ("user_jobs", "digest_batch", "ALTER TABLE user_jobs ADD COLUMN digest_batch TEXT"),
        ("user_jobs", "digest_position", "ALTER TABLE user_jobs ADD COLUMN digest_position INTEGER"),
    ]:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(ddl)

    conn.commit()
    conn.close()


# ── Users ──────────────────────────────────────────────────────────────────

def create_user(name: str, email: str, invite_token: str | None = None) -> int:
    conn = get_conn()
    is_owner = 1 if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else 0
    cur = conn.execute(
        """INSERT INTO users (name, email, invite_token, login_token, is_owner)
           VALUES (?, ?, ?, ?, ?)""",
        (name, email, invite_token or str(uuid.uuid4()), str(uuid.uuid4()), is_owner),
    )
    user_id = cur.lastrowid
    conn.execute("INSERT INTO user_criteria (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()
    return user_id


def _one(sql: str, params: tuple) -> dict | None:
    conn = get_conn()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    return _one("SELECT * FROM users WHERE id=?", (user_id,))


def get_user_by_email(email: str) -> dict | None:
    return _one("SELECT * FROM users WHERE email=?", (email.lower(),))


def get_user_by_login_token(token: str) -> dict | None:
    if not token:
        return None
    return _one("SELECT * FROM users WHERE login_token=?", (token,))


def get_user_by_invite_token(token: str) -> dict | None:
    if not token:
        return None
    return _one("SELECT * FROM users WHERE invite_token=?", (token,))


def get_all_users() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_users() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return n


def update_user(user_id: int, fields: dict):
    fields = _filter(dict(fields), USER_FIELDS, "user")
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(
        f"UPDATE users SET {assignments} WHERE id=?", [*fields.values(), user_id]
    )
    conn.commit()
    conn.close()


# ── Criteria ───────────────────────────────────────────────────────────────

def get_criteria(user_id: int) -> dict | None:
    row = _one("SELECT * FROM user_criteria WHERE user_id=?", (user_id,))
    if not row:
        return None
    for field in JSON_CRITERIA_FIELDS:
        row[field] = json.loads(row.get(field) or "[]")
    return row


def update_criteria(user_id: int, fields: dict):
    fields = _filter(dict(fields), CRITERIA_FIELDS, "criteria")
    if not fields:
        return
    for key, value in fields.items():
        if isinstance(value, list):
            fields[key] = json.dumps(value)
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(
        f"UPDATE user_criteria SET {assignments} WHERE user_id=?",
        [*fields.values(), user_id],
    )
    conn.commit()
    conn.close()


# ── Sessions ───────────────────────────────────────────────────────────────
# `state` holds in-progress onboarding answers. `user_id` is the signed-in
# identity — it is set only by an authenticated path, never from onboarding input.

def get_session(session_id: str) -> dict | None:
    row = _one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        return None
    row["state"] = json.loads(row.get("state") or "{}")
    return row


def get_session_state(session_id: str) -> dict:
    sess = get_session(session_id)
    return sess["state"] if sess else {}


def create_session(session_id: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, state) VALUES (?, '{}')", (session_id,)
    )
    conn.commit()
    conn.close()


def set_session_state(session_id: str, state: dict):
    conn = get_conn()
    conn.execute(
        """INSERT INTO sessions (id, state, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
             state=excluded.state, updated_at=CURRENT_TIMESTAMP""",
        (session_id, json.dumps(state)),
    )
    conn.commit()
    conn.close()


def attach_user_to_session(session_id: str, user_id: int):
    """Sign a session in. Only call this once identity has actually been proven."""
    conn = get_conn()
    conn.execute(
        """INSERT INTO sessions (id, user_id, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
             user_id=excluded.user_id, updated_at=CURRENT_TIMESTAMP""",
        (session_id, user_id),
    )
    conn.commit()
    conn.close()


def clear_session(session_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    conn.commit()
    conn.close()


# ── Jobs ───────────────────────────────────────────────────────────────────

def upsert_job(source: str, external_id: str, title: str, company: str,
               apply_url: str, **kwargs) -> int:
    kwargs = _filter(kwargs, JOB_FIELDS, "job")
    kwargs.pop("source", None)
    kwargs.pop("external_id", None)
    kwargs.pop("title", None)
    kwargs.pop("company", None)
    kwargs.pop("apply_url", None)

    columns = ["source", "external_id", "title", "company", "apply_url", *kwargs]
    values = [source, external_id, title, company, apply_url, *kwargs.values()]
    placeholders = ", ".join("?" * len(columns))

    conn = get_conn()
    conn.execute(
        f"""INSERT INTO jobs ({", ".join(columns)}) VALUES ({placeholders})
            ON CONFLICT(source, external_id) DO UPDATE SET
              title=excluded.title,
              description=COALESCE(excluded.description, jobs.description),
              fetched_at=CURRENT_TIMESTAMP""",
        values,
    )
    job_id = conn.execute(
        "SELECT id FROM jobs WHERE source=? AND external_id=?", (source, external_id)
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return job_id


def get_user_jobs(user_id: int, status: str | None = None) -> list[dict]:
    sql = """SELECT uj.*, j.title, j.company, j.company_domain, j.location,
                    j.salary_min, j.salary_max, j.remote_type, j.apply_url,
                    j.description
             FROM user_jobs uj JOIN jobs j ON uj.job_id = j.id
             WHERE uj.user_id=?"""
    params: list = [user_id]
    if status:
        sql += " AND uj.status=?"
        params.append(status)
    sql += " ORDER BY uj.score DESC, uj.id DESC"
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_user_job(user_id: int, job_id: int, score: int, score_reason: str):
    conn = get_conn()
    conn.execute(
        """INSERT INTO user_jobs (user_id, job_id, score, score_reason)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id, job_id) DO NOTHING""",
        (user_id, job_id, score, score_reason),
    )
    conn.commit()
    conn.close()


def get_user_job(uj_id: int) -> dict | None:
    return _one(
        """SELECT uj.*, j.title, j.company, j.location, j.apply_url,
                  j.description, j.salary_min, j.salary_max
           FROM user_jobs uj JOIN jobs j ON uj.job_id = j.id
           WHERE uj.id=?""",
        (uj_id,),
    )


def get_owned_user_job(uj_id: int, user_id: int) -> dict | None:
    """Fetch a user_job only if it belongs to this user. Use this on every mutation."""
    uj = get_user_job(uj_id)
    if not uj or uj["user_id"] != user_id:
        return None
    return uj


def update_user_job(uj_id: int, fields: dict):
    fields = _filter(dict(fields), USER_JOB_FIELDS, "user_job")
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(
        f"UPDATE user_jobs SET {assignments} WHERE id=?", [*fields.values(), uj_id]
    )
    conn.commit()
    conn.close()


def get_unsent_user_jobs(user_id: int) -> list[dict]:
    return get_user_jobs(user_id, status="new")


def mark_digest_sent(user_id: int, uj_ids: list[int], batch: str):
    """Record which jobs went out in this digest, and at which number.

    The positions are what the user replies with ("1, 3, 5"), so they have to be
    stored — the status changes to 'sent' immediately and the old code then tried
    to resolve reply numbers against the (now empty) 'new' list.
    """
    conn = get_conn()
    for position, uj_id in enumerate(uj_ids, start=1):
        conn.execute(
            """UPDATE user_jobs
               SET status='sent', digest_sent_at=CURRENT_TIMESTAMP,
                   digest_batch=?, digest_position=?
               WHERE id=? AND user_id=?""",
            (batch, position, uj_id, user_id),
        )
    conn.commit()
    conn.close()


def get_latest_digest_batch(user_id: int) -> str | None:
    conn = get_conn()
    row = conn.execute(
        """SELECT digest_batch FROM user_jobs
           WHERE user_id=? AND digest_batch IS NOT NULL
           ORDER BY digest_sent_at DESC, id DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_digest_batch_jobs(user_id: int, batch: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT uj.*, j.title, j.company, j.apply_url
           FROM user_jobs uj JOIN jobs j ON uj.job_id = j.id
           WHERE uj.user_id=? AND uj.digest_batch=?
           ORDER BY uj.digest_position""",
        (user_id, batch),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Digest runs (so the dashboard can say what happened) ───────────────────

def start_digest_run(user_id: int) -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO digest_runs (user_id) VALUES (?)", (user_id,))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def finish_digest_run(run_id: int, status: str, jobs_found: int = 0,
                      jobs_matched: int = 0, email_sent: bool = False,
                      message: str = ""):
    conn = get_conn()
    conn.execute(
        """UPDATE digest_runs
           SET finished_at=CURRENT_TIMESTAMP, status=?, jobs_found=?,
               jobs_matched=?, email_sent=?, message=?
           WHERE id=?""",
        (status, jobs_found, jobs_matched, 1 if email_sent else 0, message, run_id),
    )
    conn.commit()
    conn.close()


def get_latest_digest_run(user_id: int) -> dict | None:
    return _one(
        "SELECT * FROM digest_runs WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
