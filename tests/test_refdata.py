# ================================
# tests/test_refdata.py
# Юнит-тесты автообновления справочников (без сети и Chrome).
# ================================
import json
from datetime import datetime, timezone

import parser_2gis.web.refdata as refdata


def test_parse_cities():
    """region/list -> [{'name','code','domain','country_code'}], сортировка по домену."""
    doc = {'result': {'items': [
        {'name': 'Калининград_', 'code': 'kaliningrad', 'domain': 'ru', 'country_code': 'ru'},
        {'name': 'Алматы', 'code': 'almaty', 'domain': 'kz', 'country_code': 'kz'},
    ]}}
    out = refdata.parse_cities(doc)
    assert len(out) == 2
    by_code = {c['code']: c for c in out}
    assert by_code['kaliningrad']['name'] == 'Калининград'  # хвостовой '_' убран
    assert [c['domain'] for c in out] == ['kz', 'ru']  # сортировка по домену


def test_parse_rubrics():
    """availableParameters -> {code: node} без служебных полей totalCount/groupId."""
    doc = {'rubrics': {
        'fitness_club': {'code': 'fitness_club', 'parentCode': '0', 'label': 'Фитнес-клубы',
                         'totalCount': 5, 'groupId': 'x'},
        'barbershop': {'code': 'barbershop', 'parentCode': '0', 'label': 'Барбершопы',
                       'totalCount': 3, 'groupId': 'y'},
    }}
    out = refdata.parse_rubrics(doc)
    assert 'fitness_club' in out
    assert 'totalCount' not in out['fitness_club']
    assert 'groupId' not in out['fitness_club']
    assert out['barbershop']['label'] == 'Барбершопы'


def test_refdata_files_prefer_user_copy(tmp_path, monkeypatch):
    """Без user-копии — файл пакета; с копией — она."""
    monkeypatch.setattr(refdata, '_refdata_dir', lambda: tmp_path)
    from parser_2gis.paths import data_path
    assert refdata.cities_file() == data_path() / 'cities.json'
    (tmp_path / 'cities.json').write_text('[]', encoding='utf-8')
    (tmp_path / 'rubrics.json').write_text('{}', encoding='utf-8')
    assert refdata.cities_file() == tmp_path / 'cities.json'
    assert refdata.rubrics_file() == tmp_path / 'rubrics.json'


def test_freshness(tmp_path, monkeypatch):
    """last_refresh_time/is_fresh по маркеру updated_at."""
    monkeypatch.setattr(refdata, '_refdata_dir', lambda: tmp_path)
    assert refdata.last_refresh_time() is None
    assert refdata.is_fresh(24) is False
    (tmp_path / 'last_refresh.json').write_text(
        json.dumps({'updated_at': datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
    assert refdata.is_fresh(24) is True
    assert refdata.is_fresh(0.0001) is True  # кламп минимальной свежести 0.1ч
    # устаревший маркер (2 часа назад): свежо за 24ч, устарело за 0.5ч
    from datetime import timedelta
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    (tmp_path / 'last_refresh.json').write_text(json.dumps({'updated_at': old}),
                                                encoding='utf-8')
    assert refdata.is_fresh(24) is True
    assert refdata.is_fresh(0.5) is False
