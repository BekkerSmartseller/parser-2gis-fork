from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from ..logger import logger

_SCHEMA_PATH = Path(__file__).with_name('schema.sql')

# Включение БД-режима глобально (DSN из env, секрет не в коде).
_DEFAULT_DB_URL_ENV = 'P2GIS_DB_URL'
# TTL кэша запросов по умолчанию (часы).
_DEFAULT_TTL_HOURS = 168

_pool = None
_pool_lock = threading.Lock()
_db_disabled = False


def dsn() -> str:
    """DSN БД из переменной окружения (P2GIS_DB_URL). Пустая строка = выключено."""
    return os.environ.get(_DEFAULT_DB_URL_ENV, '').strip()


def enabled() -> bool:
    """Доступен ли БД-режим (задана переменная окружения)."""
    return bool(dsn()) and not _db_disabled


def default_ttl_hours() -> int:
    """TTL кэша запросов по умолчанию (часы): env P2GIS_CACHE_TTL_HOURS или 168."""
    try:
        return max(1, int(os.environ.get('P2GIS_CACHE_TTL_HOURS', str(_DEFAULT_TTL_HOURS))))
    except ValueError:
        return _DEFAULT_TTL_HOURS


def _create_pool():
    """Ленивое создание пула соединений psycopg3 (ThreadedConnectionPool)."""
    global _pool, _db_disabled
    with _pool_lock:
        if _pool is not None:
            return _pool
        if _db_disabled:
            return None
        try:
            from psycopg_pool import ConnectionPool
            from psycopg.types.json import set_json_loads

            # jsonb -> Python dict при чтении (psycopg3 по умолчанию возвращает str).
            set_json_loads(__import__('json').loads)

            _pool = ConnectionPool(
                dsn(),
                min_size=1,
                max_size=5,
                open=False,
            )
            _pool.open(wait=True, timeout=15)
            logger.debug('[db] пул соединений открыт')
        except Exception as e:  # noqa: BLE001
            _db_disabled = True
            logger.warning('[db] БД недоступна, БД-режим отключён: %s', e)
            return None
        return _pool


@contextmanager
def connection() -> Iterator[Any]:
    """Контекст-менеджер соединения (строки — dict). Бросает RuntimeError, если БД выключена."""
    pool = _create_pool()
    if pool is None:
        raise RuntimeError('БД не настроена (задайте P2GIS_DB_URL)')
    with pool.connection() as conn:
        try:
            from psycopg.rows import dict_row
            conn.row_factory = dict_row
        except Exception:  # noqa: BLE001
            pass
        yield conn


def _split_statements(sql: str) -> list[str]:
    """Разбивает SQL-файл на отдельные statements (psycopg3 не умеет мульти)."""
    out = []
    for stmt in sql.split(';'):
        if stmt.strip():
            out.append(stmt.strip())
    return out


def apply_schema() -> bool:
    """Применяет schema.sql идемпотентно + превращает parse_requests в гипертаблицу.

    Returns:
        True при успехе, False — БД недоступна/схема не применилась.
    """
    if not dsn():
        return False
    try:
        with connection() as conn:
            sql = _SCHEMA_PATH.read_text(encoding='utf-8')
            for stmt in _split_statements(sql):
                conn.execute(stmt)
            conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            _make_hypertable(conn)
            conn.execute(
                "INSERT INTO p2gis.sync_state (id, last_synced_at) VALUES (1, NULL) "
                "ON CONFLICT (id) DO NOTHING")
            # Миграция: rubric_ids без ведущих пробелов (старые записи ' 4515').
            conn.execute(
                "UPDATE p2gis.records SET rubric_ids = "
                "ARRAY(SELECT trim(x) FROM unnest(rubric_ids) x) "
                "WHERE rubric_ids::text LIKE '% %'")
        logger.info('[db] схема p2gis применена')
        return True
    except Exception as e:  # noqa: BLE001
        logger.error('[db] не удалось применить схему p2gis: %s', e)
        return False


def _make_hypertable(conn) -> None:
    """Превращает p2gis.parse_requests в гипертаблицу TimescaleDB (graceful)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = 'p2gis' AND hypertable_name = 'parse_requests'"
        ).fetchone()
        if row:
            return
        conn.execute(
            "SELECT create_hypertable('p2gis.parse_requests', "
            "by_range('started_at', INTERVAL '1 day'), if_not_exists => TRUE)")
        try:
            conn.execute(
                "SELECT add_retention_policy('p2gis.parse_requests', INTERVAL '180 days', "
                "if_not_exists => TRUE)")
        except Exception:  # noqa: BLE001
            pass
        logger.info('[db] p2gis.parse_requests — гипертаблица TimescaleDB')
    except Exception as e:  # noqa: BLE001
        # TimescaleDB недоступен — оставляем обычной таблицей (plan: graceful).
        logger.warning('[db] гипертаблица недоступна, parse_requests — обычная таблица: %s', e)


def close_pool() -> None:
    """Закрывает пул (при остановке сервера)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:  # noqa: BLE001
                pass
            _pool = None


def is_hypertable(conn) -> bool:
    """Является ли parse_requests гипертаблицей (для тестов/диагностики)."""
    row = conn.execute(
        "SELECT 1 FROM timescaledb_information.hypertables "
        "WHERE hypertable_schema = 'p2gis' AND hypertable_name = 'parse_requests'"
    ).fetchone()
    return bool(row)
