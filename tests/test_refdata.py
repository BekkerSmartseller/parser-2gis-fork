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


def test_parse_city_regions():
    """availableParameters-дерево -> карта город(lower) -> регион."""
    doc = {'regions': [
        {'name': 'Костромская область', 'cities': [
            {'name': 'Кострома', 'code': 'kostroma', 'regionName': 'Костромская область'},
            {'name': 'Шарья', 'code': 'sharya', 'regionName': 'Костромская область'},
        ]},
        {'name': 'Москва', 'cities': [
            {'name': 'Москва', 'code': 'moscow', 'regionName': 'Москва'},
        ]},
    ]}
    out = refdata.parse_city_regions(doc)
    assert out.get('кострома') == 'Костромская область'
    assert out.get('шарья') == 'Костромская область'
    assert out.get('москва') == 'Москва'


def test_apply_city_regions():
    cities = [
        {'name': 'Кострома', 'code': 'kostroma', 'domain': 'ru'},
        {'name': 'Шарья', 'code': 'sharya', 'domain': 'ru'},
        {'name': 'Москва', 'code': 'moscow', 'domain': 'ru', 'region': 'Москва'},
    ]
    n = refdata.apply_city_regions(
        cities, {'кострома': 'Костромская область', 'шарья': 'Костромская область'})
    assert n == 2  # Москва уже с регионом — не трогаем
    assert cities[0]['region'] == 'Костромская область'
    assert cities[1]['region'] == 'Костромская область'
    assert cities[2]['region'] == 'Москва'


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


# --- БД-слой справочников (p2gis.cities / p2gis.rubrics) ---


def test_save_db_disabled_returns_zero(monkeypatch):
    """Без БД save_*_db ничего не пишут и не падают."""
    monkeypatch.setattr('parser_2gis.db.connection.enabled', lambda: False)
    assert refdata.save_cities_db([{'code': 'x', 'name': 'X'}]) == 0
    assert refdata.save_rubrics_db({'1': {'code': '1', 'label': 'X'}}) == 0


def test_load_cities_list_file_mode(tmp_path, monkeypatch):
    """Файловый режим: load_cities_list читает cities.json."""
    monkeypatch.setattr('parser_2gis.db.connection.enabled', lambda: False)
    monkeypatch.setattr(refdata, '_refdata_dir', lambda: tmp_path)
    (tmp_path / 'cities.json').write_text(
        json.dumps([{'name': 'Алматы', 'code': 'almaty', 'domain': 'kz', 'country_code': 'kz'}]),
        encoding='utf-8')
    assert refdata.load_cities_list() == [
        {'name': 'Алматы', 'code': 'almaty', 'domain': 'kz', 'country_code': 'kz'}]


def test_load_rubrics_dict_file_mode(tmp_path, monkeypatch):
    """Файловый режим: load_rubrics_dict читает rubrics.json (пакет)."""
    monkeypatch.setattr('parser_2gis.db.connection.enabled', lambda: False)
    rubrics = refdata.load_rubrics_dict()
    assert isinstance(rubrics, dict) and len(rubrics) > 1000
    assert '268' in rubrics  # Фитнес-клубы


def test_load_cities_list_db_failure_falls_back_to_file(tmp_path, monkeypatch):
    """БД включена, но недоступна -> фолбэк на файл (не падаем)."""
    monkeypatch.setattr('parser_2gis.db.connection.enabled', lambda: True)
    monkeypatch.setattr(refdata, '_refdata_dir', lambda: tmp_path)

    def boom():
        raise RuntimeError('db down')
    monkeypatch.setattr('parser_2gis.db.connection.connection', boom)
    (tmp_path / 'cities.json').write_text(
        json.dumps([{'name': 'Шарья', 'code': 'sharya', 'domain': 'ru', 'country_code': 'ru'}]),
        encoding='utf-8')
    cities = refdata.load_cities_list()
    assert cities and cities[0]['code'] == 'sharya'


def test_seed_refdata_db(monkeypatch, tmp_path):
    """seed_refdata_db: без БД — no-op; с БД — пишет города и рубрики из файлов."""
    calls = []

    def fake_save_cities(cities, source='2gis'):
        calls.append(('cities', source, len(cities)))
        return len(cities)

    def fake_save_rubrics(rubrics):
        calls.append(('rubrics', len(rubrics)))
        return len(rubrics)

    # БД выключена -> no-op
    monkeypatch.setattr('parser_2gis.db.connection.enabled', lambda: False)
    assert refdata.seed_refdata_db()['status'] == 'disabled'

    # БД включена -> save_*_db вызываются с данными из файлов
    monkeypatch.setattr('parser_2gis.db.connection.enabled', lambda: True)
    monkeypatch.setattr(refdata, 'save_cities_db', fake_save_cities)
    monkeypatch.setattr(refdata, 'save_rubrics_db', fake_save_rubrics)
    res = refdata.seed_refdata_db()
    assert res['ok'] is True
    assert any(c[0] == 'cities' for c in calls)
    assert any(c[0] == 'rubrics' for c in calls)


def test_save_db_calls(monkeypatch):
    """save_cities_db/save_rubrics_db формируют корректные параметры (фейк-пул)."""
    monkeypatch.setattr('parser_2gis.db.connection.enabled', lambda: True)
    recorded = []

    class FakeCursor:
        def __init__(self, conn):
            self._conn = conn

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def executemany(self, sql, rows):
            recorded.append((sql, rows))

    class FakeConn:
        def cursor(self):
            return FakeCursor(self)

        def transaction(self):
            from contextlib import nullcontext
            return nullcontext()

    monkeypatch.setattr('parser_2gis.db.connection.connection',
                        lambda: _FakeCtx(FakeConn()))
    n = refdata.save_cities_db([{'name': 'Шарья', 'code': 'sharya', 'domain': 'ru',
                                 'country_code': 'ru'}], source='custom')
    assert n == 1
    assert recorded and recorded[0][1][0] == ('sharya', 'Шарья', 'ru', 'ru', None, 'custom')
    recorded.clear()
    refdata.save_cities_db([{'name': 'Кострома', 'code': 'kostroma', 'domain': 'ru',
                             'country_code': 'ru', 'region': 'Костромская область'}])
    assert recorded and recorded[0][1][0] == ('kostroma', 'Кострома', 'ru', 'ru',
                                              'Костромская область', '2gis')
    recorded.clear()
    refdata.save_rubrics_db({'268': {'code': '268', 'label': 'Фитнес-клубы', 'parentCode': '0'}})
    assert recorded and recorded[0][1][0][2] == '0'


class _FakeCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *a):
        return False
