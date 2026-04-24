from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            session_token_hash TEXT NOT NULL,
            csrf_token_hash TEXT NOT NULL,
            selected_service TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            locked_by_tab TEXT,
            op_dir TEXT NOT NULL
        )
        """
    )

    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(operations)").fetchall()
    }
    if "csrf_token_hash" not in cols:
        conn.execute(
            "ALTER TABLE operations ADD COLUMN csrf_token_hash TEXT NOT NULL DEFAULT ''"
        )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_expires_at ON operations(expires_at)")
    conn.commit()
