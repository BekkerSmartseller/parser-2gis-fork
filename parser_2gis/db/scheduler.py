# ================================
# parser_2gis/db/scheduler.py
# Планировщик автообновления: расписания в p2gis.refresh_schedules,
# фоновый цикл (как refdata), cron через croniter. Запуск задач через
# JobManager с общим лимитом воркеров.
# ================================
from __future__ import annotations

import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..logger import logger
from .connection import connection, default_ttl_hours, enabled

_CHECK_INTERVAL_SECONDS = 60


# --- URL-строитель (как генератор ссылок веб-UI) ---

def _cities_map() -> dict[str, dict]:
    """code -> {name, domain, country_code} (БД-режим: из p2gis.cities)."""
    from ..web.refdata import load_cities_list
    return {c.get('code'): c for c in load_cities_list() if c.get('code')}


def _rubrics_map() -> dict[str, dict]:
    """code -> node (label и т.д.) (БД-режим: из p2gis.rubrics)."""
    from ..web.refdata import load_rubrics_dict
    return load_rubrics_dict()


def build_urls(cities: list[str], rubrics: list[str], queries: list[str]) -> list[str]:
    """URL'ы по городам × (рубрики|текстовые запросы), формат генератора ссылок."""
    cities_map = _cities_map()
    rubrics_map = _rubrics_map()
    urls: list[str] = []
    for code in cities or []:
        city = cities_map.get(code)
        base = ('https://2gis.' + city['domain'] + '/' + code) if city else 'https://2gis.ru/' + code
        if rubrics:
            for r in rubrics or []:
                label = (rubrics_map.get(r) or {}).get('label') or r
                urls.append(base + '/search/' + urllib.parse.quote(label)
                            + '/rubricId/' + r + '/filters/sort=name')
        for q in queries or []:
            if q and q.strip():
                urls.append(base + '/search/' + urllib.parse.quote(q.strip())
                            + '/filters/sort=name')
    return urls


# --- Расписания: CRUD ---

_SCHEDULE_COLUMNS = ("id, name, cron, interval_minutes, cities, rubrics, queries, "
                     "max_concurrent, ttl_hours, sync_after, enabled, last_run, next_run, "
                     "last_status, created_at, updated_at")


def _row_to_schedule(row, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cities = list(row.get('cities') or [])
    rubrics = list(row.get('rubrics') or [])
    queries = list(row.get('queries') or [])
    d = {'id': row.get('id'), 'name': row.get('name'), 'cron': row.get('cron'),
         'interval_minutes': row.get('interval_minutes'), 'cities': cities,
         'rubrics': rubrics, 'queries': queries,
         'max_concurrent': row.get('max_concurrent'), 'ttl_hours': row.get('ttl_hours'),
         'sync_after': row.get('sync_after'), 'enabled': row.get('enabled'),
         'last_run': row.get('last_run'), 'next_run': row.get('next_run'),
         'last_status': row.get('last_status'),
         'created_at': row.get('created_at'), 'updated_at': row.get('updated_at'),
         'urls': build_urls(cities, rubrics, queries)}
    d['due'] = _is_due(d, now)
    return d


def validate_cron(expr: Optional[str]) -> None:
    """Проверяет cron-выражение; бросает ValueError при некорректном."""
    if not expr:
        return
    try:
        from croniter import croniter
        croniter(expr, datetime.now())
    except Exception as e:  # noqa: BLE001
        raise ValueError('Некорректное cron-выражение: %s' % e)


def _compute_next(schedule: dict, now: Optional[datetime] = None) -> Optional[datetime]:
    now = now or datetime.now(timezone.utc)
    if schedule.get('cron'):
        try:
            from croniter import croniter
            return croniter(schedule['cron'], now).get_next(datetime)
        except Exception:  # noqa: BLE001
            return None
    interval = schedule.get('interval_minutes')
    if interval and interval > 0:
        last = schedule.get('last_run')
        base = last or now
        nxt = base + timedelta(minutes=interval)
        return nxt if nxt > now else now + timedelta(minutes=interval)
    return None


def _is_due(schedule: dict, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if not schedule.get('enabled'):
        return False
    if not schedule.get('cron') and not schedule.get('interval_minutes'):
        return False
    nxt = schedule.get('next_run')
    return nxt is None or nxt <= now


def list_schedules() -> list[dict]:
    if not enabled():
        return []
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT %s FROM p2gis.refresh_schedules ORDER BY id" % _SCHEDULE_COLUMNS).fetchall()
            return [_row_to_schedule(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] list_schedules: %s', e)
        return []


def get_schedule(schedule_id: int) -> Optional[dict]:
    try:
        with connection() as conn:
            row = conn.execute(
                "SELECT %s FROM p2gis.refresh_schedules WHERE id = %%s" % _SCHEDULE_COLUMNS,
                [schedule_id]).fetchone()
            return _row_to_schedule(row) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] get_schedule: %s', e)
        return None


def create_schedule(name: str, cron: Optional[str] = None, interval_minutes: Optional[int] = None,
                    cities: Optional[list[str]] = None, rubrics: Optional[list[str]] = None,
                    queries: Optional[list[str]] = None, max_concurrent: Optional[int] = None,
                    ttl_hours: Optional[int] = None, sync_after: bool = True,
                    enabled_flag: bool = True) -> dict:
    if not (name or '').strip():
        raise ValueError('Укажите название расписания')
    cities = list(cities or [])
    rubrics = list(rubrics or [])
    queries = [q for q in (queries or []) if q and q.strip()]
    if not cities:
        raise ValueError('Выберите хотя бы один город')
    if not rubrics and not queries:
        raise ValueError('Задайте хотя бы одну рубрику или запрос')
    validate_cron(cron)
    if not cron and not interval_minutes:
        raise ValueError('Задайте cron или interval_minutes')
    with connection() as conn:
        row = conn.execute(
            "INSERT INTO p2gis.refresh_schedules "
            "(name, cron, interval_minutes, cities, rubrics, queries, max_concurrent, "
            " ttl_hours, sync_after, enabled, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now()) RETURNING id",
            [name, cron, interval_minutes, cities, rubrics,
             queries, max_concurrent, ttl_hours, sync_after, enabled_flag]).fetchone()
    schedule_id = row['id']
    sched = get_schedule(schedule_id)
    if sched:
        _update_next(schedule_id, sched)
    return get_schedule(schedule_id) or sched


def update_schedule(schedule_id: int, **fields) -> dict:
    allowed = {'name', 'cron', 'interval_minutes', 'cities', 'rubrics', 'queries',
               'max_concurrent', 'ttl_hours', 'sync_after', 'enabled'}
    sets = []
    params: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == 'cron':
            validate_cron(v)
        if k in ('cities', 'rubrics', 'queries') and v is not None:
            v = list(v)
        sets.append('%s = %%s' % k)
        params.append(v)
    if sets:
        params.append(schedule_id)
        with connection() as conn:
            conn.execute(
                "UPDATE p2gis.refresh_schedules SET %s, updated_at=now() WHERE id=%%s"
                % ', '.join(sets), params)
    sched = get_schedule(schedule_id)
    if sched:
        _update_next(schedule_id, sched)
    return get_schedule(schedule_id) or sched


def delete_schedule(schedule_id: int) -> bool:
    with connection() as conn:
        cur = conn.execute("DELETE FROM p2gis.refresh_schedules WHERE id = %s", [schedule_id])
        return cur.rowcount > 0


def toggle_schedule(schedule_id: int) -> dict:
    sched = get_schedule(schedule_id)
    if not sched:
        raise LookupError('Расписание не найдено')
    return update_schedule(schedule_id, enabled=not sched['enabled'])


def _update_next(schedule_id: int, sched: dict) -> None:
    nxt = _compute_next(sched)
    with connection() as conn:
        conn.execute("UPDATE p2gis.refresh_schedules SET next_run = %s WHERE id = %s",
                     [nxt, schedule_id])


def mark_stale_running_schedules() -> int:
    """Расписания, застрявшие в last_status='running' (рестарт процесса),
    помечаются failed — иначе UI вечно показывает «выполняется»."""
    with connection() as conn:
        cur = conn.execute(
            "UPDATE p2gis.refresh_schedules SET last_status='failed', "
            "updated_at=now() WHERE last_status='running'")
        return cur.rowcount


# --- Фоновый цикл ---

class Scheduler:
    """Фоновый поток, запускающий due-расписания через JobManager."""

    def __init__(self, jobs) -> None:
        self._jobs = jobs
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='p2gis-scheduler')
        self._thread.start()
        logger.info('[scheduler] фоновый цикл запущен (каждые %dс)', _CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(_CHECK_INTERVAL_SECONDS):
            try:
                self.run_due()
            except Exception as e:  # noqa: BLE001
                logger.warning('[scheduler] цикл: %s', e)

    def _build_config(self, schedule: dict) -> Any:
        from ..config import Configuration
        cfg = Configuration()
        cfg.chrome.headless = True
        cfg.parser.storage = 'db'
        if schedule.get('max_concurrent'):
            cfg.parser.max_concurrent = max(1, int(schedule['max_concurrent']))
        cfg.parser.cache_ttl_hours = schedule.get('ttl_hours') or default_ttl_hours()
        cfg.parser.sync_after = bool(schedule.get('sync_after', True))
        return cfg

    def run_schedule(self, schedule_id: int) -> dict:
        """Запускает расписание немедленно. Возвращает job_id (или None)."""
        schedule = get_schedule(schedule_id)
        if not schedule:
            raise LookupError('Расписание не найдено')
        urls = schedule['urls']
        if not urls:
            raise RuntimeError('У расписания нет городов/рубрик/запросов')
        with self._lock:
            job_id = self._jobs.start(self._build_config(schedule), urls)
            self._mark_started(schedule_id, schedule)
        logger.info('[scheduler] расписание #%d «%s» запущено (%d URL), job=%s',
                    schedule_id, schedule['name'], len(urls), job_id)
        return {'job_id': job_id, 'urls': len(urls)}

    def run_due(self) -> int:
        """Запускает все due-расписания. Возвращает число запущенных."""
        now = datetime.now(timezone.utc)
        started = 0
        for schedule in list_schedules():
            if not _is_due(schedule, now):
                continue
            try:
                self.run_schedule(schedule['id'])
                started += 1
            except Exception as e:  # noqa: BLE001
                logger.warning('[scheduler] расписание #%s: %s', schedule['id'], e)
        return started

    @staticmethod
    def _mark_started(schedule_id: int, schedule: dict) -> None:
        nxt = _compute_next(schedule)
        with connection() as conn:
            conn.execute(
                "UPDATE p2gis.refresh_schedules SET last_run=now(), next_run=%s, "
                "last_status='running', updated_at=now() WHERE id=%s", [nxt, schedule_id])
