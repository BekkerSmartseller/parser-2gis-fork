# ================================
# tests/test_record.py
# Юнит-тесты extract_record: все веб-сайты организации (массив websites).
# ================================
from parser_2gis.writer.record import extract_record


def _doc(name: str = 'Bright Fit', contacts=None) -> dict:
    return {
        'result': {'items': [{
            'id': '70000001030060198_abc123',
            'locale': 'ru_RU',
            'type': 'firm',
            'name': name,
            'contact_groups': [{'contacts': contacts or []}],
        }]}
    }


def test_extract_record_websites_all():
    """Несколько website-контактов -> массив websites (query-параметры срезаны)."""
    doc = _doc(contacts=[
        {'type': 'website', 'url': 'http://красноярск.брайтфит.рф/?utm_source=2gis'},
        {'type': 'website', 'url': 'http://брайтфит.рф'},
        {'type': 'website', 'url': 'http://brightfit.ru/krs/?club_id=2&utm_source=2gis'},
        {'type': 'phone', 'value': '+73912540010'},
    ])
    r = extract_record(doc)
    assert r is not None
    assert r['contacts']['websites'] == [
        'http://красноярск.брайтфит.рф/',
        'http://брайтфит.рф',
        'http://brightfit.ru/krs/',
    ]
    # совместимость: одиночный website остаётся первым
    assert r['contacts']['website'] == 'http://красноярск.брайтфит.рф/'


def test_extract_record_websites_dedup():
    """Одинаковые сайты (разные query) схлопываются в один."""
    doc = _doc(contacts=[
        {'type': 'website', 'url': 'http://site.ru/?utm=1'},
        {'type': 'website', 'url': 'http://site.ru/?utm=2'},
    ])
    r = extract_record(doc)
    assert r['contacts']['websites'] == ['http://site.ru/']


def test_extract_record_websites_single():
    """Один сайт -> массив из одного элемента, contacts.website совпадает."""
    doc = _doc(contacts=[
        {'type': 'website', 'url': 'http://braitfit.ru'},
        {'type': 'vkontakte', 'url': 'https://vk.com/brightfit'},
    ])
    r = extract_record(doc)
    assert r['contacts']['websites'] == ['http://braitfit.ru']
    assert r['contacts']['website'] == 'http://braitfit.ru'
    assert r['contacts']['vkontakte'] == 'https://vk.com/brightfit'


def test_extract_record_no_websites():
    """Нет website-контактов -> пустой массив."""
    doc = _doc(contacts=[
        {'type': 'phone', 'value': '+73912540010'},
    ])
    r = extract_record(doc)
    assert r['contacts']['websites'] == []


def _attr_doc():
    return {
        'result': {'items': [{
            'id': '9148465024074680_xyz',
            'locale': 'ru_RU',
            'type': 'firm',
            'name': 'World Class',
            'attribute_groups': [
                {'name': 'Фитнес-клубы и тренажёрные залы', 'attributes': [
                    {'id': 'a1', 'tag': 'fitness_details_yoga', 'name': 'Йога'},
                    {'id': 'a2', 'tag': 'fitness_year_unltd_subscription_price',
                     'name': 'Годовой абонемент от 30000 ₽'},
                ]},
                {'name': 'Способы оплаты', 'attributes': [
                    {'tag': 'general_payment_type_card', 'name': 'Оплата картой'},
                ]},
                {'name': 'Актуальность данных', 'attributes': [
                    {'tag': 'data_currency_data_relevance_data_is_current',
                     'name': 'Данные актуальны'},
                ]},
                {'name': 'Премия 2ГИС', 'attributes': [
                    {'tag': 'awards_awards2026_thebestfitnessclub2026', 'name': 'Лучший фитнес-клуб 2026',
                     'is_award': True},
                ]},
            ],
            'links': {
                'nearest_stations': [{'id': 's1', 'name': 'Маршала Василевского',
                                      'distance': 180, 'route_types': ['bus']}],
                'nearest_metro': [{'id': 'm1', 'distance': 900}],
                'nearest_parking': [{'id': 'p1'}],
                'entrances': [{'id': 'e1', 'is_primary': True}],
            },
            'dates': {'created_at': '2010-03-05T00:00:00Z',
                      'updated_at': '2026-07-28T03:00:00Z'},
            'has_goods': True, 'has_pinned_goods': True, 'has_discount': False,
            'is_promoted': False, 'poi_category': 'pool',
        }]}
    }


def test_extract_record_structured_attrs():
    """Новые структурированные поля: attribute_groups/tags/awards/payment/связи."""
    r = extract_record(_attr_doc())
    assert r is not None
    assert 'fitness_details_yoga' in r['attribute_tags']
    assert 'general_payment_type_card' in r['attribute_tags']
    assert r['payment_methods'] == ['Оплата картой']
    assert r['data_currency'] == 'Данные актуальны'
    awards = r['awards']
    assert any(a.get('name') == 'Лучший фитнес-клуб 2026' for a in awards)
    # плоский attributes сохранён (совместимость)
    assert 'Йога' in r['attributes']
    # связи
    assert r['links_ext']['nearest_stations'][0]['name'] == 'Маршала Василевского'
    assert r['links_ext']['nearest_metro'][0]['distance'] == 900
    assert r['links_ext']['nearest_parking'] == ['p1']
    assert r['dates']['updated_at'] == '2026-07-28T03:00:00Z'
    assert r['has_goods'] is True
    assert r['poi_category'] == 'pool'
