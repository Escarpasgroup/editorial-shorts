"""SQLite store. One file per workspace; every write that can race runs inside BEGIN IMMEDIATE."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, theme TEXT, path TEXT NOT NULL CHECK(path IN ('cut','original')),
  page_id TEXT, angle TEXT, priority INTEGER DEFAULT 0, status TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS refs(
  id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, data TEXT NOT NULL, cluster TEXT, story_id TEXT,
  demo INTEGER NOT NULL DEFAULT 0, collected_at TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, story_id TEXT, role TEXT, action TEXT, payload TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS curations(story_id TEXT PRIMARY KEY, data TEXT NOT NULL, ts TEXT);
CREATE TABLE IF NOT EXISTS scripts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, story_id TEXT, data TEXT, verdict TEXT, problems TEXT, sha TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS renders(
  id TEXT PRIMARY KEY, story_id TEXT, version INTEGER, path TEXT, sha256 TEXT, manifest TEXT, status TEXT, ts TEXT,
  UNIQUE(story_id, version));
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT, render_id TEXT, sha256 TEXT, decision TEXT, notes TEXT, auto TEXT,
  reviewer TEXT, valid INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS publications(
  id TEXT PRIMARY KEY, story_id TEXT, page_id TEXT, platform TEXT, account TEXT, adapter TEXT, render_id TEXT,
  sha256 TEXT, state TEXT, idem_key TEXT UNIQUE, worker TEXT, lease_until REAL, attempts INTEGER DEFAULT 0,
  remote_id TEXT, remote_url TEXT, last_error TEXT, meta TEXT, created_at TEXT, updated_at TEXT,
  UNIQUE(story_id, page_id, platform, account));
CREATE TABLE IF NOT EXISTS pub_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, pub_id TEXT, from_state TEXT, to_state TEXT, worker TEXT, note TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT, pub_id TEXT, win TEXT, data TEXT, demo INTEGER, source TEXT, ts TEXT,
  UNIQUE(pub_id, win, source));
"""


def connect(workspace):
    path = Path(workspace) / "data" / "editorial.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn):
    """Write transaction that takes the database write lock up front (no lost updates between workers)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def row(conn, sql, *args):
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def rows(conn, sql, *args):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def jload(s, default=None):
    return json.loads(s) if s else default


def jdump(o):
    return json.dumps(o, ensure_ascii=False, sort_keys=True)


def log_event(conn, story_id, role, action, payload, ts):
    conn.execute("INSERT INTO events(story_id, role, action, payload, ts) VALUES(?,?,?,?,?)",
                 (story_id, role, action, jdump(payload), ts))
