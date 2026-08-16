# ================================
# parser_2gis/web/refdata.py
# Автообновление справочников 2GIS (cities.json, rubrics.json).
#
# Справочники обновляются из data.2gis.com через Chrome (перехват API
# region/list и availableParameters), как в scripts/update_*.py, но прямо во
# время работы сервера: при запуске, по расписанию (раз в сутки) и по
# POST /api/refresh. Обновлённые файлы пишутся в user_path(False)/refdata/,
# а загрузчики предпочитают свежую копию из user dir перед файлом пакета.
# ================================
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..logger import logger
from ..paths import data_path, user_path

# --- Настройки (env) ---
_CFG_REFRESH = 'P2GIS_REFDATA_REFRESH'          # '0' — отключить автообновление
_CFG_INTERVAL_HOURS = 'P2GIS_REFDATA_INTERVAL_HOURS'  # сколько «свежо» (по умолчанию 24)
_CFG_CHECK_MINUTES = 'P2GIS_REFDATA_CHECK_MINUTES'    # как часто проверять расписание (60)

_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH = 'last_refresh.json'


def _refdata_dir() -> Path:
    d = user_path(False) / 'refdata'
    d.mkdir(parents=True, exist_ok=True)
    return d


def cities_file() -> Path:
    """Путь к cities.json: свежая копия из user dir, иначе файл пакета."""
    user_copy = _refdata_dir() / 'cities.json'
    return user_copy if user_copy.is_file() else data_path() / 'cities.json'


def rubrics_file() -> Path:
    """Путь к rubrics.json: свежая копия из user dir, иначе файл пакета."""
    user_copy = _refdata_dir() / 'rubrics.json'
    return user_copy if user_copy.is_file() else data_path() / 'rubrics.json'


def last_refresh_time() -> Optional[datetime]:
    """Время последнего успешного обновления (по маркеру)."""
    try:
        marker = _refdata_dir() / _LAST_REFRESH
        if marker.is_file():
            data = json.loads(marker.read_text(encoding='utf-8'))
            return datetime.fromisoformat(str(data['updated_at']))
    except Exception:  # noqa: BLE001
        pass
    return None


def is_fresh(interval_hours: float = 24.0) -> bool:
    """Справочники обновлены и не старше `interval_hours`."""
    last = last_refresh_time()
    if last is None:
        return False
    age_h = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() / 3600
    return age_h < max(0.1, interval_hours)


def _write_atomic(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=4), encoding='utf-8')
    tmp.replace(path)


def _write_marker() -> None:
    _write_atomic(_refdata_dir() / _LAST_REFRESH,
                  {'updated_at': datetime.now(timezone.utc).isoformat()})


# --- БД-слой справочников (канонический в БД-режиме; файлы — зеркало/фолбэк) ---
# Импорты ..db — только внутри функций, чтобы не создавать цикл: db-модули
# не импортируют web.refdata на уровне модуля.

def save_cities_db(cities: list[dict[str, Any]], source: str = '2gis') -> int:
    """Upsert городов в p2gis.cities. Возвращает число записанных (0 при недоступной БД)."""
    try:
        from ..db.connection import connection, enabled
        if not enabled():
            return 0
        rows = [(c.get('code'), c.get('name'), c.get('domain', 'ru'),
                 c.get('country_code', 'ru'), c.get('region'), source)
                for c in cities if c.get('code') and c.get('name')]
        if not rows:
            return 0
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO p2gis.cities (code, name, domain, country_code, region, source, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, now()) "
                        "ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, "
                        "domain=EXCLUDED.domain, country_code=EXCLUDED.country_code, "
                        "region=COALESCE(EXCLUDED.region, p2gis.cities.region), "
                        "source=EXCLUDED.source, updated_at=now()", rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning('[refdata] save_cities_db: %s', e)
        return 0


def save_rubrics_db(rubrics: dict[str, dict]) -> int:
    """Upsert рубрикатора в p2gis.rubrics. Возвращает число записанных."""
    try:
        from psycopg.types.json import Jsonb
        from ..db.connection import connection, enabled
        if not enabled():
            return 0
        rows = []
        for code, node in (rubrics or {}).items():
            if not code:
                continue
            rows.append((str(code), str(node.get('label') or ''),
                         str(node.get('parentCode') or '0'), Jsonb(node)))
        if not rows:
            return 0
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO p2gis.rubrics (code, label, parent_code, node, updated_at) "
                        "VALUES (%s, %s, %s, %s, now()) "
                        "ON CONFLICT (code) DO UPDATE SET label=EXCLUDED.label, "
                        "parent_code=EXCLUDED.parent_code, node=EXCLUDED.node, updated_at=now()",
                        rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning('[refdata] save_rubrics_db: %s', e)
        return 0


def load_cities_list() -> list[dict[str, Any]]:
    """Города: из БД (БД-режим), иначе/при ошибке — из файла (как раньше)."""
    try:
        from ..db.connection import connection, enabled
        if enabled():
            with connection() as conn:
                rows = conn.execute(
                    "SELECT code, name, domain, country_code, region FROM p2gis.cities "
                    "ORDER BY domain, name").fetchall()
                if rows:
                    return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning('[refdata] load_cities_list (БД): %s', e)
    try:
        with open(cities_file(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def load_rubrics_dict() -> dict[str, dict]:
    """Рубрикатор {code: node}: из БД (БД-режим), иначе/при ошибке — из файла."""
    try:
        from ..db.connection import connection, enabled
        if enabled():
            with connection() as conn:
                rows = conn.execute(
                    "SELECT node FROM p2gis.rubrics").fetchall()
                out = {}
                for r in rows:
                    node = r['node']
                    code = str(node.get('code') or '')
                    if code:
                        out[code] = node
                if out:
                    return out
    except Exception as e:  # noqa: BLE001
        logger.warning('[refdata] load_rubrics_dict (БД): %s', e)
    try:
        with open(rubrics_file(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def seed_refdata_db() -> dict:
    """Одноразовое сидирование справочников из файлов в БД (БД-режим).

    Вызывается сразу после apply_schema(): города и рубрики появляются в
    p2gis.cities/p2gis.rubrics немедленно, независимо от успеха Chrome-обновления
    из data.2gis.com. Идемпотентно (upsert по code). Без БД — no-op.
    """
    try:
        from ..db.connection import enabled
        if not enabled():
            return {'ok': False, 'status': 'disabled'}
        cities = load_cities_list()
        rubrics = load_rubrics_dict()
        n_cities = save_cities_db(cities, source='2gis')
        n_rubrics = save_rubrics_db(rubrics)
        logger.info('[refdata] сидирование справочников в БД: %d городов, %d рубрик',
                    n_cities, n_rubrics)
        return {'ok': True, 'cities': n_cities, 'rubrics': n_rubrics}
    except Exception as e:  # noqa: BLE001
        logger.warning('[refdata] seed_refdata_db: %s', e)
        return {'ok': False, 'error': str(e)}


# --- Разбор ответов 2GIS (чистые функции, тестируются без Chrome) ---

def parse_cities(doc: dict) -> list[dict[str, Any]]:
    """Разбирает ответ catalog.api.2gis.ru/.../region/list -> [{'name','code','domain','country_code'}]."""
    cities = []
    for item in (doc.get('result') or {}).get('items') or []:
        cities.append({
            'name': str(item.get('name') or '').strip('_'),
            'code': item.get('code'),
            'domain': item.get('domain'),
            'country_code': item.get('country_code'),
            'region': item.get('region'),
        })
    return sorted(cities, key=lambda c: c['domain'])


def parse_city_regions(doc: dict) -> dict[str, str]:
    """Извлекает карту «название города (lower) -> регион» из availableParameters.

    2GIS availableParameters содержит дерево регионов с городами; город может
    встречаться в нескольких регионах (родительский регион и город как регион
    федерального значения). Сохраняем первый непустой регион по каждому городу.
    Регион извлекаем только если запись города явно содержит название региона —
    иначе карта пропускает город (добьётся из спарсенных записей p2gis.records).
    """
    out: dict[str, str] = {}

    def _city_name(node: dict) -> Optional[str]:
        name = node.get('name')
        if isinstance(name, str) and name.strip():
            return name.strip('_').strip().lower()
        code = node.get('code')
        if isinstance(code, str) and code.strip():
            return code.strip().lower()
        return None

    def _region_of(node: dict) -> Optional[str]:
        for key in ('region', 'regionName', 'parentName', 'adm_div_name'):
            v = node.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ('cities', 'children', 'items', 'regions') and isinstance(v, list):
                    for child in v:
                        name = _city_name(child) if isinstance(child, dict) else None
                        region = _region_of(child) if isinstance(child, dict) else None
                        if name and region and name not in out:
                            out[name] = region
                        walk(child)
                else:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(doc)
    return out


def apply_city_regions(cities: list[dict[str, Any]],
                       regions_map: dict[str, str]) -> int:
    """Проставляет region городам (по названию, lower), возвращает число обновлённых."""
    n = 0
    if not regions_map:
        return 0
    for c in cities:
        key = str(c.get('name') or '').strip('_').strip().lower()
        if not key:
            continue
        region = regions_map.get(key)
        if region and not c.get('region'):
            c['region'] = region
            n += 1
    return n


def parse_rubrics(doc: dict) -> dict[str, dict]:
    """Разбирает hermes.2gis.ru availableParameters -> {code: {code,parentCode,label,...}}."""
    rubrics = dict(doc.get('rubrics') or {})
    for v in rubrics.values():
        v.pop('totalCount', None)
        v.pop('groupId', None)
    return rubrics


# --- Само обновление ---

def refresh_reference_data(force: bool = False) -> dict:
    """Обновляет cities.json и rubrics.json из data.2gis.com.

    Возвращает статус: {'ok', 'status': 'ok'|'skipped'|'busy'|'error',
    'cities', 'rubrics', 'updated_at', 'error'?}."""
    if not _REFRESH_LOCK.acquire(blocking=False):
        return {'ok': False, 'status': 'busy',
                'error': 'Обновление справочников уже выполняется'}

    try:
        if not force and is_fresh():
            return {'ok': True, 'status': 'skipped',
                    'updated_at': last_refresh_time().isoformat()}

        from ..chrome import ChromeRemote, ChromeOptions
        # Прокси без кредов — как в server._os_proxy (Chrome не умеет креды).
        chrome_options = ChromeOptions(headless=True)
        try:
            from .server import _os_proxy
            proxy = _os_proxy()
            if proxy:
                chrome_options.proxy = proxy
        except Exception:  # noqa: BLE001
            pass

        cities: list[dict] = []
        rubrics: dict[str, dict] = {}
        city_regions: dict[str, str] = {}
        with ChromeRemote(chrome_options,
                          [r'https://catalog\.api\.2gis\.[^/]+/.*/region/list',
                           r'https://hermes\.2gis\.ru/api/data/availableParameters']) as remote:
            remote.start()
            remote.navigate('https://data.2gis.com', referer='https://google.com', timeout=120)
            resp = remote.wait_response(r'https://catalog\.api\.2gis\.[^/]+/.*/region/list')
            if resp:
                body = remote.get_response_body(resp, timeout=15)
                cities = parse_cities(json.loads(body))
            resp = remote.wait_response(r'https://hermes\.2gis\.ru/api/data/availableParameters')
            if resp:
                body = remote.get_response_body(resp, timeout=15)
                doc = json.loads(body)
                rubrics = parse_rubrics(doc)
                city_regions = parse_city_regions(doc)

        if not cities:
            raise RuntimeError('2GIS не вернул список городов (region/list)')
        if not rubrics:
            raise RuntimeError('2GIS не вернул рубрикатор (availableParameters)')

        # Регионы городов: из дерева availableParameters (дополнение; добивка из
        # спарсенных записей p2gis.records происходит при синке в store.py/sync.py).
        n_regions = apply_city_regions(cities, city_regions)
        if n_regions:
            logger.info('[refdata] проставлены регионы %d городам из availableParameters',
                        n_regions)

        _write_atomic(_refdata_dir() / 'cities.json', cities)
        _write_atomic(_refdata_dir() / 'rubrics.json', rubrics)
        # БД-режим: справочники пишем и в БД (канон), файлы остаются зеркалом/фолбэком.
        n_cities = save_cities_db(cities)
        n_rubrics = save_rubrics_db(rubrics)
        _write_marker()
        _clear_caches()

        logger.info('[refdata] справочники обновлены: %d городов, %d рубрик'
                    ' (БД: %d городов, %d рубрик)',
                    len(cities), len(rubrics), n_cities, n_rubrics)
        return {'ok': True, 'status': 'ok', 'cities': len(cities),
                'rubrics': len(rubrics),
                'updated_at': last_refresh_time().isoformat()}
    except Exception as e:  # noqa: BLE001
        logger.error('[refdata] ошибка обновления справочников: %s', e)
        return {'ok': False, 'status': 'error', 'error': str(e)}
    finally:
        _REFRESH_LOCK.release()


def _clear_caches() -> None:
    """Сбрасывает lru_cache загрузчиков (свежие файлы подхватятся)."""
    try:
        from .server import _load_cities, _load_rubrics
        _load_cities.cache_clear()
        _load_rubrics.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..writer.record import _load_rubricator
        _load_rubricator.cache_clear()
    except Exception:  # noqa: BLE001
        pass


def auto_refresh_enabled() -> bool:
    return os.environ.get(_CFG_REFRESH, '1').strip().lower() not in ('0', 'no', 'false')


def start_background_refresh() -> None:
    """Фоновый поток: обновление при запуске и по расписанию (раз в сутки).

    Запускается только из run_server (реальный веб-сервер), не из create_app
    (тесты/прямые импорты не трогают сеть)."""
    if not auto_refresh_enabled():
        logger.info('[refdata] автообновление справочников отключено '
                    '(%s=0)', _CFG_REFRESH)
        return
    try:
        interval_h = float(os.environ.get(_CFG_INTERVAL_HOURS, '24'))
        check_min = float(os.environ.get(_CFG_CHECK_MINUTES, '60'))
    except ValueError:
        interval_h, check_min = 24.0, 60.0

    def loop() -> None:
        # При запуске: обновить, если справочники устарели/отсутствуют.
        try:
            if not is_fresh(interval_h):
                refresh_reference_data(force=False)
        except Exception:  # noqa: BLE001
            logger.exception('[refdata] фоновое обновление при запуске упало')
        # Дальше — по расписанию.
        while True:
            time.sleep(max(1.0, check_min) * 60)
            try:
                if not is_fresh(interval_h):
                    refresh_reference_data(force=False)
            except Exception:  # noqa: BLE001
                logger.exception('[refdata] плановое обновление упало')

    threading.Thread(target=loop, daemon=True, name='refdata-refresh').start()
    logger.info('[refdata] фоновое обновление справочников запущено '
                '(каждые %.0f ч, проверка каждые %.0f мин)', interval_h, check_min)
