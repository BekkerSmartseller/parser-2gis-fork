# ================================
# tests/test_geocoder.py
# Юнит-тесты геокодинга 2GIS (markers/clustered и legacy форматы) — без сети.
# ================================
from parser_2gis.parser.geocoder import (
    _extract_point, _item_latlon, _score_item, _city_slug, _search_url,
)


def test_city_slug():
    """Кириллица -> латинский slug (слова через дефис)."""
    assert _city_slug('Калининград') == 'kaliningrad'
    assert _city_slug('Москва') == 'moskva'
    assert _city_slug('Санкт-Петербург') == 'sankt-peterburg'
    assert _city_slug('Шарья') == 'sharya'


def test_search_url():
    """Поиск: slug города в пути, без сужающего окно ?m= (теряет результат)."""
    url = _search_url('Московский проспект 273', city='Калининград')
    assert url.startswith('https://2gis.ru/kaliningrad/search/')
    assert '?m=' not in url
    url2 = _search_url('фитнес')
    assert url2 == 'https://2gis.ru/search/%D1%84%D0%B8%D1%82%D0%BD%D0%B5%D1%81'


def test_item_latlon():
    """Координаты из point ИЛИ верхнего уровня (формат markers/clustered)."""
    assert _item_latlon({'point': {'lat': 1.5, 'lon': 2.5}}) == (1.5, 2.5)
    assert _item_latlon({'lat': 1.5, 'lon': 2.5}) == (1.5, 2.5)
    assert _item_latlon({'point': {'lat': 1.5}}) == (None, None)
    assert _item_latlon('not a dict') == (None, None)


def test_score_item():
    """Организации по названию предпочтительнее дорог/районов; без координат — -1."""
    assert _score_item({'name': 'x', 'type': 'org'}) == -1  # нет координат
    org = _score_item({'name': 'Кафе Уют', 'type': 'org', 'lat': 1, 'lon': 2},
                      query_lower='кафе уют')
    road = _score_item({'name': 'Ленинский проспект', 'type': 'road', 'lat': 1, 'lon': 2},
                       query_lower='кафе уют')
    assert org > road
    assert org > 0
    assert _score_item('not a dict') == -1


def test_extract_point_markers():
    """markers/clustered: верхнеуровневые lat/lon, id из id/geometry_id."""
    payload = {
        'result': {
            'items': [
                {'id': '111222333444_7tvwktuc', 'geometry_id': '111222333444',
                 'lat': 54.71, 'lon': 20.51,
                 'name': 'Московский проспект, 273', 'type': 'building',
                 'address_name': {'display': 'Московский проспект, 273'}},
                {'id': 'id_city', 'lat': 54.70, 'lon': 20.50,
                 'name': 'Калининград', 'type': 'city',
                 'address_name': {'display': 'Калининград'}},
            ],
            'total': 2,
        }
    }
    pt = _extract_point(payload, query='Московский проспект 273')
    assert pt is not None
    assert pt['lat'] == 54.71
    assert pt['lon'] == 20.51
    assert pt['id'] == '111222333444'
    assert pt['address'] == 'Московский проспект, 273'


def test_extract_point_legacy_items_search():
    """Старый формат items/search (point.lat/lon) тоже парсится."""
    payload = {
        'result': {
            'items': [
                {'id': '123_abc', 'point': {'lat': 54.7, 'lon': 20.5},
                 'name': 'Московский проспект, 273', 'type': 'building'},
            ],
        }
    }
    pt = _extract_point(payload, query='московский проспект')
    assert pt is not None
    assert pt['lat'] == 54.7
    assert pt['id'] == '123'


def test_extract_point_empty():
    """Пустые ответы -> None."""
    assert _extract_point(None) is None
    assert _extract_point({}) is None
    assert _extract_point({'result': {'items': []}}) is None
    assert _extract_point({'result': {'items': [{'name': 'x'}]}}) is None  # нет координат
