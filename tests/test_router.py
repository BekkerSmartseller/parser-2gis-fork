# ================================
# tests/test_router.py
# Юнит-тесты парсинга маршрутов 2GIS (routing API) — без сети.
# ================================
from parser_2gis.parser.router import (
    _extract_route, _decode_polyline, _parse_itinerary, _parse_duration_s,
    _parse_distance_m, _parse_transfers, _build_directions_url,
    _transport_segment,
)
from parser_2gis.parser.geocoder import _city_slug, _search_url


def test_city_slug_transliterates():
    """Кириллический город -> латинский slug для URL 2GIS (иначе маршрут/поиск
    зависает: 2GIS не понимает https://2gis.ru/Калининград/...)."""
    assert _city_slug('Калининград') == 'kaliningrad'
    assert _city_slug('Москва') == 'moskva'
    assert _city_slug('Санкт-Петербург') == 'sankt-peterburg'
    assert _city_slug('Шарья') == 'sharya'


def test_search_url_uses_city_slug_and_map_anchor():
    """Поиск 2GIS должен идти в конкретном городе (slug), без узкого ?m=.

    ?m=lon,lat/16 сужает окно поиска и теряет результат вне него
    (маркеры-ответ: code 404 «Results not found»), поэтому геокодер его не шлёт.
    """
    url = _search_url('Московский проспект 273', city='Калининград')
    assert url.startswith('https://2gis.ru/kaliningrad/search/')
    assert '?m=' not in url
    assert '%D0%9C%D0%BE%D1%81%D0%BA%D0%BE%D0%B2%D1%81%D0%BA%D0%B8%D0%B9' in url

    # Без города/якоря — обычный поиск (как раньше)
    url2 = _search_url('фитнес')
    assert url2 == 'https://2gis.ru/search/%D1%84%D0%B8%D1%82%D0%BD%D0%B5%D1%81'


def test_decode_polyline_google():
    """Google официальный пример: (38.5, -120.2) -> '_p~iF~ps|U'."""
    pts = _decode_polyline('_p~iF~ps|U')
    assert len(pts) == 1
    assert abs(pts[0][0] - 38.5) < 1e-4
    assert abs(pts[0][1] + 120.2) < 1e-4


def test_extract_route_car():
    """Автомобильный маршрут: directions[0].polyline/distance/duration."""
    payload = {
        'result': {
            'directions': [{
                'polyline': '_p~iF~ps|U',
                'distance': 4200,
                'duration': 780,
                'traffic': 1.3,
            }],
        }
    }
    route = _extract_route(payload)
    assert route is not None
    assert route['mode'] == 'car'
    assert route['distance_m'] == 4200
    assert route['duration_s'] == 780
    assert route['traffic'] == 1.3
    assert len(route['points']) == 1


def test_extract_route_transit():
    """Маршрут ОТ: maps[0].legs с walk/bus/segments."""
    payload = {
        'result': {
            'maps': [{
                'legs': [
                    {'type': 'walk', 'duration': 300, 'distance': 400,
                     'polyline': '_p~iF~ps|U'},
                    {'type': 'bus', 'duration': 1200, 'distance': 5000,
                     'route': '104', 'from': 'Остановка А', 'to': 'Остановка Б',
                     'polyline': 'gfo}E_tohhV'},
                ],
            }],
        }
    }
    route = _extract_route(payload)
    assert route is not None
    assert route['mode'] == 'transit'
    assert len(route['segments']) == 2
    assert route['segments'][1]['route'] == '104'
    assert route['duration_s'] == 1500
    # walk + bus полилинии склеены (1 + 1 точка)
    assert len(route['points']) == 2


def test_extract_route_empty():
    """Пустой ответ -> None."""
    assert _extract_route({'result': {}}) is None
    assert _extract_route(None) is None
    assert _extract_route({'result': {'directions': []}}) is None


def test_extract_route_transit_itineraries_otp_style():
    """Современный ответ 2GIS/OTP для ОТ: result.itineraries[] с legs
    (mode: 'WALK'/'BUS', routeShortName, legGeometry.points, from/to объекты).
    Раньше такой формат не парсился -> парсер возвращал None -> «нет вариантов ОТ»."""
    payload = {
        'result': {
            'itineraries': [
                {
                    'duration': 1740,
                    'legs': [
                        {'mode': 'WALK', 'duration': 300, 'distance': 400,
                         'from': {'name': 'Точка А'}, 'to': {'name': 'Остановка 1'},
                         'legGeometry': {'points': '_p~iF~ps|U'}},
                        {'mode': 'BUS', 'duration': 1200, 'distance': 5200,
                         'routeShortName': '104',
                         'from': {'name': 'Остановка 1'}, 'to': {'name': 'Остановка Б'},
                         'legGeometry': {'points': 'gfo}E_tohhV'}},
                        {'mode': 'WALK', 'duration': 240, 'distance': 350,
                         'from': {'name': 'Остановка Б'}, 'to': {'name': 'Точка Б'},
                         'legGeometry': {'points': '}vx|F'}},
                    ],
                },
                {
                    'duration': 2400,
                    'legs': [
                        {'mode': 'WALK', 'duration': 400, 'distance': 500,
                         'from': {'name': 'Точка А'}, 'to': {'name': 'Остановка 2'},
                         'legGeometry': {'points': '_p~iF~ps|U'}},
                        {'mode': 'TRAM', 'duration': 2000, 'distance': 7000,
                         'routeShortName': '5',
                         'from': {'name': 'Остановка 2'}, 'to': {'name': 'Точка Б'},
                         'legGeometry': {'points': 'gfo}E_tohhV'}},
                    ],
                },
            ],
        }
    }
    route = _extract_route(payload)
    assert route is not None
    assert route['mode'] == 'transit'
    assert len(route['segments']) == 3
    # bus-участок: номер маршрута из routeShortName
    bus = [s for s in route['segments'] if s['mode'] == 'bus']
    assert bus and bus[0]['route'] == '104'
    assert bus[0]['from'] == 'Остановка 1'
    assert bus[0]['to'] == 'Остановка Б'
    assert route['duration_s'] == 1740
    # walk-only дистанция (400 + 350), без автобусной
    assert route['distance_m'] == 750
    # все варианты (2 маршрута) доступны для оценки LLM
    assert len(route['variants']) == 2
    assert route['variants'][1]['segments'][1]['route'] == '5'


def test_extract_route_transit_maps_legacy():
    """Старый формат ОТ (maps[].legs с type='walk'/'bus') тоже работает."""
    payload = {
        'result': {
            'maps': [{
                'legs': [
                    {'type': 'walk', 'duration': 300, 'distance': 400,
                     'polyline': '_p~iF~ps|U'},
                    {'type': 'bus', 'duration': 1200, 'distance': 5000,
                     'route': '104', 'from': 'Остановка А', 'to': 'Остановка Б',
                     'polyline': 'gfo}E_tohhV'},
                ],
            }],
        }
    }
    route = _extract_route(payload)
    assert route is not None
    assert route['mode'] == 'transit'
    assert len(route['segments']) == 2
    assert route['segments'][1]['route'] == '104'
    assert len(route['variants']) == 1


def test_midpoint_map():
    """Параметр карты 2GIS для страницы маршрута: середина + зум по дальности."""
    from parser_2gis.parser.router import _midpoint_map
    # точки очень близко (<1 км) -> зум 16
    m = _midpoint_map(54.710, 20.510, 54.711, 20.510)
    assert m.startswith('20.510000,54.710500/16')
    # маршрут через город (~4.5 км) -> зум меньше
    m2 = _midpoint_map(54.74, 20.44, 54.70, 20.58)
    zoom = int(m2.split('/')[1])
    assert 11 <= zoom <= 15


def test_parse_duration_s():
    """Нормализация времени карточек 2GIS: «1 час 8 мин» -> 4080 сек."""
    assert _parse_duration_s('57 мин') == 3420
    assert _parse_duration_s('1 час 8 мин') == 4080
    assert _parse_duration_s('2 часа 12 мин') == 7920
    assert _parse_duration_s('1 час') == 3600
    assert _parse_duration_s('Пешком 14 мин') == 840
    assert _parse_duration_s('') is None
    assert _parse_duration_s(None) is None


def test_parse_distance_m():
    """«18 километров» -> 18000 м; «1,2 км» -> 1200 м."""
    assert _parse_distance_m('18 километров') == 18000
    assert _parse_distance_m('1,2 км') == 1200
    assert _parse_distance_m('') is None


def test_parse_transfers():
    """Количество пересадок из текста карточки."""
    assert _parse_transfers('без пересадок') == 0
    assert _parse_transfers('1 пересадка') == 1
    assert _parse_transfers('2 пересадки') == 2
    assert _parse_transfers('') is None


def test_parse_itinerary_transit():
    """Разбор ОТ-карточек (SSR-DOM): время, пересадки, сегменты с маршрутами."""
    cards = [
        {
            'lines': ['1 час 8 мин', 'Пешком 23 мин', 'без пересадок',
                      '28', '16 мин', '7 мин'],
            'segs': [
                {'title': 'Пешком', 'chips': []},
                {'title': 'Автобус: 28',
                 'chips': [{'title': 'Автобус: 28', 'text': '28'}]},
                {'title': 'Пешком', 'chips': []},
            ],
        },
        {
            'lines': ['57 мин', 'Пешком 14 мин', '1 пересадка',
                      '72', '74', '7 мин', '7 мин'],
            'segs': [
                {'title': 'Пешком', 'chips': []},
                {'title': 'Маршрутка: 72',
                 'chips': [{'title': 'Маршрутка: 72', 'text': '72'}]},
                {'title': 'Маршруты: 2, 2а', 'chips': [
                    {'title': 'Троллейбус: 2', 'text': '2'},
                    {'title': 'Автобус: 2а', 'text': '2а'}]},
                {'title': 'Пешком', 'chips': []},
            ],
        },
    ]
    route = _parse_itinerary(cards, 'transit')
    assert route is not None
    assert route['mode'] == 'transit'
    assert route['duration_s'] == 4080
    assert route['walk_duration_s'] == 1380
    assert route['transfers'] == 0
    assert len(route['variants']) == 2

    segs = route['segments']
    assert [s['type'] for s in segs] == ['walk', 'bus', 'walk']
    assert segs[1]['route'] == '28'
    assert segs[1]['duration_s'] == 960  # '16 мин'

    v2 = route['variants'][1]
    assert v2['transfers'] == 1
    assert v2['duration_s'] == 3420  # '57 мин'
    assert [s['type'] for s in v2['segments']] == \
        ['walk', 'shuttle_bus', 'trolleybus', 'bus', 'walk']
    assert v2['segments'][3]['route'] == '2а'


def test_parse_itinerary_simple_cards():
    """Карточки авто/пешком/вело: время + дистанция (+ примечание)."""
    cards = [['15 мин', '18 километров', 'с учётом пробок'],
             ['21 мин', '18 километров', 'с учётом пробок']]
    route = _parse_itinerary(cards, 'car')
    assert route is not None
    assert route['mode'] == 'car'
    assert route['duration_s'] == 900
    assert route['distance_m'] == 18000
    assert route['note'] == 'с учётом пробок'

    # пешком: время и дистанция без примечания
    walk = _parse_itinerary([['2 часа 12 мин', '12 километров', 'по основным улицам']],
                            'walk')
    assert walk is not None
    assert walk['duration_s'] == 7920
    assert walk['distance_m'] == 12000


def test_parse_itinerary_empty():
    """Нет карточек -> None (2GIS не построил маршрут)."""
    assert _parse_itinerary([], 'transit') is None
    assert _parse_itinerary(None, 'transit') is None
    assert _parse_itinerary([], 'car') is None


def test_parse_itinerary_serializable():
    """Маршрут с вариантами не должен содержать циклы (json-сериализация)."""
    import json

    cards = [{
        'lines': ['57 мин', 'Пешком 14 мин', '1 пересадка', '72', '74', '7 мин', '7 мин'],
        'segs': [
            {'title': 'Пешком', 'chips': []},
            {'title': 'Маршрутка: 72',
             'chips': [{'title': 'Маршрутка: 72', 'text': '72'}]},
            {'title': 'Автобус: 74',
             'chips': [{'title': 'Автобус: 74', 'text': '74'}]},
        ],
    }]
    route = _parse_itinerary(cards, 'transit')
    dumped = json.dumps({'ok': True, **route})
    assert dumped
    assert route['segments'][1]['route'] == '72'


def test_build_directions_url():
    """Формат URL маршрута: точки 'lon,lat;ID', табы по режиму, якорь m=."""
    # ОТ с ID
    url = _build_directions_url(
        'kaliningrad', 'transit',
        (20.51, 54.71, '111222333444'),
        (20.53, 54.72, '555666777888'))
    assert url.startswith('https://2gis.ru/kaliningrad/directions/tab/bus/points/')
    assert '20.51%2C54.71%3B111222333444' in url
    assert '%7C' in url
    assert '20.53%2C54.72%3B555666777888' in url
    assert '?m=' in url

    # без ID — только координаты
    url2 = _build_directions_url('Калининград', 'car', (20.44, 54.74), (20.58, 54.70))
    assert url2.startswith('https://2gis.ru/kaliningrad/directions/tab/car/points/')
    assert ';' not in url2.split('/points/')[1].split('?')[0]

    # табы
    assert '/tab/bus/' in _build_directions_url('k', 'transit', (1, 2), (3, 4))
    assert '/tab/car/' in _build_directions_url('k', 'car', (1, 2), (3, 4))
    assert '/tab/pedestrian/' in _build_directions_url('k', 'walk', (1, 2), (3, 4))
    assert '/tab/bike/' in _build_directions_url('k', 'bike', (1, 2), (3, 4))
    # неизвестный режим -> car
    assert '/tab/car/' in _build_directions_url('k', 'unknown', (1, 2), (3, 4))


def test_transport_segment_metro():
    """Метро: title = название линии, номер линии — в тексте чипа.

    «Метро: Кольцевая линия» + чип text='5' -> {type: 'metro', route: '5'}."""
    seg = _transport_segment('Метро: Кольцевая линия', '5')
    assert seg['type'] == 'metro'
    assert seg['mode'] == 'metro'
    assert seg['route'] == '5'
    assert seg['name'] == 'Метро: 5'


def test_transport_segment_unknown_fallback():
    """Неизвестный тип сегмента -> generic 'transit', а не кириллическое имя."""
    seg = _transport_segment('Маршруты: 27, 3', '')
    assert seg['type'] == 'transit'
    assert seg['route'] == '27, 3'
    seg2 = _transport_segment('Фуникулёр: Горьковский', '2')
    assert seg2['type'] == 'funicular'


def test_parse_itinerary_transit_metro():
    """Москва: маршрут ОТ с метро (реальная структура SSR-карточки).

    lines: ['17 мин', 'Пешком 8 мин', '1 пересадка', '7', '5', '2 мин', '2 мин']
    сегменты: пешком | метро (Таганско-Краснопресненская, линия 7) |
              метро (Кольцевая, линия 5) | пешком."""
    cards = [{
        'lines': ['17 мин', 'Пешком 8 мин', '1 пересадка',
                  '7', '5', '2 мин', '2 мин'],
        'segs': [
            {'title': 'Пешком', 'chips': []},
            {'title': 'Метро: Таганско-Краснопресненская линия',
             'chips': [{'title': 'Метро: Таганско-Краснопресненская линия',
                        'text': '7'}]},
            {'title': 'Метро: Кольцевая линия',
             'chips': [{'title': 'Метро: Кольцевая линия', 'text': '5'}]},
            {'title': 'Пешком', 'chips': []},
        ],
    }]
    route = _parse_itinerary(cards, 'transit')
    assert route is not None
    assert route['mode'] == 'transit'
    assert route['duration_s'] == 1020  # 17 мин
    assert route['walk_duration_s'] == 480  # 8 мин
    assert route['transfers'] == 1

    segs = route['segments']
    assert [s['type'] for s in segs] == ['walk', 'metro', 'metro', 'walk']
    assert segs[1]['route'] == '7'
    assert segs[2]['route'] == '5'
    assert segs[1]['name'] == 'Метро: 7'
    # длительности метро-участков (2 мин, 2 мин)
    assert segs[1]['duration_s'] == 120
    assert segs[2]['duration_s'] == 120
    assert len(route['variants']) == 1


def test_parse_itinerary_transit_metro_spb():
    """СПб-вариант: одна линия метро без пересадок (без пересадок -> 0)."""
    cards = [{
        'lines': ['15 мин', 'Пешком 5 мин', 'без пересадок',
                  '2', '3 мин', '4 мин'],
        'segs': [
            {'title': 'Пешком', 'chips': []},
            {'title': 'Метро: Московско-Петроградская линия',
             'chips': [{'title': 'Метро: Московско-Петроградская линия',
                        'text': '2'}]},
            {'title': 'Пешком', 'chips': []},
        ],
    }]
    route = _parse_itinerary(cards, 'transit')
    assert route is not None
    assert route['transfers'] == 0
    assert route['duration_s'] == 900  # 15 мин
    assert [s['type'] for s in route['segments']] == ['walk', 'metro', 'walk']
    assert route['segments'][1]['route'] == '2'
    assert route['segments'][1]['duration_s'] == 180  # 3 мин
