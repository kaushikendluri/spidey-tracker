"""SQLite access layer.

The app is read-heavy with a background writer (the demo simulator), so every
connection runs in WAL mode with a busy timeout. Connections are per-thread:
Flask request threads get one via `get_db()`, background threads open their own
with `connection()`.
"""

import os
import sqlite3
import threading
import time

from flask import current_app, g

_local = threading.local()
# Serialises writes from request threads and the simulator thread. WAL already
# allows concurrent readers; this avoids write contention retries entirely.
write_lock = threading.RLock()


def _configure(conn):
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def connect(path):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    return _configure(conn)


def get_db():
    """Connection bound to the current Flask request."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


class connection(object):
    """Context manager for use outside a request context.

    Reuses one connection per thread so the simulator does not churn handles.
    """

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        conn = getattr(_local, "conn", None)
        if conn is None or getattr(_local, "path", None) != self.path:
            if conn is not None:
                conn.close()
            conn = connect(self.path)
            _local.conn = conn
            _local.path = self.path
        return conn

    def __exit__(self, *_exc):
        return False


def init_db(app):
    """Create the schema if absent. Safe to call on every boot."""
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
    with open(schema_path, "r") as handle:
        script = handle.read()
    with connection(app.config["DATABASE"]) as conn:
        with write_lock:
            conn.executescript(script)
            conn.commit()


# --- helpers ------------------------------------------------------------


class transaction(object):
    """Hold the write lock across a multi-statement operation.

    Individual `execute` calls are already serialised, but an operation like
    "insert sighting, insert analysis, insert detection, update camera" must
    not interleave with another writer or SQLite starts returning
    'database is locked'. write_lock is reentrant, so nested execute() calls
    inside this block are free.
    """

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        write_lock.acquire()
        return self.conn

    def __exit__(self, exc_type, *_rest):
        try:
            if exc_type is not None:
                self.conn.rollback()
        finally:
            write_lock.release()
        return False


def query(conn, sql, params=(), one=False):
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(conn, sql, params=()):
    with write_lock:
        cur = conn.execute(sql, params)
        conn.commit()
        rowid = cur.lastrowid
        count = cur.rowcount
        cur.close()
    return rowid, count


def get_setting(conn, key, default=None):
    row = query(conn, "SELECT value FROM settings WHERE key = ?", (key,), one=True)
    return row["value"] if row else default


def set_setting(conn, key, value):
    execute(
        conn,
        "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, str(value), time.time()),
    )


def dictify(row):
    return dict(row) if row is not None else None


def dictify_all(rows):
    return [dict(r) for r in rows]
