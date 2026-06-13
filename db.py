"""SQLite storage for AgentLens monitoring. stdlib only."""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agentlens.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    added_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    scanned_at TEXT NOT NULL,
    score INTEGER NOT NULL,
    grade TEXT NOT NULL,
    report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    created_at TEXT NOT NULL,
    severity TEXT NOT NULL,          -- 'regression' | 'improvement'
    summary TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0
);
"""


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _now():
    return datetime.now(timezone.utc).isoformat()


def add_store(url):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO stores (url, added_at) VALUES (?, ?)",
                  (url, _now()))
        c.execute("UPDATE stores SET active = 1 WHERE url = ?", (url,))
        return c.execute("SELECT id FROM stores WHERE url = ?", (url,)).fetchone()["id"]


def remove_store(url):
    with _conn() as c:
        c.execute("UPDATE stores SET active = 0 WHERE url = ?", (url,))


def list_stores():
    with _conn() as c:
        rows = c.execute("""
            SELECT s.id, s.url, s.added_at,
                   (SELECT score FROM scans WHERE store_id = s.id
                    ORDER BY scanned_at DESC LIMIT 1) AS last_score,
                   (SELECT grade FROM scans WHERE store_id = s.id
                    ORDER BY scanned_at DESC LIMIT 1) AS last_grade,
                   (SELECT scanned_at FROM scans WHERE store_id = s.id
                    ORDER BY scanned_at DESC LIMIT 1) AS last_scanned,
                   (SELECT COUNT(*) FROM alerts WHERE store_id = s.id AND seen = 0)
                        AS unseen_alerts
            FROM stores s WHERE s.active = 1 ORDER BY s.url""").fetchall()
        return [dict(r) for r in rows]


def record_scan(store_id, report):
    with _conn() as c:
        c.execute("INSERT INTO scans (store_id, scanned_at, score, grade, report_json) "
                  "VALUES (?, ?, ?, ?, ?)",
                  (store_id, report["scanned_at"], report["score"], report["grade"],
                   json.dumps(report)))


def last_scan(store_id):
    with _conn() as c:
        row = c.execute("SELECT report_json FROM scans WHERE store_id = ? "
                        "ORDER BY scanned_at DESC LIMIT 1", (store_id,)).fetchone()
        return json.loads(row["report_json"]) if row else None


def scan_history(store_id, limit=30):
    with _conn() as c:
        rows = c.execute("SELECT scanned_at, score, grade FROM scans "
                         "WHERE store_id = ? ORDER BY scanned_at DESC LIMIT ?",
                         (store_id, limit)).fetchall()
        return [dict(r) for r in rows]


def record_alert(store_id, severity, summary, detail):
    with _conn() as c:
        c.execute("INSERT INTO alerts (store_id, created_at, severity, summary, "
                  "detail_json) VALUES (?, ?, ?, ?, ?)",
                  (store_id, _now(), severity, summary, json.dumps(detail)))


def list_alerts(limit=50):
    with _conn() as c:
        rows = c.execute("""
            SELECT a.id, a.created_at, a.severity, a.summary, a.detail_json, a.seen,
                   s.url
            FROM alerts a JOIN stores s ON s.id = a.store_id
            ORDER BY a.created_at DESC LIMIT ?""", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            out.append(d)
        return out


def mark_alerts_seen():
    with _conn() as c:
        c.execute("UPDATE alerts SET seen = 1")
