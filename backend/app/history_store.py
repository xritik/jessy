"""
Persistent action log — SQLite, stdlib only. Every event that would
otherwise only live in the frontend's in-memory activity feed (Step
11.3) gets written here too, so Action History survives reloads and
restarts.
"""

import sqlite3
import time
import json
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "history.db"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            capability TEXT,
            detail TEXT,
            payload TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_type ON actions(type)")
    conn.commit()
    conn.close()


def record_action(
    session_id: str,
    type: str,
    capability: Optional[str] = None,
    detail: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """Call this at every point an event is emitted (WS push, chat
    received, error raised) — mirrors the live feed 1:1."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO actions (session_id, type, capability, detail, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            type,
            capability,
            detail,
            json.dumps(payload) if payload else None,
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def get_actions(
    session_id: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    clauses, params = [], []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if type:
        clauses.append("type = ?")
        params.append(type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = conn.execute(f"SELECT COUNT(*) FROM actions {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT id, session_id, type, capability, detail, payload, created_at
        FROM actions {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    conn.close()

    items = [
        {
            "id": row["id"],
            "session_id": row["session_id"],
            "type": row["type"],
            "capability": row["capability"],
            "detail": row["detail"],
            "payload": json.loads(row["payload"]) if row["payload"] else None,
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}

def list_conversations(limit: int = 50, offset: int = 0) -> dict:
    """One row per session_id, with a preview of the first message,
    timestamps, and how many user turns it contains."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(DISTINCT session_id) FROM actions").fetchone()[0]
    rows = conn.execute(
        """
        SELECT
            a.session_id AS session_id,
            MIN(a.created_at) AS started_at,
            MAX(a.created_at) AS last_at,
            SUM(CASE WHEN a.type = 'chat_received' THEN 1 ELSE 0 END) AS message_count,
            (
                SELECT detail FROM actions a2
                WHERE a2.session_id = a.session_id AND a2.type = 'chat_received'
                ORDER BY a2.created_at ASC LIMIT 1
            ) AS first_message
        FROM actions a
        GROUP BY a.session_id
        ORDER BY last_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    conn.close()

    items = [
        {
            "session_id": row["session_id"],
            "started_at": row["started_at"],
            "last_at": row["last_at"],
            "message_count": row["message_count"],
            "preview": (row["first_message"] or "")[:120],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_conversation(session_id: str) -> list[dict]:
    """Ordered transcript: just the user/assistant turns for this session,
    oldest first — capability/error events are left out of the reading view."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, type, detail, created_at
        FROM actions
        WHERE session_id = ? AND type IN ('chat_received', 'chat_response')
        ORDER BY created_at ASC
        """,
        (session_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "role": "user" if row["type"] == "chat_received" else "assistant",
            "text": row["detail"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]

def check_db_health() -> bool:
    """Quick reachability check for the Status page."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False
