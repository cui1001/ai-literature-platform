"""SQLite 连接与事务。

- 单文件数据库，WAL 模式，写操作通过进程内 RLock 串行化；
- ``isolation_level=None``（自动提交），事务由 ``transaction()`` 显式管理；
- 连接按数据库路径缓存，测试可用 ``reset_database()`` 清空重建。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..core.config import Settings, get_settings
from ..core.logging import get_logger

log = get_logger("db")

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_CONNECTIONS: dict[str, sqlite3.Connection] = {}
_LOCK = threading.RLock()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


def get_connection(settings: Settings | None = None) -> sqlite3.Connection:
    s = settings or get_settings()
    s.ensure_dirs()
    key = str(s.db_path)
    with _LOCK:
        conn = _CONNECTIONS.get(key)
        if conn is None:
            conn = _connect(s.db_path)
            init_schema(conn)
            _CONNECTIONS[key] = conn
            log.info("sqlite ready | %s", key, extra={"tag": "init"})
        return conn


def reset_database(settings: Settings | None = None) -> None:
    """关闭并删除数据库文件，用于测试与 /api/demo/reset。"""
    s = settings or get_settings()
    key = str(s.db_path)
    with _LOCK:
        conn = _CONNECTIONS.pop(key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        for suffix in ("", "-wal", "-shm"):
            path = Path(key + suffix)
            if path.exists():
                path.unlink()
    get_connection(s)


def close_all() -> None:
    with _LOCK:
        for conn in _CONNECTIONS.values():
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        _CONNECTIONS.clear()


@contextmanager
def transaction(settings: Settings | None = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(settings)
    with _LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def _rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def query(sql: str, params: Sequence[Any] = (), settings: Settings | None = None) -> list[dict[str, Any]]:
    conn = get_connection(settings)
    with _LOCK:
        return _rows_to_dicts(conn.execute(sql, tuple(params)).fetchall())


def query_one(sql: str, params: Sequence[Any] = (), settings: Settings | None = None) -> dict[str, Any] | None:
    rows = query(sql, params, settings)
    return rows[0] if rows else None


def execute(sql: str, params: Sequence[Any] = (), settings: Settings | None = None) -> int:
    conn = get_connection(settings)
    with _LOCK:
        cursor = conn.execute(sql, tuple(params))
        return cursor.rowcount if cursor.rowcount is not None else 0


def executemany(sql: str, seq: Iterable[Sequence[Any]], settings: Settings | None = None) -> int:
    conn = get_connection(settings)
    with _LOCK:
        cursor = conn.executemany(sql, list(seq))
        return cursor.rowcount or 0


def execute_script(sql: str, settings: Settings | None = None) -> None:
    conn = get_connection(settings)
    with _LOCK:
        conn.executescript(sql)
