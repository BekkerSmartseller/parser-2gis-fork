# ================================
# parser_2gis/parser/router.py
# Построение маршрутов через 2GIS UI (Chrome + перехват routing API).
#
# 2GIS считает маршруты на странице directions:
#   авто: https://2gis.ru/{city}/directions/points/{lon,lat;id|lon,lat;id}?m=...
#   ОТ:   https://2gis.ru/{city}/directions/tab/bus/points/{...}
#
# При открытии страницы Chrome (с чистым UA, без капчи) отправляет запросы
# к routing.api.2gis.ru — мы перехватываем ответ и парсим маршрут.
# Если 2GIS недоступен/не ответил — вызывающий (бэкенд) фолбэчится на MOTIS.
# ================================
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any, Optional

from ..chrome import ChromeRemote, ChromeOptions
from ..logger import logger

# Маршруты 2GIS считаются через routing API (host может отличаться).
_ROUTING_PATTERN = r'https://routing\.api\.2gis\.[^/]+/.*'
_ANY_2GIS_PATTERN = r'https://.*\.2gis\.[^/]+/.*'

# Ожидаемое время для расчёта маршрута (сек)
_ROUTE_WAIT = 12


def _extract_route(payload: Any) -> Optional[dict]:
    """Извлекает маршрут из ответа routing API 2GIS.

    Ожидаемая структура (routing.api.2gis.ru/2.0/directions):
      {result: {directions: [...], requests: [...]}, ...}
    В directions: {request_index, distance, duration, polyline, ...}
    Для ОТ (transit): result.maps[] с legs (тип, длительность, маршрут, пересадки).
    """
    if not isinstance(payload, dict):
        return None
    result = payload.get('result') or {}
    if not isinstance(result, dict):
        return None

    # --- Авто (directions) ---
    directions = result.get('directions') or []
    if isinstance(directions, list) and directions:
        d = directions[0]
        polyline = d.get('polyline') or ''
        distance = d.get('distance') or d.get('length') or 0
        duration = d.get('duration') or 0
        traffic = d.get('traffic')  # коэффициент пробок (может отсутствовать)
        return {
            'mode': 'car',
            'distance_m': int(distance),
            'duration_s': int(duration),
            'traffic': traffic,
            'points': _decode_polyline(polyline),
            'raw': d,
        }

    # --- ОТ (transit: maps[]) ---
    maps = result.get('maps') or []
    if isinstance(maps, list) and maps:
        m = maps[0]
        legs = m.get('legs') or []
        points: list[tuple[float, float]] = []
        total_distance = 0
        total_duration = 0
        segments = []
        for leg in legs:
            total_duration += int(leg.get('duration') or 0)
            if leg.get('type') in ('walk', 'wait'):
                total_distance += int(leg.get('distance') or 0)
            pl = leg.get('polyline') or ''
            if pl:
                pts = _decode_polyline(pl)
                if len(points) > 1 and pts and points[-1] == pts[0]:
                    points.extend(pts[1:])
                else:
                    points.extend(pts)
            segments.append({
                'type': leg.get('type'),
                'mode': leg.get('type'),
                'name': leg.get('name') or leg.get('route') or '',
                'route': leg.get('route') or leg.get('bus') or '',
                'duration_s': int(leg.get('duration') or 0),
                'distance_m': int(leg.get('distance') or 0),
                'from': leg.get('from') or '',
                'to': leg.get('to') or '',
            })
        return {
            'mode': 'transit',
            'distance_m': total_distance,
            'duration_s': total_duration,
            'points': points,
            'segments': segments,
            'raw': m,
        }

    return None


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Google/OTP encoded polyline -> [(lat, lon), ...] (масштаб 1e5)."""
    if not encoded:
        return []
    points: list[tuple[float, float]] = []
    index = 0
    lat = lon = 0
    while index < len(encoded):
        b = 0
        shift = 0
        while True:
            if index >= len(encoded):
                break
            byte = ord(encoded[index]) - 63
            index += 1
            b |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lat += (~b >> 1) if (b & 1) else (b >> 1)
        b = 0
        shift = 0
        while True:
            if index >= len(encoded):
                break
            byte = ord(encoded[index]) - 63
            index += 1
            b |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lon += (~b >> 1) if (b & 1) else (b >> 1)
        points.append((lat / 1e5, lon / 1e5))
    return points


class RouteBuilder:
    """Построение маршрута (авто/ОТ) через Chrome + 2GIS UI."""

    def __init__(self, chrome_options: ChromeOptions) -> None:
        self._chrome_options = chrome_options
        self._remote: Optional[ChromeRemote] = None

    def __enter__(self) -> 'RouteBuilder':
        self._remote = ChromeRemote(
            chrome_options=self._chrome_options,
            response_patterns=[_ROUTING_PATTERN, _ANY_2GIS_PATTERN])
        self._remote.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._remote:
            try:
                self._remote._chrome_browser.close()
            except Exception:
                pass
            self._remote = None

    def build(self, from_lat: float, from_lon: float,
              to_lat: float, to_lon: float,
              transport_mode: str = 'car',
              city: Optional[str] = None,
              timeout: int = 60) -> Optional[dict]:
        """Строит маршрут от точки А до точки Б.

        transport_mode: 'car' | 'transit' | 'walk' | 'bike'.
        Возвращает {'mode','distance_m','duration_s','points','segments',...}
        или None (2GIS не смог построить маршрут).
        """
        if self._remote is None:
            raise RuntimeError('RouteBuilder not started (use context manager)')
        mode = (transport_mode or 'car').lower()
        if mode not in ('car', 'transit', 'walk', 'bike'):
            mode = 'car'

        city_slug = city or 'kaliningrad'
        # формат точки: lon,lat (без id допускается)
        a = f'{from_lon},{from_lat}'
        b = f'{to_lon},{to_lat}'
        if mode == 'transit':
            url = (f'https://2gis.ru/{city_slug}/directions/tab/bus/points/'
                   f'{urllib.parse.quote(a)}%7C{urllib.parse.quote(b)}')
        else:
            url = (f'https://2gis.ru/{city_slug}/directions/points/'
                   f'{urllib.parse.quote(a)}%7C{urllib.parse.quote(b)}')
        logger.info('[router] 2GIS маршрут (%s): %s', mode, url[:160])
        try:
            self._remote.clear_requests()
            self._remote.navigate(url, referer='https://2gis.ru', timeout=timeout)
            route = self._wait_route(timeout=max(10, timeout - 10))
            if route:
                route['mode'] = mode
            return route
        except Exception as e:  # noqa: BLE001
            logger.warning('[router] ошибка построения маршрута: %s', e)
            return None

    def _wait_route(self, timeout: int = 30) -> Optional[dict]:
        """Ждёт ответ routing API и парсит маршрут."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self._remote.wait_response(_ROUTING_PATTERN)
            if response and response.get('status') == 200:
                body = self._remote.get_response_body(response)
                if body:
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        payload = None
                    route = _extract_route(payload) if payload else None
                    if route:
                        return route
            time.sleep(0.5)
        # fallback: пересмотреть все собранные ответы (routing мог попасть в другой pattern)
        for r in self._remote.get_responses():
            u = r.get('url', '')
            if 'routing' in u and r.get('status') == 200:
                body = self._remote.get_response_body(r)
                if body:
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        payload = None
                    route = _extract_route(payload) if payload else None
                    if route:
                        return route
        return None
