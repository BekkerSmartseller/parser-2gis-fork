# ================================
# parser_2gis/db/sync.py
# Синхронизация p2gis -> medexpertai (единая база организаций health_ai).
# Орг-гранулярный инкремент: org + все его филиалы, upsert по firm_id,
# деактивация филиалов сети, отсутствующих в свежем наборе. Курсор —
# p2gis.sync_state.last_synced_at. Идемпотентно.
# ================================
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from psycopg.types.json import Jsonb

from ..logger import logger
from .connection import connection, enabled

_DEFAULT_BATCH = 20000

_ORG_INSERT_SQL = """
INSERT INTO medexpertai.organizations
    (primary_name, synonyms, brand, gis_org_ids, source, status,
     created_at, updated_at, is_partner)
VALUES (%s, %s, %s, %s, '2gis', 'active', now(), now(), false)
RETURNING id
"""

_ORG_APPEND_GIS_ID_SQL = """
UPDATE medexpertai.organizations
SET gis_org_ids = array_append(gis_org_ids, %s), updated_at = now()
WHERE id = %s AND NOT (gis_org_ids @> ARRAY[%s]::text[])
"""

_BRANCH_SELECT_FIRMS_SQL = """
SELECT firm_id, id FROM medexpertai.organization_branches
WHERE organization_id = %s
"""

_BRANCH_INSERT_SQL = """
INSERT INTO medexpertai.organization_branches
    (organization_id, firm_id, gis_org_id, name, description, address,
     address_comment, city, district, region, country, postcode, lat, lon,
     phone, mobile, website, websites, socials, rubrics, photos, schedule,
     schedule_comment, url, reviews_url, nearest_station, station_distance,
     average_check, rating, review_count, raw_data, status,
     created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, 'active', now(), now())
ON CONFLICT (firm_id) WHERE firm_id IS NOT NULL DO UPDATE SET
    organization_id=EXCLUDED.organization_id,
    gis_org_id=EXCLUDED.gis_org_id, name=EXCLUDED.name,
    description=EXCLUDED.description, address=EXCLUDED.address,
    address_comment=EXCLUDED.address_comment, city=EXCLUDED.city,
    district=EXCLUDED.district, region=EXCLUDED.region,
    country=EXCLUDED.country, postcode=EXCLUDED.postcode,
    lat=EXCLUDED.lat, lon=EXCLUDED.lon, phone=EXCLUDED.phone,
    mobile=EXCLUDED.mobile, website=EXCLUDED.website,
    websites=EXCLUDED.websites, socials=EXCLUDED.socials,
    rubrics=EXCLUDED.rubrics, photos=EXCLUDED.photos,
    schedule=EXCLUDED.schedule, schedule_comment=EXCLUDED.schedule_comment,
    url=EXCLUDED.url, reviews_url=EXCLUDED.reviews_url,
    nearest_station=EXCLUDED.nearest_station,
    station_distance=EXCLUDED.station_distance,
    average_check=EXCLUDED.average_check, rating=EXCLUDED.rating,
    review_count=EXCLUDED.review_count, raw_data=EXCLUDED.raw_data,
    status='active', updated_at=now()
"""

_BRANCH_UPDATE_SQL = """
UPDATE medexpertai.organization_branches SET
    gis_org_id=%s, name=%s, description=%s, address=%s, address_comment=%s,
    city=%s, district=%s, region=%s, country=%s, postcode=%s, lat=%s, lon=%s,
    phone=%s, mobile=%s, website=%s, websites=%s, socials=%s, rubrics=%s,
    photos=%s, schedule=%s, schedule_comment=%s, url=%s, reviews_url=%s,
    nearest_station=%s, station_distance=%s, average_check=%s, rating=%s,
    review_count=%s, raw_data=%s, status='active', updated_at=now()
WHERE id = %s
"""

_BRANCH_DEACTIVATE_SQL = """
UPDATE medexpertai.organization_branches SET status='inactive', updated_at=now()
WHERE organization_id = %s AND status != 'inactive'
  AND (firm_id IS NULL OR NOT (firm_id = ANY(%s::text[])))
"""

# Линк рубрик филиала на категории health_ai: p2gis.records.rubric_ids — это
# коды medexpertai.categories (числовые ID рубрик 2GIS).
_BRANCH_CATEGORIES_LINK_SQL = """
INSERT INTO medexpertai.branch_categories (branch_id, category_id, kind)
SELECT b.id, c.id, 'primary'
FROM medexpertai.organization_branches b
JOIN p2gis.records r ON r.firm_id = b.firm_id AND r.is_active
JOIN medexpertai.categories c ON c.code = ANY(r.rubric_ids)
WHERE b.organization_id = %s AND b.firm_id = ANY(%s::text[]) AND b.status = 'active'
ON CONFLICT (branch_id, category_id) DO NOTHING
"""


def _row_to_branch(row) -> dict[str, Any]:
    """Строка p2gis.records -> значения филиала medexpertai."""
    websites = list(row['websites'] or [])
    return {
        'firm_id': row['firm_id'],
        'gis_org_id': row['org_id'],
        'name': row['name'],
        'description': row['description'],
        'address': row['address'],
        'address_comment': row['address_comment'],
        'city': row['city'],
        'district': row['district'],
        'region': row['region'],
        'country': row['country'],
        'postcode': row['postcode'],
        'lat': row['lat'],
        'lon': row['lon'],
        'phone': row['phone'],
        'mobile': row['mobile'],
        'website': websites[0] if websites else None,
        'websites': websites,
        'socials': Jsonb(row['socials'] or {}),
        'rubrics': list(row['rubrics'] or []),
        'photos': list(row['photos'] or []),
        'schedule': Jsonb(row['schedule']) if row['schedule'] is not None else None,
        'schedule_comment': row['schedule_comment'],
        'url': row['url'],
        'reviews_url': row['reviews_url'],
        'nearest_station': row['nearest_station'],
        'station_distance': row['station_distance'],
        'average_check': row['average_check'],
        'rating': row['rating'],
        'review_count': row['review_count'],
        'raw_data': Jsonb(row['raw_doc']),
    }


_RECORD_COLUMNS = ("firm_id, org_id, org_name, name, description, address, "
                   "address_comment, city, district, region, country, postcode, lat, lon, "
                   "phone, mobile, websites, socials, rubrics, photos, schedule, "
                   "schedule_comment, url, reviews_url, nearest_station, station_distance, "
                   "average_check, rating, review_count, raw_doc, updated_at")


def _fetch_records(since: Optional[datetime], limit: int,
                   city: Optional[str], rubric_id: Optional[str]) -> list[dict]:
    where = ['is_active']
    params: list[Any] = []
    if since is not None:
        where.append('updated_at > %s')
        params.append(since)
    if city and city.strip():
        where.append('(city_code = %s OR city ILIKE %s)')
        params += [city.strip(), '%' + city.strip() + '%']
    if rubric_id:
        where.append('rubric_ids @> ARRAY[%s]::text[]')
        params.append(rubric_id)
    sql = ("SELECT {} FROM p2gis.records WHERE {} ORDER BY org_id, firm_id LIMIT %s"
           .format(_RECORD_COLUMNS, ' AND '.join(where)))
    return _run_fetch(sql, params + [limit])


def _run_fetch(sql: str, params: list[Any]) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _upsert_org(conn, org_id: Optional[str], org_name: Optional[str],
                first_name: Optional[str]) -> Optional[int]:
    """Возвращает pk organizations; создаёт организацию, если нет."""
    if not org_id:
        return None
    row = conn.execute(
        "SELECT id FROM medexpertai.organizations "
        "WHERE gis_org_ids @> ARRAY[%s]::text[] LIMIT 1", [org_id]).fetchone()
    if row:
        pk = row['id']
        conn.execute(_ORG_APPEND_GIS_ID_SQL, [org_id, pk, org_id])
        return pk
    name = (org_name or first_name or org_id).strip() or org_id
    pk = conn.execute(
        _ORG_INSERT_SQL, [name, [first_name or name], name, [org_id]]).fetchone()['id']
    return pk


def _sync_org(conn, org_id: Optional[str], rows: list[dict]) -> dict[str, int]:
    stats = {'branches_upserted': 0, 'branches_deactivated': 0, 'categories_linked': 0}
    org_name = rows[0].get('org_name')
    first_name = rows[0].get('name')
    org_pk = _upsert_org(conn, org_id, org_name, first_name)
    if org_pk is None:
        return stats

    existing = {r['firm_id']: r['id'] for r in conn.execute(
        _BRANCH_SELECT_FIRMS_SQL, [org_pk]).fetchall() if r.get('firm_id')}
    firm_ids = [r['firm_id'] for r in rows if r.get('firm_id')]
    to_insert = []
    to_update = []
    for row in rows:
        b = _row_to_branch(row)
        branch_pk = existing.get(b['firm_id'])
        if branch_pk is not None:
            to_update.append((_branch_update_params(b), branch_pk))
        else:
            to_insert.append((org_pk, b))

    with conn.cursor() as cur:
        for params, pk in to_update:
            cur.execute(_BRANCH_UPDATE_SQL, params + [pk])
        for org_pk_i, b in to_insert:
            cur.execute(_BRANCH_INSERT_SQL, _branch_insert_params(org_pk_i, b))
    stats['branches_upserted'] = len(to_insert) + len(to_update)

    if firm_ids:
        cur = conn.cursor()
        cur.execute(_BRANCH_DEACTIVATE_SQL, [org_pk, firm_ids])
        stats['branches_deactivated'] = cur.rowcount
        # Связываем рубрики -> категории health_ai (по коду, ON CONFLICT DO NOTHING).
        try:
            cur.execute(_BRANCH_CATEGORIES_LINK_SQL, [org_pk, firm_ids])
            stats['categories_linked'] = cur.rowcount
        except Exception as e:  # noqa: BLE001
            logger.warning('[sync] branch_categories: %s', e)
    # Добиваем регион города в p2gis.cities (надёжный источник — спарсенные записи).
    try:
        _backfill_city_regions(conn, rows)
    except Exception as e:  # noqa: BLE001
        logger.warning('[sync] backfill_city_regions: %s', e)
    return stats


def _backfill_city_regions(conn, rows: list[dict]) -> None:
    """Проставляет p2gis.cities.region из записей (город -> регион адм. деления).

    Регион города не приходит из region/list; основной источник — записи
    (p2gis.records.region). Обновляем только города с пустым регионом."""
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for r in rows:
        city_code = (r.get('city_code') or '').strip()
        region = (r.get('region') or '').strip()
        city_name = (r.get('city') or '').strip()
        if not city_code or not region:
            continue
        key = (city_code, region)
        if key in seen:
            continue
        seen.add(key)
        pairs.append((city_code, region, city_name))
    if not pairs:
        return
    try:
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
        logger.warning('[sync] backfill_city_regions: %s', e)


def _branch_insert_params(org_pk: int, b: dict) -> list[Any]:
    return [org_pk, b['firm_id'], b['gis_org_id'], b['name'], b['description'],
            b['address'], b['address_comment'], b['city'], b['district'], b['region'],
            b['country'], b['postcode'], b['lat'], b['lon'], b['phone'], b['mobile'],
            b['website'], b['websites'], b['socials'], b['rubrics'], b['photos'],
            b['schedule'], b['schedule_comment'], b['url'], b['reviews_url'],
            b['nearest_station'], b['station_distance'], b['average_check'],
            b['rating'], b['review_count'], b['raw_data']]


def _branch_update_params(b: dict) -> list[Any]:
    return [b['gis_org_id'], b['name'], b['description'], b['address'],
            b['address_comment'], b['city'], b['district'], b['region'], b['country'],
            b['postcode'], b['lat'], b['lon'], b['phone'], b['mobile'], b['website'],
            b['websites'], b['socials'], b['rubrics'], b['photos'], b['schedule'],
            b['schedule_comment'], b['url'], b['reviews_url'], b['nearest_station'],
            b['station_distance'], b['average_check'], b['rating'], b['review_count'],
            b['raw_data']]


def sync_status() -> dict[str, Any]:
    """Курсор и последняя ошибка синхронизации."""
    if not enabled():
        return {'enabled': False, 'last_synced_at': None, 'last_error': None}
    try:
        with connection() as conn:
            row = conn.execute(
                "SELECT last_synced_at, last_error FROM p2gis.sync_state WHERE id = 1").fetchone()
        return {'enabled': True,
                'last_synced_at': row['last_synced_at'] if row else None,
                'last_error': row['last_error'] if row else None}
    except Exception as e:  # noqa: BLE001
        return {'enabled': True, 'last_synced_at': None, 'last_error': str(e)}


def sync_to_medexpertai(since: Optional[datetime] = None, limit: int = _DEFAULT_BATCH,
                        city: Optional[str] = None,
                        rubric_id: Optional[str] = None,
                        deactivate: bool = True) -> dict[str, Any]:
    """Синхронизирует p2gis.records в medexpertai (org + филиалы).

    Без `since` — берётся курсор из p2gis.sync_state. Один вызов обрабатывает
    до `limit` записей (все их организации). Идемпотентен.
    """
    if not enabled():
        raise RuntimeError('БД не настроена (задайте P2GIS_DB_URL)')
    if since is None:
        st = sync_status()
        since = st.get('last_synced_at')

    rows = _fetch_records(since, limit, city, rubric_id)
    if not rows:
        return {'synced_orgs': 0, 'branches_upserted': 0, 'branches_deactivated': 0,
                'records': 0, 'cursor': None, 'deactivated': deactivate}

    # Группировка по org_id.
    orgs: dict[str, list[dict]] = {}
    for r in rows:
        orgs.setdefault(r.get('org_id') or '', []).append(r)

    stats = {'orgs': 0, 'branches_upserted': 0, 'branches_deactivated': 0,
             'categories_linked': 0}
    last_updated: Optional[datetime] = None
    try:
        with connection() as conn:
            with conn.transaction():
                for org_id, branch_rows in orgs.items():
                    if not org_id:
                        continue
                    s = _sync_org(conn, org_id, branch_rows)
                    stats['orgs'] += 1
                    stats['branches_upserted'] += s['branches_upserted']
                    stats['branches_deactivated'] += s['branches_deactivated']
                    stats['categories_linked'] += s['categories_linked']
                    for r in branch_rows:
                        if last_updated is None or (r['updated_at'] and r['updated_at'] > last_updated):
                            last_updated = r['updated_at']
                if last_updated is not None:
                    conn.execute(
                        "INSERT INTO p2gis.sync_state (id, last_synced_at, last_error, updated_at) "
                        "VALUES (1, %s, NULL, now()) "
                        "ON CONFLICT (id) DO UPDATE SET last_synced_at=EXCLUDED.last_synced_at, "
                        "last_error=NULL, updated_at=now()", [last_updated])
    except Exception as e:  # noqa: BLE001
        logger.error('[sync] ошибка синхронизации: %s', e)
        try:
            with connection() as conn:
                conn.execute(
                    "UPDATE p2gis.sync_state SET last_error=%s, updated_at=now() WHERE id=1",
                    [str(e)])
        except Exception:  # noqa: BLE001
            pass
        raise

    logger.info('[sync] синхронизировано: org=%d, филиалы=%d, деактивировано=%d, категории=%d',
                stats['orgs'], stats['branches_upserted'], stats['branches_deactivated'],
                stats['categories_linked'])
    return {'synced_orgs': stats['orgs'],
            'branches_upserted': stats['branches_upserted'],
            'branches_deactivated': stats['branches_deactivated'],
            'categories_linked': stats['categories_linked'],
            'records': len(rows), 'cursor': last_updated.isoformat() if last_updated else None,
            'deactivated': deactivate}
