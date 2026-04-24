from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_or_create_fernet_key(key_path: Path) -> bytes:
    _ensure_parent(key_path)
    if key_path.exists():
        return key_path.read_bytes()
    key = Fernet.generate_key()
    tmp_path = key_path.with_suffix(key_path.suffix + ".tmp")
    tmp_path.write_bytes(key)
    os.replace(tmp_path, key_path)
    return key


def get_fernet(key_path: Path) -> Fernet:
    return Fernet(load_or_create_fernet_key(key_path))
