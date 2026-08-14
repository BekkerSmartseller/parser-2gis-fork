# ================================
# parser_2gis/parser/router.py
# Построение маршрутов через 2GIS UI (Chrome + SSR-итинерарий).
#
# 2GIS считает маршруты на странице directions и рендерит итинерарий
# СЕРВЕРОМ (SSR) прямо в HTML — отдельных вызовов routing API при загрузке
# нет (routing.api.2gis.ru / public-transport.api.2gis.ru мертвы).
#
#   авто:   https://2gis.ru/{city}/directions/tab/car/points/{lon,lat[;id]|...}
#   ОТ:     https://2gis.ru/{city}/directions/tab/bus/points/{lon,lat[;id]|...}
#   пешком: https://2gis.ru/{city}/directions/tab/pedestrian/points/{...}
#   вело:   https://2gis.ru/{city}/directions/tab/bike/points/{...}
#
# План: открываем страницу в Chrome (с чистым UA, без капчи), ждём появления
# карточек маршрутов в DOM и извлекаем их (JS-экстрактор). Если 2GIS
# недоступен/не ответил — возвращаем None (на API это код 404).
# ================================
from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any, Optional

from ..chrome import ChromeRemote, ChromeOptions
from ..logger import logger
from .geocoder import _city_slug, _detect_challenge

# Ответы routing API (legacy-форматы) — оставлены для совместимости.
_ROUTING_PATTERN = r'https://(?:routing|public-transport)\.api\.2gis\.[^/]+/.*'

# Табы 2GIS directions по типу транспорта.
_MODE_TABS = {
    'car': 'tab/car/',
    'transit': 'tab/bus/',
    'walk': 'tab/pedestrian/',
    'bike': 'tab/bike/',
}

# Названия транспорта в title-атрибутах сегментов -> тип 2GIS.
# Для метро title сегмента = название линии («Метро: Кольцевая линия»),
# номер линии приходит в тексте чипа — тип при этом всё равно 'metro'.
_TRANSPORT_TITLES = {
    'Автобус': 'bus', 'Троллейбус': 'trolleybus', 'Трамвай': 'tram',
    'Маршрутка': 'shuttle_bus', 'Электричка': 'suburban_train',
    'Метро': 'metro', 'Монорельс': 'monorail', 'Фуникулёр': 'funicular',
    'Водный транспорт': 'river_transport', 'Канатная дорога': 'cable_car',
}


def _midpoint_map(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Параметр карты 2GIS: середина маршрута и зум по дальности.

    Формат: 'lon,lat/zoom'. Zoom ~16 у точки, ~12 у маршрута через город."""
    mid_lat = (lat1 + lat2) / 2.0
    mid_lon = (lon1 + lon2) / 2.0
    # грубое расстояние в км (1° широты ~111 км)
    dist_km = abs(lat2 - lat1) * 111.0
    if dist_km < 1:
        zoom = 16
    else:
        import math
        zoom = max(10, min(16, int(15 - math.log2(dist_km / 2.0))))
    return f'{mid_lon:.6f},{mid_lat:.6f}/{zoom}'


def _parse_duration_s(text: Any) -> Optional[int]:
    """«57 мин» -> 3420; «1 час 8 мин» -> 4080; «2 часа 12 мин» -> 7920."""
    t = (str(text or '')).strip().lower()
    if not t:
        return None
    hours = re.search(r'(\d+)\s*час', t)
    minutes = re.search(r'(\d+)\s*мин', t)
    h = int(hours.group(1)) if hours else 0
    m = int(minutes.group(1)) if minutes else 0
    return h * 3600 + m * 60 if (h or m) else None


def _parse_distance_m(text: Any) -> Optional[int]:
    """«18 километров» -> 18000; «1,2 км» -> 1200."""
    t = (str(text or '')).strip().lower()
    if not t:
        return None
    m = re.search(r'([\d\s.,]+)\s*километр', t) or re.search(r'([\d\s.,]+)\s*км\b', t)
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(' ', '').replace(',', '.')) * 1000)
    except ValueError:
        return None


def _parse_transfers(text: Any) -> Optional[int]:
    """«без пересадок» -> 0; «1 пересадка» -> 1; «2 пересадки» -> 2."""
    t = (str(text or '')).strip().lower()
    if not t:
        return None
    if t.startswith('без'):
        return 0
    m = re.search(r'(\d+)\s*пересад', t)
    return int(m.group(1)) if m else None


def _transport_segment(title: str, text: str = '') -> dict:
    """«Автобус: 2а» -> {type:'bus', route:'2а'}; «Маршрутка: 72» -> shuttle_bus."""
    m = re.match(r'^\s*(.*?)\s*:\s*(.+?)\s*$', title or '')
    if not m:
        return {
            'type': 'transit', 'mode': 'transit',
            'route': (text or title or '').strip(), 'name': title or '',
            'duration_s': None, 'from': '', 'to': '',
        }
    tname = m.group(1).strip()
    number = m.group(2).strip()
    # неизвестный тип -> generic 'transit' (а не кириллическое «маршруты»)
    ttype = _TRANSPORT_TITLES.get(tname, 'transit')
    route = (text or number).strip()
    return {
        'type': ttype, 'mode': ttype, 'route': route,
        'name': f'{tname}: {route}', 'duration_s': None, 'from': '', 'to': '',
    }


def _parse_transit_card(lines: list, segs: list) -> Optional[dict]:
    """Разбирает одну ОТ-карточку (SSR-DOM) в маршрут.

    lines: непустые строки innerText карточки
        (0: время всего, 1: пешком, 2: пересадки, дальше номера/длительности);
    segs: сегменты с title-атрибутами (Пешком / «Тип: N» / «Маршруты: ...»).
    """
    if not lines:
        return None
    total = _parse_duration_s(lines[0])
    if not total:
        return None
    walk = _parse_duration_s(lines[1] if len(lines) > 1 else '')
    transfers = _parse_transfers(lines[2] if len(lines) > 2 else '')
    # хвостовые «N мин» — длительности ОТ-участков (wait/ride)
    seg_durations = [ln for ln in lines[3:]
                     if re.match(r'^\d+\s*мин$', ln.strip())]

    segments: list[dict] = []
    for s in segs:
        title = str((s or {}).get('title') or '').strip()
        if title.lower() == 'пешком':
            segments.append({
                'type': 'walk', 'mode': 'walk', 'route': '', 'name': 'Пешком',
                'duration_s': None, 'from': '', 'to': '',
            })
            continue
        chips = (s or {}).get('chips') or []
        if chips:
            for ch in chips:
                segments.append(_transport_segment(str(ch.get('title') or ''),
                                                   str(ch.get('text') or '')))
        else:
            segments.append(_transport_segment(title, ''))

    # длительности привязываем к ОТ-сегментам по порядку (best effort:
    # в карточке «N мин» идут после номеров маршрутов, по одному-двум на маршрут)
    transport = [sg for sg in segments if sg['type'] != 'walk']
    if transport and seg_durations:
        for i, sg in enumerate(transport):
            if i < len(seg_durations):
                sg['duration_s'] = _parse_duration_s(seg_durations[i])

    return {
        'mode': 'transit',
        'duration_s': total,
        'distance_m': None,
        'walk_duration_s': walk,
        'transfers': transfers,
        'segments': segments,
    }


def _parse_simple_card(lines: list, mode: str) -> Optional[dict]:
    """Разбирает карточку авто/пешком/вело: «15 мин | 18 километров | ...»."""
    if not lines:
        return None
    total = _parse_duration_s(lines[0])
    if not total:
        return None
    dist = _parse_distance_m(lines[1] if len(lines) > 1 else '')
    return {
        'mode': mode,
        'duration_s': total,
        'distance_m': dist,
        'note': (lines[2] if len(lines) > 2 else '') or None,
        'segments': [],
    }


def _parse_itinerary(cards: Any, mode: str) -> Optional[dict]:
    """Собирает маршрут из карточек, извлечённых из DOM.

    cards — результат JS-экстрактора:
      - transit: [{lines: [...], segs: [{title, chips: [{title, text}]}]}];
      - авто/пешком/вело: [['15 мин', '18 километров', '...'], ...].
    Первая карточка — основной вариант, остальные — в `variants`.
    """
    if not isinstance(cards, list) or not cards:
        return None
    if mode == 'transit':
        variants = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            v = _parse_transit_card(card.get('lines') or [], card.get('segs') or [])
            if v:
                variants.append(v)
        if not variants:
            return None
        # main — копия первого варианта (иначе variants[0] is main -> цикл
        # при json-сериализации ответа: 'Circular reference detected').
        main = dict(variants[0])
        main['variants'] = variants
        return main
    # авто/пешком/вело: берём первую карточку с временем
    for card in cards:
        lines = card if isinstance(card, list) else (card or {}).get('lines') or []
        r = _parse_simple_card(lines, mode)
        if r:
            return r
    return None


# --- JS-экстракторы карточек маршрутов (выполняются в контексте страницы) ---

# ОТ: элементы с таб-фокусом, текстом «пересад...» и svg-иконкой.
_TRANSIT_CARDS_JS = r'''
(() => {
  const cards = [...document.querySelectorAll('[tabindex="0"]')]
    .filter(el => el.innerText && /пересад/.test(el.innerText)
        && el.querySelector('svg') && /\d+\s*мин/.test(el.innerText));
  return cards.map(c => {
    const segs = [];
    for (const el of c.querySelectorAll('[title]')) {
      // сегмент — элемент с title и иконкой (svg); чипы-номера без svg
      if (el.querySelector('svg path, svg')) {
        const chips = [...el.querySelectorAll('[title]')]
          .filter(x => x !== el && !x.querySelector('svg path, svg'));
        segs.push({
          title: el.getAttribute('title'),
          chips: chips.map(x => ({ title: x.getAttribute('title'),
                                   text: (x.innerText || '').trim() }))
        });
      }
    }
    const lines = (c.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    return { lines, segs };
  });
})()
'''

# Авто/пешком/вело: элементы с таб-фокусом, svg и текстом с «км/километр».
_SIMPLE_CARDS_JS = r'''
(() => {
  const cards = [...document.querySelectorAll('[tabindex="0"]')]
    .filter(el => el.innerText && /(км|километр)/.test(el.innerText)
        && el.querySelector('svg') && /\d+\s*мин/.test(el.innerText));
  return cards.map(c =>
    (c.innerText || '').split('\n').map(s => s.trim()).filter(Boolean));
})()
'''


# --- Legacy-парсеры ответов routing API (для совместимости/тестов) ---

def _leg_field(leg: dict, *keys):
    """Первое непустое значение из ключей leg (для разных форматов 2GIS/OTP)."""
    for k in keys:
        v = leg.get(k)
        if v:
            return v
    return None


def _leg_mode(leg: dict) -> str:
    """Тип участка: 'walk' | 'bus' | 'tram' | ... (регистронезависимо)."""
    m = _leg_field(leg, 'type', 'mode', 'transport_type', 'vehicleType')
    if isinstance(m, dict):
        m = m.get('type') or m.get('mode') or m.get('name') or ''
    return str(m or '').lower()


def _leg_route(leg: dict) -> str:
    """Номер/название маршрута ОТ ('104', 'троллейбус 1', ...)."""
    r = _leg_field(leg, 'route', 'routeShortName', 'shortName', 'route_name',
                   'routeNumber', 'bus', 'transport', 'vehicle')
    if isinstance(r, dict):
        r = (r.get('name') or r.get('number') or r.get('shortName')
             or r.get('routeShortName') or '')
    return str(r or '')


def _leg_polyline_points(leg: dict) -> list[tuple[float, float]]:
    """Точки участка из полилинии (закодированной строкой) ИЛИ массива точек."""
    # 1) готовый массив точек
    pts = _leg_field(leg, 'points', 'polylinePoints', 'geometryPoints')
    if isinstance(pts, list) and pts:
        out = []
        for p in pts:
            if isinstance(p, dict) and p.get('lat') is not None and p.get('lon') is not None:
                out.append((float(p['lat']), float(p['lon'])))
        if out:
            return out
    # 2) закодированная полилиния: polyline | legGeometry.points | geometry
    pl = _leg_field(leg, 'polyline', 'legGeometry', 'geometry', 'encodedPolyline')
    if isinstance(pl, dict):
        pl = pl.get('points') or pl.get('encoded') or ''
    return _decode_polyline(str(pl or '')) if pl else []


def _leg_from_to(leg: dict) -> tuple[str, str]:
    def _name(obj):
        if isinstance(obj, dict):
            return str(obj.get('name') or obj.get('stopName') or obj.get('title') or '')
        return str(obj or '')
    return _name(leg.get('from')), _name(leg.get('to'))


def _parse_transit_legs(legs: list) -> Optional[dict]:
    """Собирает ОТ-маршрут из списка участков (walk/bus/tram/...).

    Объединяет разные форматы 2GIS:
      - старый: leg = {type, polyline, route, from, to, duration, distance};
      - OTP-стиль: leg = {mode:'BUS', routeShortName, legGeometry:{points},
                          from:{name}, to:{name}, duration, distance}.
    """
    if not legs:
        return None
    points: list[tuple[float, float]] = []
    total_distance = 0
    total_duration = 0
    segments = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        mode = _leg_mode(leg)
        is_walk = mode in ('walk', 'wait', 'foot', 'pedestrian', 'walking') \
            or 'walk' in mode or 'pedestr' in mode
        total_duration += int(leg.get('duration') or 0)
        if is_walk:
            total_distance += int(leg.get('distance') or 0)
        pts = _leg_polyline_points(leg)
        if pts:
            if len(points) > 1 and points[-1] == pts[0]:
                points.extend(pts[1:])
            else:
                points.extend(pts)
        frm, to = _leg_from_to(leg)
        route = _leg_route(leg)
        segments.append({
            'type': mode or ('walk' if is_walk else 'transit'),
            'mode': mode or ('walk' if is_walk else 'transit'),
            'name': route or frm or '',
            'route': route,
            'duration_s': int(leg.get('duration') or 0),
            'distance_m': int(leg.get('distance') or 0),
            'from': frm,
            'to': to,
        })
    if not segments:
        return None
    return {
        'mode': 'transit',
        'distance_m': total_distance,
        'duration_s': total_duration,
        'points': points,
        'segments': segments,
    }


def _extract_route(payload: Any) -> Optional[dict]:
    """Извлекает маршрут из ответа routing API 2GIS (legacy-форматы).

    Форматы:
      - авто: {result: {directions: [{distance, duration, polyline, ...}]}};
      - ОТ старый: {result: {maps: [{legs: [{type, polyline, route, ...}]}]}};
      - ОТ OTP-стиль: {result: {itineraries: [{legs: [{mode, routeShortName,
        legGeometry: {points}, from/to: {name}, duration, distance}]}]}}.
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

    # --- ОТ (transit): maps[] (старый) и itineraries[] (OTP-стиль) ---
    transit_blocks = result.get('maps') or result.get('itineraries') or []
    variants: list[dict] = []
    if isinstance(transit_blocks, list):
        for m in transit_blocks:
            if not isinstance(m, dict):
                continue
            legs = m.get('legs') or []
            parsed = _parse_transit_legs(legs)
            if parsed:
                parsed['raw'] = m
                variants.append(parsed)
    if variants:
        # Первый вариант — «основной», все варианты — в variants (для LLM:
        # ассистент оценивает несколько маршрутов ОТ и выбирает лучший).
        # main — копия (иначе variants[0] is main -> цикл при сериализации).
        main = dict(variants[0])
        main['variants'] = variants
        return main

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


def _fmt_coord(v: float) -> str:
    """Координата для URL 2GIS без лишних нулей: 20.51, а не 20.510000."""
    return f'{v:.6f}'.rstrip('0').rstrip('.') or '0'


def _build_directions_url(city: Optional[str], mode: str,
                          a: tuple, b: tuple) -> str:
    """Собирает URL страницы directions 2GIS.

    Точка: (lon, lat[, id]) -> 'lon,lat' или 'lon,lat;id'. Пример:
      https://2gis.ru/kaliningrad/directions/tab/bus/points/
          20.510000,54.710000;111222333444|20.530000,54.720000;555666777888
    """
    def _point(p: tuple) -> str:
        lon, lat = float(p[0]), float(p[1])
        s = f'{_fmt_coord(lon)},{_fmt_coord(lat)}'
        if len(p) > 2 and p[2]:
            s += f';{p[2]}'
        return urllib.parse.quote(s)

    city_slug = _city_slug(city) if city and city.strip() else 'kaliningrad'
    tab = _MODE_TABS.get(mode, 'tab/car/')
    m = _midpoint_map(float(a[1]), float(a[0]), float(b[1]), float(b[0]))
    return (f'https://2gis.ru/{city_slug}/directions/{tab}points/'
            f'{_point(a)}%7C{_point(b)}?m={m}')


class RouteBuilder:
    """Построение маршрута (авто/ОТ/пешком/вело) через Chrome + SSR 2GIS."""

    def __init__(self, chrome_options: ChromeOptions) -> None:
        self._chrome_options = chrome_options
        self._remote: Optional[ChromeRemote] = None

    def __enter__(self) -> 'RouteBuilder':
        self._remote = ChromeRemote(
            chrome_options=self._chrome_options,
            response_patterns=[_ROUTING_PATTERN])
        self._remote.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._remote:
            try:
                # ВАЖНО: полный stop() (вкладка + браузер + фоновый монитор).
                # Раньше закрывался только браузер — монитор вкладки продолжал
                # опрашивать GET /json, пока Chrome не умирал сам.
                self._remote.stop()
            except Exception:  # noqa: BLE001
                pass
            self._remote = None

    def build(self, from_lat: float, from_lon: float,
              to_lat: float, to_lon: float,
              transport_mode: str = 'car',
              city: Optional[str] = None,
              from_id: Optional[str] = None,
              to_id: Optional[str] = None,
              timeout: int = 60) -> Optional[dict]:
        """Строит маршрут от точки А до точки Б.

        Args:
            from_lat/from_lon/to_lat/to_lon: координаты точек.
            transport_mode: 'car' | 'transit' | 'walk' | 'bike'.
            city: название города (кириллица) или готовый латинский slug.
            from_id/to_id: ID точек 2GIS (из /api/geocode) — точная привязка.

        Returns:
            {'mode','duration_s','distance_m','segments','variants',...}
            или None (2GIS не смог построить маршрут / капча).
        """
        if self._remote is None:
            raise RuntimeError('RouteBuilder not started (use context manager)')
        mode = (transport_mode or 'car').lower()
        if mode not in _MODE_TABS:
            mode = 'car'

        url = _build_directions_url(
            city, mode,
            (from_lon, from_lat, from_id),
            (to_lon, to_lat, to_id))
        logger.info('[router] 2GIS маршрут (%s): %s', mode, url[:180])

        # на попытку — до трети таймаута (итинерарий рендерится за 1-2 с, SSR)
        wait_per = max(12, min(20, (timeout or 60) // 2))
        for attempt in (1, 2):
            try:
                self._remote.clear_requests()
                self._remote.navigate(url, referer='https://2gis.ru', timeout=wait_per)
                route = self._wait_itinerary(mode, timeout=wait_per)
                if route:
                    route['mode'] = mode
                    return route
                if _detect_challenge(self._remote):
                    logger.warning(
                        '[router] анти-бот проверка (attempt %d). Ждём и повторяем...',
                        attempt)
                    time.sleep(4)
                    continue
            except Exception as e:  # noqa: BLE001
                logger.warning('[router] ошибка построения маршрута (attempt %d): %s',
                               attempt, e)
        return None

    def _wait_itinerary(self, mode: str, timeout: int = 20) -> Optional[dict]:
        """Ждёт появления карточек маршрута в DOM и парсит итинерарий."""
        js = _TRANSIT_CARDS_JS if mode == 'transit' else _SIMPLE_CARDS_JS
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self._remote.execute_script(js)
            except Exception as e:  # noqa: BLE001
                logger.warning('[router] execute_script карточек: %s', e)
                return None
            cards = json.loads(raw) if isinstance(raw, str) else raw
            route = _parse_itinerary(cards, mode)
            if route:
                return route
            time.sleep(0.5)

        # Диагностика состояния страницы: title и фактический URL после
        # SPA-редиректов, признак капчи — чтобы понять, загрузился ли SPA 2GIS.
        logger.warning('[router] маршрут не получен за %ss (%s).', timeout, mode)
        try:
            title = self._remote.execute_script('document.title')
            href = self._remote.execute_script('location.href')
            logger.warning('[router] состояние страницы: title=%r url=%r', title, href)
        except Exception as e:  # noqa: BLE001
            logger.warning('[router] состояние страницы недоступно: %s', e)
        if _detect_challenge(self._remote):
            logger.warning('[router] на странице анти-бот проверка.')
        try:
            txt = self._remote.execute_script(
                'document.body ? document.body.innerText.slice(0, 500) : ""')
            logger.warning('[router] текст страницы: %s',
                           (txt or '').replace('\n', ' | ')[:500])
        except Exception as e:  # noqa: BLE001
            logger.warning('[router] текст страницы недоступен: %s', e)
        return None
