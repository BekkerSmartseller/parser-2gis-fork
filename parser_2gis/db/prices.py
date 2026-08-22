# ================================
# parser_2gis/db/prices.py
# Прайс-каталог филиала (вкладка «Цены») с market-backend.api.2gis.ru.
#
# 2GIS отдаёт цены отдельным API (без Chrome и без авторизации):
#   GET https://market-backend.api.2gis.ru/5.0/product/items_by_branch
#       ?branch_id={firm_id}&locale=ru_RU&page={N}&page_size=50
# Ответ: {result: {total, updated_at, items: [{product: {...}, offer: {price}}]}}.
# Категории продуктов — сгруппированный каталог («Здоровье», «Фитнес», …).
# Здесь: HTTP-загрузка (пагинация), upsert в p2gis.branch_prices, чтение.
# ================================
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..logger import logger
from .connection import connection, enabled

try:
    import httpx
except Exception:  # noqa: BLE001
    httpx = None

_MARKET_BASE = 'https://market-backend.api.2gis.ru/5.0'
_ENDPOINT = '/product/items_by_branch'
_PAGE_SIZE = 50
_TIMEOUT = 20.0
_MAX_PAGES = 200
# Ретраи на 5xx/сетевые сбои (market API периодически отдаёт 500).
_MAX_RETRIES = 3
_RETRY_BACKOFF = [2.0, 5.0, 10.0]
# Пауза между запросами разных фирм (анти-бот / rate limit).
_FIRM_DELAY = 0.4

# Market API требует публичный ключ веб-клиента 2ГИС (иначе HTTP 500).
# Приоритет: env MARKET_API_KEY -> файл-кэш .market_api_key -> дефолт.
# При 500 на всех вариантах парсер автоматически обновляет ключ с сайта 2ГИС.
_MARKET_KEY_DEFAULT = 'c7f1a769-c8a5-4636-b14d-d8c987808a12'
_KEY_FILE = Path(__file__).resolve().parent.parent.parent / '.market_api_key'
_KEY_REFRESH_MIN = 900.0        # поиск нового ключа не чаще 15 минут
_key_cached: Optional[str] = None
_last_discovery: float = 0.0

# Часть фирм работает с v=2.0, часть — только с v=1.0; перебираем с фолбэком.


def _load_key() -> str:
    """Ключ market API: env -> файл-кэш -> дефолт."""
    global _key_cached
    if _key_cached is not None:
        return _key_cached
    env = os.environ.get('MARKET_API_KEY', '').strip()
    if env:
        _key_cached = env
        return _key_cached
    try:
        if _KEY_FILE.exists():
            k = _KEY_FILE.read_text().strip()
            if k:
                _key_cached = k
                return _key_cached
    except OSError:  # noqa: BLE001
        pass
    _key_cached = _MARKET_KEY_DEFAULT
    return _key_cached


def _variants() -> list[dict]:
    k = _load_key()
    return [{'key': k, 'v': '2.0'}, {'key': k, 'v': '1.0'}, {'key': k}]


def _discover_web_key() -> Optional[str]:
    """Извлекает актуальный webApiKey из SSR-конфига сайта 2ГИС.

    Сайт отдаёт `var __customcfg = JSON.parse('{"webApiKey":"<uuid>",...}')`.
    Ключ может ротироваться; вызывается не чаще _KEY_REFRESH_MIN секунд.
    """
    global _last_discovery
    if httpx is None:
        return None
    now = time.time()
    if now - _last_discovery < _KEY_REFRESH_MIN:
        return None
    _last_discovery = now
    for url in ('https://2gis.ru/moscow',
                'https://2gis.ru/ivanovo/firm/9148465024104211'):
        try:
            with httpx.Client(timeout=20, trust_env=False,
                              headers={'User-Agent': _UA,
                                       'Accept-Language': 'ru-RU,ru;q=0.9'}) as client:
                r = client.get(url)
                if r.status_code != 200:
                    continue
                m = re.search(r'webApiKey["\']?\s*[:=]\s*["\']([0-9a-fA-F-]{36})["\']',
                              r.text)
                if m:
                    return m.group(1)
        except Exception as e:  # noqa: BLE001
            logger.warning('[prices] discover key %s: %s', url, e)
    return None


def _refresh_key() -> bool:
    """Пробует обновить ключ с сайта; True, если ключ изменился и записан."""
    new = _discover_web_key()
    if not new or new == _load_key():
        return False
    global _key_cached
    _key_cached = new
    try:
        _KEY_FILE.write_text(new)
    except OSError:  # noqa: BLE001
        pass
    logger.info('[prices] ключ market API обновлён: %s…', new[:8])
    return True

_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

_UPSERT_SQL = """
INSERT INTO p2gis.branch_prices
    (firm_id, product_id, name, description, price, currency, categories,
     images, source, updated_at, fetched_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (firm_id, product_id) DO UPDATE SET
    name=EXCLUDED.name, description=EXCLUDED.description,
    price=EXCLUDED.price, currency=EXCLUDED.currency,
    categories=EXCLUDED.categories, images=EXCLUDED.images,
    source=EXCLUDED.source, updated_at=EXCLUDED.updated_at,
    fetched_at=now()
"""


def _num(v: Any) -> Optional[float]:
    """Число из price (int/float/строка с пробелами)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace('\u00a0', ' ').replace(' ', '').strip()
    m = re.search(r'\d[\d\s.,]*', s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(' ', '').replace(',', '.'))
    except ValueError:
        return None


def _items_from_result(res: Any) -> list[dict]:
    """Нормализует items из ответа market API в строки branch_prices."""
    out = []
    for it in ((res or {}).get('items') or []):
        if not isinstance(it, dict):
            continue
        product = it.get('product') or {}
        offer = it.get('offer') or {}
        price_value = offer.get('price_value') or {}
        fixed = price_value.get('fixed') or {}
        pid = product.get('id')
        name = product.get('name')
        if not pid or not name:
            continue
        price = offer.get('price')
        if price is None:
            price = fixed.get('value')
        categories = [c.get('label') for c in (product.get('categories') or [])
                      if isinstance(c, dict) and c.get('label')]
        images = [img for img in (product.get('images') or []) if isinstance(img, str)]
        out.append({
            'firm_id': None,  # заполняется вызывающим
            'product_id': str(pid),
            'name': name,
            'description': product.get('description') or None,
            'price': _num(price),
            'currency': offer.get('currency') or fixed.get('currency') or 'RUB',
            'categories': categories,
            'images': images,
            'source': ((product.get('source') or {}).get('code')
                       if isinstance(product.get('source'), dict) else None),
        })
    return out


def _fetch_firm(firm_id: str, locale: str = 'ru_RU') -> Optional[dict]:
    """Загружает все страницы прайса фирмы. Возвращает {updated_at, items} или None.

    Market API требует параметр key (публичный ключ 2ГИС) и рабочую версию v
    (часть фирм — v=2.0, часть — v=1.0): без key — HTTP 500. Перебираем варианты
    параметров с фолбэком и ретраим 5xx/сетевые ошибки.
    """
    if httpx is None:
        logger.warning('[prices] httpx недоступен')
        return None
    base = {'branch_id': firm_id, 'locale': locale, 'page_size': _PAGE_SIZE}
    # до 2 раундов: 1-й — текущим ключом; 2-й — после автообновления ключа
    for _round in range(2):
        all_items: list[dict] = []
        updated_at: Optional[str] = None
        total = None
        chosen: Optional[dict] = None
        failed = False
        for page in range(1, _MAX_PAGES + 1):
            res = None
            variants = _variants() if chosen is None else [chosen]
            for variant in variants:
                # пока вариант не выбран — быстрый пробинг (1 попытка на вариант),
                # чтобы при «лежащем» API не жечь время на ретраи каждого варианта.
                attempts = 1 if chosen is None else _MAX_RETRIES
                for attempt in range(1, attempts + 1):
                    try:
                        with httpx.Client(timeout=_TIMEOUT, trust_env=False,
                                          headers={'User-Agent': _UA,
                                                   'Accept-Language': 'ru-RU,ru;q=0.9'}) as client:
                            resp = client.get(f'{_MARKET_BASE}{_ENDPOINT}', params={
                                **base, 'page': page, **variant})
                            resp.raise_for_status()
                            res = (resp.json() or {}).get('result') or {}
                            chosen = variant
                            break
                    except Exception as e:  # noqa: BLE001
                        logger.warning('[prices] фирма %s стр. %d %s (попытка %d/%d): %s',
                                       firm_id, page, variant, attempt, attempts, e)
                        if attempt < attempts:
                            time.sleep(_RETRY_BACKOFF[attempt - 1])
                if res is not None:
                    break  # этот вариант параметров сработал
            if res is None:
                failed = True
                logger.warning('[prices] фирма %s: отказ на стр. %d — прайс не получен',
                               firm_id, page)
                break
            if total is None:
                total = res.get('total')
            updated_at = res.get('updated_at') or updated_at
            items = _items_from_result(res)
            if not items:
                break
            all_items.extend(items)
            if len(all_items) >= (total or 0) or len(items) < _PAGE_SIZE:
                break
        if not failed:
            break
        # Все варианты дали ошибку: возможно, ключ протух (2ГИС ротирует его).
        # Автообновляем ключ с сайта и пробуем ещё раз.
        if not _refresh_key():
            break
    if not all_items:
        return None
    return {'updated_at': updated_at, 'items': all_items, 'total': total}


def upsert_firm_prices(firm_id: str, payload: dict) -> int:
    """Пишет прайс фирмы в p2gis.branch_prices. Возвращает число записей."""
    items = (payload or {}).get('items') or []
    if not items or not enabled():
        return 0
    rows = []
    for it in items:
        it = dict(it)
        it['firm_id'] = firm_id
        it['updated_at'] = payload.get('updated_at')
        rows.append((
            it['firm_id'], it['product_id'], it['name'], it.get('description'),
            it.get('price'), it.get('currency'), it.get('categories') or [],
            it.get('images') or [], it.get('source'), it.get('updated_at'),
        ))
    if not rows:
        return 0
    try:
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(_UPSERT_SQL, rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.error('[prices] upsert %s: %s', firm_id, e)
        raise


def fetch_and_store(firm_id: str, locale: str = 'ru_RU') -> dict:
    """Полный цикл: загрузка из market API + запись в БД (если включена)."""
    payload = _fetch_firm(firm_id, locale=locale)
    if payload is None:
        return {'ok': False, 'firm_id': firm_id, 'total': 0, 'items': 0}
    stored = upsert_firm_prices(firm_id, payload)
    return {
        'ok': True, 'firm_id': firm_id,
        'total': payload.get('total') or len(payload.get('items') or []),
        'items': stored, 'updated_at': payload.get('updated_at'),
    }


def fetch_many(firm_ids: list[str], locale: str = 'ru_RU',
               delay: float = _FIRM_DELAY) -> list[dict]:
    """Последовательно загружает прайсы нескольких фирм (с паузой)."""
    out = []
    for fid in (firm_ids or []):
        fid = str(fid or '').strip()
        if not fid:
            continue
        try:
            out.append(fetch_and_store(fid, locale=locale))
        except Exception as e:  # noqa: BLE001
            logger.warning('[prices] фирма %s: %s', fid, e)
            out.append({'ok': False, 'firm_id': fid, 'error': str(e)})
        if delay > 0:
            time.sleep(delay)
    return out


def list_firm_prices(firm_id: str, limit: int = 500) -> list[dict]:
    """Прайс фирмы из БД (для UI/экспорта)."""
    if not enabled() or not firm_id:
        return []
    try:
        with connection() as conn:
            rows = conn.execute(
                "SELECT product_id, name, description, price, currency, "
                "       categories, images, source, updated_at "
                "FROM p2gis.branch_prices WHERE firm_id = %s "
                "ORDER BY name LIMIT %s", [firm_id, limit]).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning('[prices] list %s: %s', firm_id, e)
        return []
