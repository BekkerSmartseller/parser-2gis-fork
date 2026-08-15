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
