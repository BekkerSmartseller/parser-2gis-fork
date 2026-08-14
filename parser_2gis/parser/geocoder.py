# ================================
# parser_2gis/parser/geocoder.py
# Геокодинг адреса через 2GIS UI (Chrome): открываем поиск 2GIS по адресу,
# перехватываем XHR к catalog.api.2gis.ru (поиск/карточка) и извлекаем
# координаты первого подходящего результата.
#
# Используется бэкендом (health_ai_backend_swarm) как fallback, когда MOTIS
# не знает адрес (СНТ, садоводства и т.п.). Эндпоинт: POST /api/geocode
# (в parser_2gis/web/server.py).
# ================================
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any, Optional

from ..chrome import ChromeRemote, ChromeOptions
from ..logger import logger

# 2GIS каталог-API: поиск по запросу и карточка по id
_CATALOG_SEARCH_PATTERN = r'https://catalog\.api\.2gis\.[^/]+/3\.0/items/search'
_CATALOG_BYID_PATTERN = r'https://catalog\.api\.2gis\.[^/]+/3\.0/items/byid'


def _extract_point(payload: Any) -> Optional[dict]:
    """Достаёт {lat, lon, name} из ответа 2GIS items/search или items/byid."""
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
    for item in items:
        if not isinstance(item, dict):
            continue
        point = item.get('point') or {}
        lat = point.get('lat')
        lon = point.get('lon')
        if lat is None or lon is None:
            continue
        name = item.get('name') or ''
        address = ''
        address_name = item.get('address_name') or ''
        if isinstance(address_name, dict):
            address = address_name.get('display') or address_name.get('full_name') or ''
        return {
            'lat': float(lat),
            'lon': float(lon),
            'name': name,
            'address': address or None,
        }
    return None


class Geocoder:
    """Геокодинг одного адреса через Chrome + 2GIS UI."""

    def __init__(self, chrome_options: ChromeOptions) -> None:
        self._chrome_options = chrome_options
        self._remote: Optional[ChromeRemote] = None

    def __enter__(self) -> 'Geocoder':
        self._remote = ChromeRemote(
            chrome_options=self._chrome_options,
            response_patterns=[_CATALOG_SEARCH_PATTERN, _CATALOG_BYID_PATTERN])
        self._remote.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._remote:
            try:
                self._remote.close()
            except Exception:
                pass
            self._remote = None

    def geocode(self, query: str, city: Optional[str] = None,
                timeout: int = 45) -> Optional[dict]:
        """Геокодирует адрес.

        Args:
            query: адрес (например «Пограничный проезд 766 СНТ Янтарь»).
            city: город-контекст (добавляется в поисковый запрос).
        """
        if self._remote is None:
            raise RuntimeError('Geocoder not started (use context manager)')
        search = query.strip()
        if city and city.strip() and city.strip().lower() not in search.lower():
            search = f'{search}, {city.strip()}'
        # 2GIS поиск: открываем страницу поиска по адресу; без m-параметра
        # (поиск по тексту). Координаты будут в XHR items/search.
        url = 'https://2gis.ru/search/' + urllib.parse.quote(search)
        logger.info('[geocoder] поиск 2GIS: %s', url)
        try:
            self._remote.clear_requests()
            self._remote.navigate(url, referer='https://2gis.ru', timeout=timeout)
            # ждём ответ поиска
            response = self._remote.wait_response(_CATALOG_SEARCH_PATTERN)
            if not response:
                # возможно поиск ушёл в byid (точное совпадение)
                response = self._remote.wait_response(_CATALOG_BYID_PATTERN)
            if not response:
                # подождём чуть дольше, соберём все ответы
                for _ in range(8):
                    response = self._remote.wait_response(_CATALOG_SEARCH_PATTERN)
                    if response:
                        break
                    response = self._remote.wait_response(_CATALOG_BYID_PATTERN)
                    if response:
                        break
            if not response:
                logger.warning('[geocoder] 2GIS не ответил на поиск %s', search)
                return None
            body = self._remote.get_response_body(response)
            if not body:
                logger.warning('[geocoder] пустое тело ответа для %s', search)
                return None
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                logger.warning('[geocoder] невалидный JSON от 2GIS для %s', search)
                return None
            point = _extract_point(payload)
            if not point:
                logger.warning('[geocoder] 2GIS не нашёл координат для %s', search)
                return None
            logger.info('[geocoder] найден: %s -> %s, %s',
                        point.get('name'), point.get('lat'), point.get('lon'))
            return point
        except Exception as e:  # noqa: BLE001
            logger.warning('[geocoder] ошибка геокодинга %s: %s', search, e)
            return None
