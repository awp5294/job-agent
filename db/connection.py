"""One connection API over SQLite and Postgres.

SQLite is the default and needs no setup, which is what you want when running
locally. Postgres takes over as soon as DATABASE_URL is set, which is what a
hosted deployment needs: Replit and most other hosts rebuild the container's
filesystem on redeploy and take a SQLite file with it, so every account and
resume would vanish the next time you pushed a change.

The two databases speak near-identical SQL for everything this app does. Three
things differ, and they are the only things translated here:

  * placeholders — ? in SQLite, %s in Postgres
  * auto-incrementing ids — AUTOINCREMENT in SQLite, SERIAL in Postgres
  * asking what columns a table has, for the migrations in database.py

The schema is translated from db/schema.sql rather than kept as a second file,
so the two backends can't drift apart.
"""
import os
import re
import sqlite3

SQLITE = "sqlite"
POSTGRES = "postgres"


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def backend() -> str:
    return POSTGRES if database_url() else SQLITE


def translate_sql(sql: str, escape_percent: bool) -> str:
    """Rewrite SQLite placeholders for Postgres, leaving string literals alone.

    A naive replace would corrupt any ? or % inside quotes. None of the current
    SQL has one, but a WHERE ... LIKE '%foo' would be silently mangled the day
    someone adds it, and that's a hard bug to see.
    """
    out = []
    in_string = False
    i = 0
    while i < len(sql):
        char = sql[i]
        if in_string:
            if char == "'":
                if sql[i + 1:i + 2] == "'":   # '' is an escaped quote
                    out.append("''")
                    i += 2
                    continue
                in_string = False
            out.append("%%" if char == "%" and escape_percent else char)
        elif char == "'":
            in_string = True
            out.append(char)
        elif char == "?":
            out.append("%s")
        elif char == "%" and escape_percent:
            out.append("%%")
        else:
            out.append(char)
        i += 1
    return "".join(out)


def translate_schema(ddl: str) -> str:
    """The one DDL difference between the two backends."""
    return re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "SERIAL PRIMARY KEY",
        ddl,
        flags=re.IGNORECASE,
    )


class Connection:
    """A connection that takes SQLite-flavoured SQL whichever backend is behind it."""

    def __init__(self, raw, kind: str):
        self._raw = raw
        self.kind = kind

    def execute(self, sql: str, params=()):
        if self.kind == SQLITE:
            return self._raw.execute(sql, params)
        # psycopg only interpolates when params is not None, and only then does a
        # literal % need doubling. Passing None keeps % alone in parameterless SQL.
        params = list(params) if params else None
        return self._raw.execute(translate_sql(sql, escape_percent=params is not None),
                                 params)

    def executescript(self, ddl: str):
        if self.kind == SQLITE:
            return self._raw.executescript(ddl)
        return self._raw.execute(translate_schema(ddl))

    def columns(self, table: str) -> set[str]:
        """Column names on a table, for the additive migrations in database.py."""
        if self.kind == SQLITE:
            # No placeholders in PRAGMA, so the table name is interpolated. Callers
            # pass literals; this must never take user input.
            return {r["name"] for r in self.execute(f"PRAGMA table_info({table})")}
        rows = self.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (table,),
        ).fetchall()
        return {r["column_name"] for r in rows}

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def connect(sqlite_path: str) -> Connection:
    url = database_url()
    if not url:
        raw = sqlite3.connect(sqlite_path)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA foreign_keys=ON")
        return Connection(raw, SQLITE)

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - depends on install
        raise RuntimeError(
            "DATABASE_URL is set but psycopg isn't installed. "
            "Run: pip install -r requirements.txt"
        ) from exc

    try:
        raw = psycopg.connect(url, row_factory=dict_row)
    except psycopg.Error as exc:
        raise RuntimeError(
            f"Could not connect to the Postgres in DATABASE_URL: {exc}"
        ) from exc
    return Connection(raw, POSTGRES)
