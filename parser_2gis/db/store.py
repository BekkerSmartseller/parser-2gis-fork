# ================================
# parser_2gis/db/store.py
# Запись результатов парсинга в p2gis.records (upsert по firm_id),
# чтение для UI/экспорта и кэш seen_firms на базе БД.
# ================================
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg.types.json import Jsonb

from ..logger import logger
from ..writer.models import CatalogItem
from ..writer.record import extract_record
from ..writer.writers import FileWriter
from .connection import connection

try:
    from ..parser.geocoder import _city_slug
except Exception:  # noqa: BLE001  (импорт без Chrome-цепочки, только транслит)
    _city_slug = None

# Сколько сырых документов держать в памяти для живого грида (остальное — в БД).
_WINDOW_SIZE = 2000
# Размер батча записи в БД.
_BATCH_SIZE = 500

_INSERT_SQL = """
INSERT INTO p2gis.records (
    firm_id, org_id, org_name, name, description, address, address_comment,
    city, city_code, district, district_area, region, country, postcode,
    lat, lon, phone, mobile, email, websites, socials, rubrics, rubric_ids,
    primary_rubric, rubric_section, sub_rubrics, rating, review_count,
    org_rating, org_review_count, average_check, schedule, schedule_comment,
    photos, url, reviews_url, branch_count, nearest_station, station_distance,
    search_text, raw_doc, last_job_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s)
ON CONFLICT (firm_id) DO UPDATE SET
    org_id=EXCLUDED.org_id, org_name=EXCLUDED.org_name, name=EXCLUDED.name,
    description=EXCLUDED.description, address=EXCLUDED.address,
    address_comment=EXCLUDED.address_comment, city=EXCLUDED.city,
    city_code=EXCLUDED.city_code, district=EXCLUDED.district,
    district_area=EXCLUDED.district_area, region=EXCLUDED.region,
    country=EXCLUDED.country, postcode=EXCLUDED.postcode,
    lat=EXCLUDED.lat, lon=EXCLUDED.lon, phone=EXCLUDED.phone,
    mobile=EXCLUDED.mobile, email=EXCLUDED.email, websites=EXCLUDED.websites,
    socials=EXCLUDED.socials, rubrics=EXCLUDED.rubrics,
    rubric_ids=EXCLUDED.rubric_ids, primary_rubric=EXCLUDED.primary_rubric,
    rubric_section=EXCLUDED.rubric_section, sub_rubrics=EXCLUDED.sub_rubrics,
    rating=EXCLUDED.rating, review_count=EXCLUDED.review_count,
    org_rating=EXCLUDED.org_rating, org_review_count=EXCLUDED.org_review_count,
    average_check=EXCLUDED.average_check, schedule=EXCLUDED.schedule,
    schedule_comment=EXCLUDED.schedule_comment, photos=EXCLUDED.photos,
    url=EXCLUDED.url, reviews_url=EXCLUDED.reviews_url,
    branch_count=EXCLUDED.branch_count, nearest_station=EXCLUDED.nearest_station,
    station_distance=EXCLUDED.station_distance, search_text=EXCLUDED.search_text,
    raw_doc=EXCLUDED.raw_doc,
    last_job_id=EXCLUDED.last_job_id, updated_at=now(), is_active=true
"""


def _city_code_of(rec: dict[str, Any]) -> str:
    city = (rec.get('city') or '').strip()
    if city:
        if _city_slug:
            return _city_slug(city)
        return city.lower()
    return ''


def _record_to_row(rec: dict[str, Any], doc: Any, job_id: Optional[str]) -> Optional[tuple]:
    """extract_record -> кортеж колонок p2gis.records."""
    firm_id = rec.get('firm_id')
    if not firm_id:
        return None
    contacts = rec.get('contacts') or {}
    socials = {k: v for k, v in contacts.items()
               if k not in ('phone', 'email', 'website', 'websites') and v}
    rubric_ids = [x.strip() for x in (rec.get('rubric_ids') or '').split(';') if x.strip()]

    # org_name из сырого документа (в extract_record его нет).
    org_name = None
    try:
        item = (doc or {}).get('result', {}).get('items', [])[0]
        org_name = (item.get('org') or {}).get('name')
    except (KeyError, IndexError, TypeError, AttributeError):
        pass

    schedule = rec.get('schedule')
    # search_text (pg_trgm): название + адрес + город + организация + рубрики.
    search_text = ' '.join(filter(None, [
        rec.get('name'), rec.get('address'), rec.get('city'), org_name,
        *list(rec.get('rubrics') or [])])).lower()
    return (
        firm_id, rec.get('org_id'), org_name, rec.get('name'), rec.get('description'),
        rec.get('address'), rec.get('address_comment'), rec.get('city'),
        _city_code_of(rec), rec.get('district'), rec.get('district_area'),
        rec.get('region'), rec.get('country'), rec.get('postcode'),
        rec.get('point_lat'), rec.get('point_lon'), contacts.get('phone'),
        rec.get('mobile'), contacts.get('email'),
        list(contacts.get('websites') or []),
        Jsonb(socials), list(rec.get('rubrics') or []), rubric_ids,
        rec.get('primary_rubric'), rec.get('rubric_section'), rec.get('sub_rubrics'),
        rec.get('rating'), rec.get('review_count'),
        rec.get('org_rating'), rec.get('org_review_count'), rec.get('average_check'),
        Jsonb(schedule) if schedule is not None else None, rec.get('schedule_comment'),
        list(rec.get('photos') or []), rec.get('url'), rec.get('reviews_url'),
        rec.get('branch_count'), rec.get('nearest_station'), rec.get('station_distance'),
        search_text, Jsonb(doc), job_id,
    )


def upsert_records(docs: list[Any], job_id: Optional[str] = None) -> int:
    """Батч-апсерт документов в p2gis.records. Возвращает число записанных."""
    rows = []
    for doc in docs:
        rec = extract_record(doc)
        if not rec:
            continue
        row = _record_to_row(rec, doc, job_id)
        if row:
            rows.append(row)
    if not rows:
        return 0
    try:
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(_INSERT_SQL, rows)
                _backfill_city_regions(conn, rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.error('[db] upsert_records: %s', e)
        raise


def _backfill_city_regions(conn, rows) -> None:
    """Добивает p2gis.cities.region из записей (город -> регион из адм. деления).

    Регион города не приходит из region/list (только из дерева availableParameters);
    основной надёжный источник — спарсенные записи. Обновляем только города
    с пустым регионом, идемпотентно."""
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for row in rows:
        city_code = row[8]   # индекс city_code в кортеже _record_to_row
        city_name = row[7] if isinstance(row[7], str) else None  # city (title)
        region = row[11] if isinstance(row[11], str) else None  # region
        if not city_code or not region:
            continue
        key = (str(city_code), region)
        if key in seen:
            continue
        seen.add(key)
        pairs.append((str(city_code), region, city_name or ''))
    if not pairs:
        return
    try:
        from psycopg.types.json import Jsonb  # noqa: F401  (общий импорт jsonb)
        for city_code, region, city_name in pairs:
            conn.execute(
                "UPDATE p2gis.cities SET region = %s, updated_at = now() "
                "WHERE code = %s AND (region IS NULL OR region = '')",
                [region, city_code])
            if city_name:
                conn.execute(
                    "UPDATE p2gis.cities SET region = %s, updated_at = now() "
                    "WHERE name = %s AND code = %s AND (region IS NULL OR region = '')",
                    [region, city_name, city_code])
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] backfill_city_regions: %s', e)


def _docs_from_rows(rows) -> list[Any]:
    return [r['raw_doc'] for r in rows if r and r.get('raw_doc')]


def records_by_job(job_id: str, limit: int = 20000) -> list[Any]:
    """Сырые документы, записанные конкретной задачей (для результатов/экспорта)."""
    if not job_id:
        return []
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT raw_doc FROM p2gis.records WHERE last_job_id = %s "
                "ORDER BY parsed_at LIMIT %s", [job_id, limit]).fetchall()
            return _docs_from_rows(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] records_by_job: %s', e)
        return []


def records_all(limit: int = 200000) -> list[Any]:
    """Все активные сырые документы (CLI-экспорт в БД-режиме)."""
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT raw_doc FROM p2gis.records WHERE is_active "
                "ORDER BY updated_at LIMIT %s", [limit]).fetchall()
            return _docs_from_rows(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] records_all: %s', e)
        return []


def records_for_fingerprint(fp: dict[str, Any], limit: int = 20000) -> list[Any]:
    """Сырые документы, соответствующие запросу (для cache-hit и db/search)."""
    try:
        with connection() as conn:
            if fp.get('rubric_id'):
                rows = conn.execute(
                    "SELECT raw_doc FROM p2gis.records WHERE is_active "
                    "AND city_code = %s AND rubric_ids @> ARRAY[%s]::text[] "
                    "ORDER BY updated_at DESC LIMIT %s",
                    [fp['city_code'], fp['rubric_id'], limit]).fetchall()
            else:
                q = (fp.get('query_text') or '').lower()
                rows = conn.execute(
                    "SELECT raw_doc FROM p2gis.records WHERE is_active "
                    "AND city_code = %s "
                    "AND (replace(search_text, '-', ' ') ILIKE '%%' || replace(%s, '-', ' ') || '%%' "
                    "     OR similarity(search_text, %s) > 0.2) "
                    "ORDER BY updated_at DESC LIMIT %s",
                    [fp['city_code'], q, q, limit]).fetchall()
            return _docs_from_rows(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] records_for_fingerprint: %s', e)
        return []


def load_seen_firm_ids(cutoff: datetime) -> set[str]:
    """firm_id из БД, обновлённые после `cutoff` (межзадачный дедуп в БД-режиме)."""
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT firm_id FROM p2gis.records WHERE is_active "
                "AND updated_at >= %s", [cutoff]).fetchall()
            return {r['firm_id'] for r in rows if r.get('firm_id')}
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] load_seen_firm_ids: %s', e)
        return set()


class DbCollector(FileWriter):
    """Writer для БД-режима: батчами в p2gis.records + окно для живого UI.

    Реализует интерфейс FileWriter.write/__enter__/__exit__, поэтому подставляется
    в ParseJob и может быть обёрнут FilterWriter. docs — ограниченное окно;
    полные результаты читаются из БД.
    """

    def __init__(self, writer_options, job_id: Optional[str] = None) -> None:
        super().__init__('', writer_options)
        self.job_id = job_id
        self._window: list[Any] = []
        self._batch: list[Any] = []
        self._total = 0
        self._lock = threading.Lock()
        self._fingerprints: list[dict[str, Any]] = []
        self._cache_hit = False

    @property
    def docs(self) -> list[Any]:
        with self._lock:
            return list(self._window)

    @property
    def count(self) -> int:
        with self._lock:
            return self._total

    def set_fingerprints(self, fingerprints: list[dict[str, Any]]) -> None:
        self._fingerprints = fingerprints

    def set_cache_hit(self, value: bool) -> None:
        self._cache_hit = value

    def _check_catalog_doc(self, catalog_doc: Any) -> bool:
        try:
            item = catalog_doc['result']['items'][0]
            CatalogItem(**item)
            return True
        except Exception:  # noqa: BLE001
            return False

    def write(self, catalog_doc: Any) -> None:
        if not self._check_catalog_doc(catalog_doc):
            return
        with self._lock:
            self._batch.append(catalog_doc)
            self._window.append(catalog_doc)
            if len(self._window) > _WINDOW_SIZE:
                del self._window[:len(self._window) - _WINDOW_SIZE]
            if len(self._batch) >= _BATCH_SIZE:
                batch = list(self._batch)
                self._batch.clear()
            else:
                batch = None
        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: list[Any]) -> None:
        if not batch:
            return
        try:
            n = upsert_records(batch, self.job_id)
        except Exception as e:  # noqa: BLE001
            logger.error('[db] не удалось записать батч в БД: %s', e)
            n = 0
        with self._lock:
            self._total += n

    def flush(self) -> None:
        with self._lock:
            batch = list(self._batch)
            self._batch.clear()
        self._flush_batch(batch)

    def load_cached(self) -> int:
        """Заполняет окно результатами из БД (cache-hit задача)."""
        if not self._fingerprints:
            return 0
        docs: list[Any] = []
        seen: set[str] = set()
        for fp in self._fingerprints:
            for d in records_for_fingerprint(fp):
                rec = extract_record(d)
                key = (rec or {}).get('url') or (rec or {}).get('firm_id')
                if key and key not in seen:
                    seen.add(key)
                    docs.append(d)
        with self._lock:
            self._window = docs[-_WINDOW_SIZE:]
            self._total = len(docs)
        return self._total

    def results(self) -> list[dict]:
        """Записи для грида: из БД (по задаче или fingerprint'ам), фолбэк — окно."""
        if self._cache_hit:
            return self._results_from_db(records_for_fingerprint(f) for f in self._fingerprints)
        docs = records_by_job(self.job_id) if self.job_id else []
        return self._results_from_db(iter([docs]))

    def all_docs(self) -> list[Any]:
        """Полные сырые документы (для экспорта/скачивания)."""
        if self._cache_hit:
            return list(_dedup_docs(d for f in self._fingerprints
                                    for d in records_for_fingerprint(f)))
        if self.job_id:
            return records_by_job(self.job_id)
        return records_all()

    @staticmethod
    def _results_from_db(iterables) -> list[dict]:
        out = []
        for chunk in iterables:
            for d in chunk:
                rec = extract_record(d)
                if rec:
                    out.append(rec)
        return out

    def __enter__(self) -> 'DbCollector':
        return self

    def __exit__(self, *exc_info) -> None:
        self.flush()


def _dedup_docs(iterable) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for d in iterable:
        rec = extract_record(d)
        key = (rec or {}).get('firm_id')
        if key is None or key not in seen:
            if key is not None:
                seen.add(key)
            out.append(d)
    return out
