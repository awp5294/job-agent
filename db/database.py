import sqlite3
import json
import os
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "jobagent.db")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    conn.close()


# ── Users ──────────────────────────────────────────────────────────────────

def create_user(name: str, email: str, invite_token: str = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO users (name, email, invite_token) VALUES (?, ?, ?)",
        (name, email, invite_token),
    )
    user_id = cur.lastrowid
    conn.execute(
        "INSERT INTO user_criteria (user_id) VALUES (?)", (user_id,)
    )
    conn.commit()
    conn.close()
    return user_id


def get_user_by_email(email: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_users() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user(user_id: int, **kwargs):
    if not kwargs:
        return
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    conn = get_conn()
    conn.execute(f"UPDATE users SET {fields} WHERE id=?", values)
    conn.commit()
    conn.close()


# ── Criteria ───────────────────────────────────────────────────────────────

def get_criteria(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM user_criteria WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    for field in [
        "job_titles", "locations", "seniority_levels",
        "industries_include", "industries_exclude",
        "keywords_include", "keywords_exclude",
        "greenhouse_companies", "lever_companies",
    ]:
        d[field] = json.loads(d.get(field) or "[]")
    return d


def update_criteria(user_id: int, **kwargs):
    for key in kwargs:
        if isinstance(kwargs[key], list):
            kwargs[key] = json.dumps(kwargs[key])
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    conn = get_conn()
    conn.execute(
        f"UPDATE user_criteria SET {fields} WHERE user_id=?", values
    )
    conn.commit()
    conn.close()


# ── Sessions ───────────────────────────────────────────────────────────────

def get_session(session_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["state"] = json.loads(d.get("state") or "{}")
    return d


def upsert_session(session_id: str, state: dict, user_id: int = None):
    conn = get_conn()
    conn.execute(
        """INSERT INTO sessions (id, state, user_id, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
             state=excluded.state,
             user_id=COALESCE(excluded.user_id, sessions.user_id),
             updated_at=CURRENT_TIMESTAMP""",
        (session_id, json.dumps(state), user_id),
    )
    conn.commit()
    conn.close()


# ── Jobs ───────────────────────────────────────────────────────────────────

def upsert_job(
    source: str, external_id: str, title: str, company: str,
    apply_url: str, **kwargs
) -> int:
    conn = get_conn()
    fields = ["source", "external_id", "title", "company", "apply_url"] + list(kwargs)
    placeholders = ", ".join("?" * len(fields))
    values = [source, external_id, title, company, apply_url] + list(kwargs.values())
    cur = conn.execute(
        f"""INSERT INTO jobs ({", ".join(fields)}) VALUES ({placeholders})
            ON CONFLICT(source, external_id) DO UPDATE SET
              title=excluded.title, description=excluded.description,
              fetched_at=CURRENT_TIMESTAMP
            RETURNING id""",
        values,
    )
    job_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return job_id


def get_user_jobs(user_id: int, status: str = None) -> list[dict]:
    conn = get_conn()
    sql = """SELECT uj.*, j.title, j.company, j.company_domain, j.location,
                    j.salary_min, j.salary_max, j.remote_type, j.apply_url,
                    j.description
             FROM user_jobs uj JOIN jobs j ON uj.job_id = j.id
             WHERE uj.user_id=?"""
    params = [user_id]
    if status:
        sql += " AND uj.status=?"
        params.append(status)
    sql += " ORDER BY uj.score DESC, uj.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_user_job(user_id: int, job_id: int, score: int, reason: str):
    conn = get_conn()
    conn.execute(
        """INSERT INTO user_jobs (user_id, job_id, score, score_reason)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id, job_id) DO NOTHING""",
        (user_id, job_id, score, reason),
    )
    conn.commit()
    conn.close()


def update_user_job(user_job_id: int, **kwargs):
    if not kwargs:
        return
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [user_job_id]
    conn = get_conn()
    conn.execute(f"UPDATE user_jobs SET {fields} WHERE id=?", values)
    conn.commit()
    conn.close()


def get_user_job_by_id(uj_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        """SELECT uj.*, j.title, j.company, j.location, j.apply_url,
                  j.description, j.salary_min, j.salary_max
           FROM user_jobs uj JOIN jobs j ON uj.job_id = j.id
           WHERE uj.id=?""",
        (uj_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_unsent_user_jobs(user_id: int) -> list[dict]:
    return get_user_jobs(user_id, status="new")


def mark_digest_sent(user_id: int):
    conn = get_conn()
    conn.execute(
        """UPDATE user_jobs SET status='sent', digest_sent_at=CURRENT_TIMESTAMP
           WHERE user_id=? AND status='new'""",
        (user_id,),
    )
    conn.commit()
    conn.close()
