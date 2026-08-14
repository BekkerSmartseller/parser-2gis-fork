# ================================
# tests/test_router.py
# Юнит-тесты парсинга маршрутов 2GIS (routing API) — без сети.
# ================================
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser_2gis.parser.router import _extract_route, _decode_polyline


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
