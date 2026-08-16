# ================================
# parser_2gis/db/cache.py
# Кэш запросов: fingerprint (город + рубрика | нормализованные токены),
# свежесть по TTL, журнал parse_requests (гипертаблица) и аналитика.
# ================================
from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

from ..logger import logger
from .connection import connection, default_ttl_hours

_CITY_RE = re.compile(r'^https?://2gis\.[^/]+/([^/]+)/(?:search|branches|firm|inside)/')
_RUBRIC_RE = re.compile(r'/rubricId/(\d+)')
_QUERY_RE = re.compile(r'/search/([^/?#]+)')


def normalize_query(query: Optional[str]) -> str:
    """«Фитнес-клуб Москва» -> 'клуб москва фитнес' (lowercase, ё->е, сорт токенов)."""
    s = (query or '').lower().replace('ё', 'е')
    s = re.sub(r'[^a-zа-я0-9]+', ' ', s)
    tokens = [t for t in s.split() if len(t) >= 2]
    tokens.sort()
    return ' '.join(tokens)


def fingerprint_for_url(url: str) -> Optional[dict[str, Any]]:
    """Разбирает URL поиска 2GIS в fingerprint-запись.

    Returns:
        {'fingerprint', 'city_code', 'rubric_id', 'query_text', 'url'} или None,
        если кэширование неприменимо: нет городского префикса (координатные
        `?m=` и голые `/search/` запросы охватывают регион/точку — они всегда
        парсятся заново), firm/branches, пустой запрос.
    """
    m_city = _CITY_RE.match(url or '')
    city = m_city.group(1) if m_city else ''
    # Без города кэш не строим: записи в БД имеют реальный city_code, а ключ без
    # города смешивал бы разные пространственные точки/регионы.
    if not city:
        return None
    m_rubric = _RUBRIC_RE.search(url or '')
    rubric_id = m_rubric.group(1) if m_rubric else None
    m_q = _QUERY_RE.search(url or '')
    query_text = urllib.parse.unquote(m_q.group(1)) if m_q else ''

    if rubric_id:
        fingerprint = f'{city}|r:{rubric_id}'
    else:
        q = normalize_query(query_text)
        if not q:
            return None
        fingerprint = f'{city}|q:{q}'

    return {
        'fingerprint': fingerprint,
        'city_code': city,
        'rubric_id': rubric_id,
        'query_text': query_text or rubric_id or '',
        'url': (url or '').split('?')[0],
    }


def _row_to_fp(row) -> dict[str, Any]:
    """Строка request_cache -> dict для API."""
    return {
        'fingerprint': row['fingerprint'],
        'city_code': row['city_code'],
        'rubric_id': row['rubric_id'],
        'query_text': row['query_text'],
        'url': row['url'],
        'last_parsed_at': row['last_parsed_at'],
        'records_found': row['records_found'],
        'status': row['status'],
        'error': row['error'],
    }


def _freshness(row, ttl_hours: int) -> dict[str, Any]:
    d = _row_to_fp(row)
    if row['last_parsed_at'] is None:
        d['fresh'] = False
        d['age_hours'] = None
    else:
        age_h = (datetime.now(timezone.utc) - row['last_parsed_at']).total_seconds() / 3600
        d['fresh'] = age_h < ttl_hours
        d['age_hours'] = round(age_h, 1)
    return d


def request_status(fingerprints: list[str], ttl_hours: Optional[int] = None) -> dict[str, dict]:
    """Статус свежести списка fingerprint'ов. Сбой БД трактуется как «промах» (не свежий)."""
    if not fingerprints:
        return {}
    ttl = ttl_hours or default_ttl_hours()
    out: dict[str, dict] = {}
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT fingerprint, city_code, rubric_id, query_text, url, "
                "last_parsed_at, records_found, status, error "
                "FROM p2gis.request_cache WHERE fingerprint = ANY(%s)",
                [fingerprints]).fetchall()
            for row in rows:
                out[row['fingerprint']] = _freshness(row, ttl)
        for fp in fingerprints:
            out.setdefault(fp, {'fresh': False, 'last_parsed_at': None,
                                'records_found': None, 'status': 'unknown',
                                'query_text': '', 'rubric_id': None,
                                'city_code': '', 'url': '', 'error': None,
                                'age_hours': None})
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] ошибка чтения request_cache: %s', e)
        for fp in fingerprints:
            out.setdefault(fp, {'fresh': False, 'status': 'unknown', 'error': str(e)})
    return out


def count_records(fingerprint: dict[str, Any]) -> int:
    """Сколько записей в p2gis.records соответствует запросу."""
    try:
        with connection() as conn:
            if fingerprint.get('rubric_id'):
                return conn.execute(
                    "SELECT count(*) FROM p2gis.records WHERE is_active "
                    "AND city_code = %s AND rubric_ids @> ARRAY[%s]::text[]",
                    [fingerprint['city_code'], fingerprint['rubric_id']]).fetchone()['count']
            q = (fingerprint.get('query_text') or '').lower()
            return conn.execute(
                "SELECT count(*) FROM p2gis.records WHERE is_active "
                "AND city_code = %s "
                "AND (replace(search_text, '-', ' ') ILIKE '%%' || replace(%s, '-', ' ') || '%%' "
                "     OR similarity(search_text, %s) > 0.2)",
                [fingerprint['city_code'], q, q]).fetchone()['count']
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] count_records: %s', e)
        return 0


def record_job(job_id: str, urls: list[str], status: str,
               cache_hit: bool = False,
               ttl_hours: Optional[int] = None,
               started_at: Optional[datetime] = None) -> None:
    """По завершении задачи: обновляет request_cache (если парсили) и пишет журнал.

    Для cache-hit записей last_parsed_at НЕ обновляется (иначе TTL никогда не сработает).
    """
    if not urls:
        return
    ttl = ttl_hours or default_ttl_hours()
    start = started_at or datetime.now(timezone.utc)
    finished = datetime.now(timezone.utc)
    fingerprints = [fingerprint_for_url(u) for u in urls]
    fingerprints = [f for f in fingerprints if f]
    try:
        with connection() as conn:
            with conn.transaction():
                for fp in fingerprints:
                    n = count_records(fp)
                    if cache_hit:
                        conn.execute(
                            "INSERT INTO p2gis.parse_requests "
                            "(started_at, finished_at, job_id, fingerprint, url, city_code, "
                            "rubric_id, query_text, cache_hit, status, records_found) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)",
                            [start, finished, job_id, fp['fingerprint'], fp['url'],
                             fp['city_code'], fp['rubric_id'], fp['query_text'],
                             status, n])
                    else:
                        conn.execute(
                            "INSERT INTO p2gis.request_cache "
                            "(fingerprint, city_code, rubric_id, query_text, url, "
                            "last_parsed_at, last_job_id, records_found, status, updated_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
                            "ON CONFLICT (fingerprint) DO UPDATE SET "
                            "city_code=EXCLUDED.city_code, rubric_id=EXCLUDED.rubric_id, "
                            "query_text=EXCLUDED.query_text, url=EXCLUDED.url, "
                            "last_parsed_at=EXCLUDED.last_parsed_at, last_job_id=EXCLUDED.last_job_id, "
                            "records_found=EXCLUDED.records_found, status=EXCLUDED.status, "
                            "error=NULL, updated_at=now()",
                            [fp['fingerprint'], fp['city_code'], fp['rubric_id'],
                             fp['query_text'], fp['url'], finished, job_id, n, status])
                        conn.execute(
                            "INSERT INTO p2gis.parse_requests "
                            "(started_at, finished_at, job_id, fingerprint, url, city_code, "
                            "rubric_id, query_text, cache_hit, status, records_found) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s)",
                            [start, finished, job_id, fp['fingerprint'], fp['url'],
                             fp['city_code'], fp['rubric_id'], fp['query_text'],
                             status, n])
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] record_job: %s', e)


def mark_backfilled(urls: list[str], parsed_at: datetime) -> int:
    """Помечает запросы из файловой истории как спарсенные в момент `parsed_at`."""
    n = 0
    for url in urls:
        fp = fingerprint_for_url(url)
        if not fp:
            continue
        try:
            with connection() as conn:
                conn.execute(
                    "INSERT INTO p2gis.request_cache "
                    "(fingerprint, city_code, rubric_id, query_text, url, "
                    "last_parsed_at, records_found, status, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, 'ok', now()) "
                    "ON CONFLICT (fingerprint) DO UPDATE SET "
                    "city_code=EXCLUDED.city_code, rubric_id=EXCLUDED.rubric_id, "
                    "query_text=EXCLUDED.query_text, url=EXCLUDED.url, "
                    "last_parsed_at=EXCLUDED.last_parsed_at, updated_at=now()",
                    [fp['fingerprint'], fp['city_code'], fp['rubric_id'],
                     fp['query_text'], fp['url'], parsed_at, 0])
                conn.execute(
                    "INSERT INTO p2gis.parse_requests "
                    "(started_at, finished_at, job_id, fingerprint, url, city_code, "
                    "rubric_id, query_text, cache_hit, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, 'ok')",
                    [parsed_at, parsed_at, 'backfill', fp['fingerprint'], fp['url'],
                     fp['city_code'], fp['rubric_id'], fp['query_text']])
                n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning('[db] mark_backfilled: %s', e)
    return n


def cache_rows(ttl_hours: Optional[int] = None) -> list[dict]:
    """Все записи request_cache со свежестью (для вкладки «DB»)."""
    ttl = ttl_hours or default_ttl_hours()
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT fingerprint, city_code, rubric_id, query_text, url, "
                "last_parsed_at, records_found, status, error "
                "FROM p2gis.request_cache ORDER BY last_parsed_at DESC NULLS LAST").fetchall()
            return [_freshness(r, ttl) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] cache_rows: %s', e)
        return []


def stale_fingerprints(ttl_hours: Optional[int] = None) -> list[dict]:
    """Fingerprint'ы, у которых кэш протух или отсутствует (для refresh-stale)."""
    ttl = ttl_hours or default_ttl_hours()
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT fingerprint, city_code, rubric_id, query_text, url, "
                "last_parsed_at, records_found, status, error "
                "FROM p2gis.request_cache "
                "WHERE last_parsed_at IS NULL OR last_parsed_at < now() - make_interval(hours => %s)",
                [ttl]).fetchall()
            return [_row_to_fp(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] stale_fingerprints: %s', e)
        return []


def coverage() -> list[dict]:
    """Покрытие по городу×рубрике: записей, последнее обновление, свежесть."""
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT city, city_code, unnest(rubrics) AS rubric, count(*) AS records, "
                "max(updated_at) AS last_updated "
                "FROM p2gis.records WHERE is_active "
                "GROUP BY 1, 2, 3 ORDER BY last_updated ASC").fetchall()
            ttl = default_ttl_hours()
            out = []
            for r in rows:
                item = {'city': r['city'], 'city_code': r['city_code'],
                        'rubric': r['rubric'], 'records': r['records'],
                        'last_updated': r['last_updated']}
                if r['last_updated'] is not None:
                    age_h = (datetime.now(timezone.utc) - r['last_updated']).total_seconds() / 3600
                    item['fresh'] = age_h < ttl
                    item['age_hours'] = round(age_h, 1)
                else:
                    item['fresh'] = False
                    item['age_hours'] = None
                out.append(item)
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] coverage: %s', e)
        return []
