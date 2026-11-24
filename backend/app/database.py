import sqlite3
import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from app.config import DATABASE_URL


def get_db_path() -> str:
    """Extract filesystem path from DATABASE_URL."""
    if DATABASE_URL.startswith("sqlite:///"):
        path = DATABASE_URL.replace("sqlite:///", "", 1)
        # Windows handling for absolute paths like sqlite:///C:/...
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        # On Windows outside Docker, /data/app.db maps to root C:\data\app.db which is non-writable
        if os.name == "nt" and (path.startswith("/data") or path.startswith("\\data")):
            # Fallback to local workspace data folder for local Windows development
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            local_data = os.path.join(project_root, "data")
            try:
                os.makedirs(local_data, exist_ok=True)
            except Exception:
                pass
            path = os.path.join(local_data, os.path.basename(path))
        return path
    elif DATABASE_URL.startswith("sqlite://"):
        return DATABASE_URL.replace("sqlite://", "", 1)
    return DATABASE_URL


def get_connection() -> sqlite3.Connection:
    db_path = get_db_path()
    # Ensure directory exists
    dir_name = os.path.dirname(db_path)
    if dir_name and not os.path.exists(dir_name):
        try:
            os.makedirs(dir_name, exist_ok=True)
        except Exception:
            pass
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Creates the SQLite tables if they do not exist."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id  TEXT NOT NULL,
              role        TEXT NOT NULL,   -- 'user' or 'assistant'
              content     TEXT NOT NULL,
              metadata    TEXT,            -- JSON string, assistant messages only
              created_at  TEXT NOT NULL    -- server time, ISO-8601
            );
            """)
            conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
              session_id    TEXT PRIMARY KEY,
              system_prompt TEXT,
              created_at    TEXT NOT NULL
            );
            """)
    finally:
        conn.close()


# Ensure tables are initialized on module load
try:
    init_db()
except Exception:
    pass


def save_session_prompt(session_id: str, system_prompt: Optional[str]) -> None:
    """Records or updates session system prompt."""
    if not system_prompt:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
            INSERT INTO sessions (session_id, system_prompt, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET system_prompt=excluded.system_prompt;
            """, (session_id, system_prompt, now_iso))
    finally:
        conn.close()


def get_session_prompt(session_id: str) -> Optional[str]:
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row and row["system_prompt"]:
            return row["system_prompt"]
        return None
    finally:
        conn.close()


def get_session_messages(session_id: str) -> List[Dict[str, Any]]:
    """Returns stored history for a session ordered oldest first."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT id, session_id, role, content, metadata, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,)
        )
        rows = cursor.fetchall()
        result = []
        for r in rows:
            meta = None
            if r["metadata"]:
                try:
                    meta = json.loads(r["metadata"])
                except Exception:
                    meta = None
            result.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "metadata": meta,
                "created_at": r["created_at"]
            })
        return result
    finally:
        conn.close()


def session_exists(session_id: str) -> bool:
    """Checks if a session has any messages or is registered."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (session_id,))
        return cursor.fetchone() is not None
    finally:
        conn.close()


def save_message_pair(
    session_id: str,
    user_content: str,
    assistant_content: str,
    assistant_metadata: Dict[str, Any]
) -> None:
    """
    Saves a successful user message and assistant message to SQLite.
    Only called when request succeeds.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps({
        "resolution_source": assistant_metadata.get("resolution_source"),
        "input_tokens": assistant_metadata.get("input_tokens", 0),
        "output_tokens": assistant_metadata.get("output_tokens", 0)
    })

    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'user', ?, NULL, ?)",
                (session_id, user_content, now_iso)
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, assistant_content, meta_json, now_iso)
            )
    finally:
        conn.close()
