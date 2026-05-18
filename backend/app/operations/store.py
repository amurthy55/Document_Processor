from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import OperationPublic, OperationStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(_hash_token(token), token_hash)


class OperationStore:
    def __init__(self, conn, fernet, operations_root, ttl_seconds):
        self.conn: sqlite3.Connection = conn
        self.fernet = fernet
        self.operations_root = Path(operations_root)
        self.ttl_seconds = int(ttl_seconds)
        self.operations_root.mkdir(parents=True, exist_ok=True)

    def create_operation(self, selected_service: str | None):
        operation_id = uuid.uuid4().hex
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        created_at = _utc_now()
        expires_at = created_at + timedelta(seconds=self.ttl_seconds)
        op_dir = self.operations_root / operation_id
        op_dir.mkdir(parents=True, exist_ok=True)

        self.conn.execute(
            """
            INSERT INTO operations (
                operation_id,
                session_token_hash,
                csrf_token_hash,
                selected_service,
                status,
                created_at,
                expires_at,
                locked_by_tab,
                op_dir
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                _hash_token(session_token),
                _hash_token(csrf_token),
                selected_service,
                OperationStatus.created.value,
                _iso(created_at),
                _iso(expires_at),
                None,
                str(op_dir),
            ),
        )
        self.conn.commit()

        return self.get_public(self.get_operation_row(operation_id)), session_token, csrf_token

    def get_operation_row(self, operation_id: str):
        return self.conn.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()

    def get_public(self, row) -> OperationPublic:
        if row is None:
            raise ValueError("operation row is required")
        return OperationPublic(
            operation_id=row["operation_id"],
            selected_service=row["selected_service"],
            status=OperationStatus(row["status"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            locked_by_tab=row["locked_by_tab"],
        )

    def require_valid_token(self, row, token: str) -> None:
        if not token or not _check_token(token, row["session_token_hash"]):
            raise PermissionError("invalid operation token")

    def require_valid_csrf(self, row, csrf_token: str) -> None:
        if not csrf_token or not _check_token(csrf_token, row["csrf_token_hash"]):
            raise PermissionError("invalid csrf token")

    def lock_to_tab(self, operation_id: str, tab_session_id: str) -> None:
        row = self.get_operation_row(operation_id)
        if row is None:
            raise RuntimeError("operation_not_found")
        locked_by = row["locked_by_tab"]
        if locked_by and locked_by != tab_session_id:
            raise RuntimeError("operation_locked")
        self.conn.execute(
            "UPDATE operations SET locked_by_tab = ?, status = ? WHERE operation_id = ?",
            (tab_session_id, OperationStatus.active.value, operation_id),
        )
        self.conn.commit()

    def unlock(self, operation_id: str, tab_session_id: str) -> None:
        row = self.get_operation_row(operation_id)
        if row is None:
            raise RuntimeError("operation_not_found")
        locked_by = row["locked_by_tab"]
        if locked_by and locked_by != tab_session_id:
            raise RuntimeError("operation_locked")
        self.conn.execute(
            "UPDATE operations SET locked_by_tab = NULL WHERE operation_id = ?",
            (operation_id,),
        )
        self.conn.commit()

    def set_status(self, operation_id: str, status: OperationStatus | str) -> None:
        status_value = status.value if isinstance(status, OperationStatus) else str(status)
        self.conn.execute(
            "UPDATE operations SET status = ? WHERE operation_id = ?",
            (status_value, operation_id),
        )
        self.conn.commit()

    def read_structured_data(self, row) -> dict[str, Any]:
        path = self._structured_path(row)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def write_structured_data(self, row, structured_data: dict[str, Any]) -> None:
        path = self._structured_path(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(structured_data, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def expire_due_operations(self) -> None:
        now = _iso(_utc_now())
        self.conn.execute(
            """
            UPDATE operations
            SET status = ?
            WHERE expires_at <= ?
              AND status NOT IN (?, ?)
            """,
            (
                OperationStatus.expired.value,
                now,
                OperationStatus.completed.value,
                OperationStatus.failed.value,
            ),
        )
        self.conn.commit()

    def _structured_path(self, row) -> Path:
        return Path(row["op_dir"]) / "structured_data.json"
