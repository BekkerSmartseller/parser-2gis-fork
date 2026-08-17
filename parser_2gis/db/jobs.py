# ================================
# parser_2gis/db/jobs.py
# Реестр задач БД-режима (p2gis.jobs): персистентность, heartbeat и
# самовосстановление после рестарта.
#
# ParseJob регистрирует себя в p2gis.jobs (queued -> running), шлёт heartbeat
# во время парсинга и пишет финальный статус. При старте сервера задачи,
# застрявшие в queued/running (процесс был убит), помечаются interrupted
# и пере-очередятся (см. recovery.requeue_interrupted_jobs) — собранные
# записи в p2gis.records при этом выживают (upsert по firm_id).
# ================================
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..logger import logger
from .connection import connection

_HEARTBEAT_STALE_MINUTES = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_loads(v: Optional[str], default: Any) -> Any:
    if not v:
        return default
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default


def create_job(job_id: str, urls: list[str], config: dict,
               fingerprints: list[Any], cache_hit: bool = False) -> None:
    """Регистрирует задачу в p2gis.jobs (статус queued). Идемпотентно."""
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO p2gis.jobs "
                "(id, urls, config, fingerprints, cache_hit, status, started_at, "
                " last_heartbeat, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 'queued', now(), now(), now(), now()) "
                "ON CONFLICT (id) DO UPDATE SET "
                "urls=EXCLUDED.urls, config=EXCLUDED.config, "
                "fingerprints=EXCLUDED.fingerprints, cache_hit=EXCLUDED.cache_hit, "
                "status='queued', error=NULL, started_at=now(), finished_at=NULL, "
                "last_heartbeat=now(), updated_at=now()",
                [job_id, json.dumps(urls, ensure_ascii=False),
                 json.dumps(config, ensure_ascii=False, default=str),
                 json.dumps(fingerprints, ensure_ascii=False, default=str),
                 bool(cache_hit)])
    except Exception as e:  # noqa: BLE001
        logger.warning('[jobs] create_job(%s): %s', job_id, e)


def update_status(job_id: str, status: str, error: Optional[str] = None,
                  finished: bool = False) -> None:
    """Пишет статус задачи (и, при finished=True, finished_at)."""
    try:
        with connection() as conn:
            finish_sql = ", finished_at=now()" if finished else ""
            conn.execute(
                "UPDATE p2gis.jobs SET status=%s, error=%s, updated_at=now(), "
                "last_heartbeat=now()" + finish_sql + " WHERE id=%s",
                [status, error, job_id])
    except Exception as e:  # noqa: BLE001
        logger.warning('[jobs] update_status(%s): %s', job_id, e)


def heartbeat(job_id: str) -> None:
    """Теппинг от живого парсинга (сбрасывает stale-детектор)."""
    try:
        with connection() as conn:
            conn.execute(
                "UPDATE p2gis.jobs SET last_heartbeat=now(), updated_at=now() "
                "WHERE id=%s", [job_id])
    except Exception as e:  # noqa: BLE001
        logger.warning('[jobs] heartbeat(%s): %s', job_id, e)


def _collect_active(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, urls, config, fingerprints, cache_hit FROM p2gis.jobs "
        "WHERE status IN ('queued','running')").fetchall()
    out = []
    for r in rows:
        out.append({
            'job_id': r['id'],
            'urls': _json_loads(r['urls'], []) or [],
            'config': _json_loads(r['config'], {}) or {},
            'fingerprints': _json_loads(r['fingerprints'], []) or [],
            'cache_hit': bool(r['cache_hit']),
        })
    return out


def mark_all_active_interrupted() -> list[dict]:
    """При старте сервера: все queued/running (прошлый процесс) -> interrupted.

    Возвращает данные прерванных задач для пере-очереди. Best-effort: ошибка
    БД не должна валить запуск сервера."""
    try:
        with connection() as conn:
            out = _collect_active(conn)
            if out:
                conn.execute(
                    "UPDATE p2gis.jobs SET status='interrupted', "
                    "error='Прервано рестартом сервиса', finished_at=now(), "
                    "updated_at=now() WHERE status IN ('queued','running')")
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning('[jobs] mark_all_active_interrupted: %s', e)
        return []


def mark_stale_running(older_than_minutes: int = _HEARTBEAT_STALE_MINUTES) -> int:
    """Фоновая защита: задачи без heartbeat дольше N минут -> interrupted."""
    cutoff = _now() - timedelta(minutes=max(5, older_than_minutes))
    try:
        with connection() as conn:
            cur = conn.execute(
                "UPDATE p2gis.jobs SET status='interrupted', "
                "error='Завис (нет heartbeat)', finished_at=now(), updated_at=now() "
                "WHERE status IN ('queued','running') AND last_heartbeat < %s",
                [cutoff])
            return cur.rowcount if cur else 0
    except Exception as e:  # noqa: BLE001
        logger.warning('[jobs] mark_stale_running: %s', e)
        return 0


def list_jobs(limit: int = 50) -> list[dict]:
    """Журнал задач для UI/диагностики."""
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT id, urls, status, error, started_at, finished_at, "
                "last_heartbeat, cache_hit FROM p2gis.jobs "
                "ORDER BY started_at DESC LIMIT %s", [max(1, limit)]).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['urls'] = _json_loads(r['urls'], []) or []
            out.append(d)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning('[jobs] list_jobs: %s', e)
        return []
