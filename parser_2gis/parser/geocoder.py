# ================================
# parser_2gis/parser/geocoder.py
# Геокодинг адреса через 2GIS UI (Chrome): открываем поиск 2GIS по адресу,
# перехватываем catalog API (markers/clustered, items/search, items/byid)
# и извлекаем координаты наилучшего результата + id (для маршрутов 2GIS).
#
# Эндпоинт: POST /api/geocode (в parser_2gis/web/server.py).
# ================================
from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any, Optional

from ..chrome import ChromeRemote, ChromeOptions
from ..logger import logger

# 2GIS каталог-API: поиск по запросу (новый UI), поиск по тексту и карточка по id
_CATALOG_MARKERS_PATTERN = r'https://catalog\.api\.2gis\.[^/]+/3\.0/markers/clustered'
_CATALOG_SEARCH_PATTERN = r'https://catalog\.api\.2gis\.[^/]+/3\.0/items/search'
_CATALOG_BYID_PATTERN = r'https://catalog\.api\.2gis\.[^/]+/3\.0/items/byid'

# Кириллица -> латинский slug города для URL 2GIS (https://2gis.ru/{slug}/...)
_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}

# Признаки анти-бот проверки 2GIS (редирект на /museum и т.п.).
_CHALLENGE_URL_MARKERS = ('/museum', 'captcha', 'challenge')
_CHALLENGE_TITLE_MARKERS = ('доступ ограничен', 'проверк', 'captcha', 'музей', 'подтвердите')


def _detect_challenge(remote: ChromeRemote) -> bool:
    """True, если 2GIS показал анти-бот проверку (капчу/редирект на /museum)."""
    try:
        href = str(remote.execute_script('location.href') or '')
    except Exception:  # noqa: BLE001
        href = ''
    try:
        title = str(remote.execute_script('document.title') or '')
    except Exception:  # noqa: BLE001
        title = ''
    return (any(m in href.lower() for m in _CHALLENGE_URL_MARKERS)
            or any(m in title.lower() for m in _CHALLENGE_TITLE_MARKERS))


def _city_slug(name: str) -> str:
    """«Калининград» -> 'kaliningrad', «Санкт-Петербург» -> 'sankt-peterburg'
    (код города в URL 2GIS: латиница, слова через дефис)."""
    s = (name or '').strip().lower().replace('ё', 'e')
    out = []
    for ch in s:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum():
            out.append(ch)
        elif ch in (' ', '-', '_'):
            out.append('-')
    slug = ''.join(out)
    # схлопываем повторы и убираем дефисы по краям
    slug = re.sub(r'-{2,}', '-', slug).strip('-')
    return slug or (name or '').strip().lower()


def _search_url(query: str, city: Optional[str] = None) -> str:
    """Собирает URL поиска 2GIS.

    Без города/координат 2GIS ищет в «городе по умолчанию» (обычно по IP
    сервера) — результат может оказаться в другом регионе. Поэтому когда
    известен город — добавляем slug в путь (https://2gis.ru/kaliningrad/...).

    ВАЖНО: без параметра ?m=lon,lat/zoom — якорь карты ограничивает поиск
    видимым окном, и при зум 16 (мелком) результат вне окна теряется
    (маркеры-ответ приходит с code 404 «Results not found»). Город в слаге
    уже ограничивает поиск нужным регионом.
    """
    quoted = urllib.parse.quote(query)
    if city and city.strip():
        return f'https://2gis.ru/{_city_slug(city)}/search/{quoted}'
    return f'https://2gis.ru/search/{quoted}'


def _item_latlon(item: Any) -> tuple[Optional[float], Optional[float]]:
    """Координаты элемента 2GIS: из `point` ИЛИ из верхнеуровневых lat/lon
    (формат markers/clustered, где координаты лежат на верхнем уровне)."""
    if not isinstance(item, dict):
        return None, None
    point = item.get('point')
    if isinstance(point, dict) and point.get('lat') is not None and point.get('lon') is not None:
        return point['lat'], point['lon']
    if item.get('lat') is not None and item.get('lon') is not None:
        return item['lat'], item['lon']
    return None, None


def _tokenize(s: str) -> set[str]:
    """Значимые токены запроса (>=3 символа) для выбора лучшего результата."""
    return set(re.findall(r'[а-яёa-z0-9]{3,}', (s or '').lower().replace('ё', 'е')))


def _score_item(item: Any, query_lower: str = '', query_tokens: Optional[set] = None) -> int:
    """Оценивает результат поиска 2GIS: организации по названию запроса
    предпочтительнее дорог/районов/админ. делений."""
    if not isinstance(item, dict):
        return -1
    lat, lon = _item_latlon(item)
    if lat is None or lon is None:
        return -1
    name = str(item.get('name') or '')
    itype = str(item.get('type') or '')
    score = 0
    # Организации — то, что обычно ищем по названию (кафе, лаборатория...)
    if itype == 'org':
        score += 30
    elif itype in ('building', 'house', 'entrance', 'address'):
        score += 15
    elif itype in ('road', 'street', 'adm_div', 'district', 'city', 'area'):
        score -= 20
    elif itype:
        score += 5
    name_l = name.lower()
    if query_lower and name_l == query_lower:
        score += 100
    if query_lower and (query_lower in name_l or name_l in query_lower):
        score += 20
    if query_tokens:
        hits = sum(1 for t in query_tokens if t in name_l)
        score += hits * 10
    return score


def _extract_point(payload: Any, query: str = '') -> Optional[dict]:
    """Достаёт {lat, lon, name, address, id} из ответа 2GIS
    (markers/clustered, items/search или items/byid).

    Выбирает НЕ первый попавшийся результат, а наилучший по релевантности:
    организации с совпадающим названием предпочтительнее случайных дорог."""
    if not isinstance(payload, dict):
        return None
    result = payload.get('result') or {}
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        items = result.get('items') or []
    else:
        items = []
    if not items:
        return None
    query_lower = (query or '').lower().strip()
    query_tokens = _tokenize(query_lower)
    best: Optional[dict] = None
    best_score = -1
    for item in items:
        score = _score_item(item, query_lower, query_tokens)
        if score < 0:
            continue
        if score > best_score:
            best_score = score
            lat, lon = _item_latlon(item)
            address = ''
            address_name = item.get('address_name') or ''
            if isinstance(address_name, dict):
                address = address_name.get('display') or address_name.get('full_name') or ''
            item_id = str(item.get('id') or item.get('geometry_id') or '').split('_')[0]
            best = {
                'lat': float(lat),
                'lon': float(lon),
                'name': str(item.get('name') or ''),
                'address': address or None,
                'id': item_id or None,
            }
    return best


class Geocoder:
    """Геокодинг одного адреса через Chrome + 2GIS UI."""

    def __init__(self, chrome_options: ChromeOptions) -> None:
        self._chrome_options = chrome_options
        self._remote: Optional[ChromeRemote] = None

    def __enter__(self) -> 'Geocoder':
        self._remote = ChromeRemote(
            chrome_options=self._chrome_options,
            response_patterns=[_CATALOG_MARKERS_PATTERN,
                               _CATALOG_SEARCH_PATTERN, _CATALOG_BYID_PATTERN])
        self._remote.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._remote:
            try:
                # ВАЖНО: stop()/close() обязательны — иначе остаётся фоновый
                # поток монитора вкладки, который бесконечно опрашивает
                # GET /json (утечка на каждый вызов геокодинга).
                self._remote.stop()
            except Exception:  # noqa: BLE001
                pass
            self._remote = None

    def geocode(self, query: str, city: Optional[str] = None,
                city_lat: Optional[float] = None,
                city_lon: Optional[float] = None,
                timeout: int = 45) -> Optional[dict]:
        """Геокодирует адрес.

        Args:
            query: адрес (например «Московский проспект 273»).
            city: город-контекст (добавляется в slug URL).
            city_lat/city_lon: координаты города-якоря (резерв; в URL сейчас
                не добавляются, т.к. ?m=.../16 сужает окно поиска).
            timeout: жёсткий дедлайн на весь геокодинг (сек).

        Returns:
            {lat, lon, name, address, id} или None.
        """
        if self._remote is None:
            raise RuntimeError('Geocoder not started (use context manager)')
        search = query.strip()
        # Город НЕ добавляем в текст запроса: он уже в URL-слаге
        # (https://2gis.ru/{slug}/search/...), а текст «, Калининград»
        # сбивает поиск 2GIS (вернёт «Results not found»).
        # city_lat/city_lon зарезервированы (могут пригодиться как широкий
        # якорь-фолбэк), но в URL сейчас не добавляются: ?m=.../16 сужает
        # окно поиска и теряет результат вне него.
        url = _search_url(search, city=city)
        logger.info('[geocoder] поиск 2GIS: %s', url)
        deadline = time.monotonic() + max(10, int(timeout))
        patterns = [_CATALOG_MARKERS_PATTERN, _CATALOG_SEARCH_PATTERN, _CATALOG_BYID_PATTERN]

        try:
            self._remote.clear_requests()
            self._remote.navigate(url, referer='https://2gis.ru',
                                  timeout=min(40, deadline - time.monotonic()))
            if _detect_challenge(self._remote):
                logger.warning('[geocoder] анти-бот проверка при открытии поиска %s', url)
                time.sleep(4)
                self._remote.navigate(url, referer='https://2gis.ru',
                                      timeout=min(40, deadline - time.monotonic()))
                if _detect_challenge(self._remote):
                    logger.warning('[geocoder] повторная анти-бот проверка — сдаёмся')
                    return None

            while time.monotonic() < deadline:
                response = None
                for pattern in patterns:
                    response = self._remote.poll_response(pattern, timeout=0.3)
                    if response:
                        break
                if response:
                    if response.get('status') != 200:
                        continue
                    body = self._remote.get_response_body(response)
                    if not body:
                        continue
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    point = _extract_point(payload, query=search)
                    if point:
                        logger.info('[geocoder] найден: %s -> %s, %s (id=%s)',
                                    point.get('name'), point.get('lat'),
                                    point.get('lon'), point.get('id'))
                        return point
                time.sleep(0.3)
            logger.warning('[geocoder] 2GIS не ответил на поиск %s за %ss', search, timeout)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning('[geocoder] ошибка геокодинга %s: %s', search, e)
            return None
