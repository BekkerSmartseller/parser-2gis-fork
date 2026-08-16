# ================================
# tests/test_server.py
# Юнит-тесты веб-слоя (справочник городов, прокси) — без сети и Chrome.
# ================================
import json

import parser_2gis.web.server as server

_PROXY_VARS = ('https_proxy', 'http_proxy', 'HTTPS_PROXY', 'HTTP_PROXY',
               'ALL_PROXY', 'all_proxy')


def _clear_proxy_env(monkeypatch):
    for k in _PROXY_VARS:
        monkeypatch.delenv(k, raising=False)


def test_translit_slug():
    """Кириллица -> латинский код города (fix B1: _translit_slug должен существовать)."""
    assert server._translit_slug('Шарья') == 'sharya'
    assert server._translit_slug('Калининград') == 'kaliningrad'
    assert server._translit_slug('') == ''


def test_os_proxy_variants(monkeypatch):
    _clear_proxy_env(monkeypatch)
    # с кредами -> None (Chrome: ERR_NO_SUPPORTED_PROXIES)
    monkeypatch.setenv('https_proxy', 'http://user:pass@host:8000')
    assert server._os_proxy() is None
    # без кредов -> возвращаем
    monkeypatch.setenv('https_proxy', 'http://host:8000')
    assert server._os_proxy() == 'http://host:8000'
    # socks:// нормализуется в socks5://
    monkeypatch.setenv('https_proxy', 'socks://host:8000')
    assert server._os_proxy() == 'socks5://host:8000'
    # пусто -> None
    monkeypatch.setenv('https_proxy', '')
    assert server._os_proxy() is None


def _fake_load_cities(cities=None):
    """Фейковый _load_cities с cache_clear (его вызывает _save_custom_cities)."""
    def _load():
        return list(cities or [])
    _load.cache_clear = lambda: None
    return _load


def test_add_city_without_code(tmp_path, monkeypatch):
    """POST /api/cities без code: транслит-slug, идемпотентно (fix B1)."""
    monkeypatch.setattr(server, '_custom_cities_path',
                        lambda: tmp_path / 'cities_custom.json')
    monkeypatch.setattr(server, '_load_cities', _fake_load_cities())
    server._load_custom_cities.cache_clear()
    try:
        city = server._add_city('Шарья')
        assert city['name'] == 'Шарья'
        assert city['code'] == 'sharya'
        assert city['domain'] == 'ru'
        assert city['country_code'] == 'ru'

        # идемпотентно: повторный вызов не создаёт дубль
        again = server._add_city('Шарья')
        assert again['code'] == 'sharya'
        custom = server._load_custom_cities()
        assert len(custom) == 1

        # сохранено в tmp-файл
        saved = json.loads((tmp_path / 'cities_custom.json').read_text(encoding='utf-8'))
        assert saved[0]['code'] == 'sharya'
    finally:
        server._load_custom_cities.cache_clear()


def test_add_city_with_explicit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_custom_cities_path',
                        lambda: tmp_path / 'cities_custom.json')
    monkeypatch.setattr(server, '_load_cities', _fake_load_cities())
    server._load_custom_cities.cache_clear()
    try:
        city = server._add_city('Санкт-Петербург', code='spb')
        assert city['code'] == 'spb'
    finally:
        server._load_custom_cities.cache_clear()


def test_add_city_requires_name(tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_custom_cities_path',
                        lambda: tmp_path / 'cities_custom.json')
    server._load_custom_cities.cache_clear()
    try:
        try:
            server._add_city('   ')
        except ValueError:
            return
        raise AssertionError('_add_city должен бросать ValueError для пустого имени')
    finally:
        server._load_custom_cities.cache_clear()


def test_build_config_storage_default_files(monkeypatch):
    """Без P2GIS_DB_URL хранилище по умолчанию — files."""
    monkeypatch.delenv('P2GIS_DB_URL', raising=False)
    cfg = server._build_config({'urls': ['https://2gis.ru/x'], 'max_records': 5})
    assert cfg.parser.storage == 'files'


def test_build_config_storage_explicit_db(monkeypatch):
    """Явный advanced.storage='db' без DSN откатывается на files."""
    monkeypatch.delenv('P2GIS_DB_URL', raising=False)
    cfg = server._build_config({'urls': ['https://2gis.ru/x'], 'max_records': 5,
                                'advanced': {'storage': 'db', 'cache_ttl_hours': 24,
                                             'sync_after': False}})
    assert cfg.parser.storage == 'files'
    assert cfg.parser.cache_ttl_hours == 24
    assert cfg.parser.sync_after is False


def test_build_config_storage_default_db_when_db_enabled(monkeypatch):
    """Авто (без advanced.storage) при доступной БД -> db; явный files сохраняется."""
    monkeypatch.setattr(server, '_db_enabled', lambda: True)
    cfg = server._build_config({'urls': ['https://2gis.ru/x'], 'max_records': 5})
    assert cfg.parser.storage == 'db'
    cfg2 = server._build_config({'urls': ['https://2gis.ru/x'], 'max_records': 5,
                                 'advanced': {'storage': 'files'}})
    assert cfg2.parser.storage == 'files'


def test_db_endpoints_disabled_without_dsn(monkeypatch):
    """Эндпоинты БД-режима возвращают 400, когда P2GIS_DB_URL не задан."""
    monkeypatch.delenv('P2GIS_DB_URL', raising=False)
    from litestar.testing import TestClient
    app = server.create_app()
    with TestClient(app) as client:
        r = client.get('/api/db/search?city=moskva&q=кафе')
        assert r.status_code == 400
        assert 'P2GIS_DB_URL' in r.json().get('error', '')
        r2 = client.get('/api/db/cache')
        assert r2.status_code == 400
        r3 = client.get('/api/schedules')
        assert r3.status_code == 400
        assert 'P2GIS_DB_URL' in r3.json().get('detail', '')
        r4 = client.get('/api/sync/status')
        assert r4.json().get('enabled') is False


def test_flatten_rubrics():
    """Дерево рубрикатора -> плоский список (вкл. group = верхний уровень)."""
    tree = {
        '0': {'code': '0', 'label': 'Город', 'parentCode': '0'},
        '2': {'code': '2', 'label': 'Досуг', 'parentCode': '0'},
        '268': {'code': '268', 'label': 'Фитнес-клубы', 'parentCode': '2'},
        '269': {'code': '269', 'label': 'Тренажёрные залы', 'parentCode': '2'},
    }
    out = server._flatten_rubrics(tree)
    by_code = {r['code']: r for r in out}
    assert '0' not in by_code  # синтетический корень пропущен
    assert by_code['268']['group'] == 'Досуг'
    assert by_code['268']['is_russian'] is True
    # сортировка по label
    labels = [r['label'] for r in out]
    assert labels == sorted(labels, key=str.lower)


def test_add_city_db_mode(monkeypatch, tmp_path):
    """БД-режим: _add_city пишет в БД (source='custom'), идемпотентно."""
    store = []

    def fake_save_cities(cities, source='2gis'):
        for c in cities:
            store.append({**c, 'source': source})
        return len(cities)

    def fake_load_cities_list():
        return [dict(c) for c in store]

    monkeypatch.setattr(server, '_db_enabled', lambda: True)
    monkeypatch.setattr(server, '_custom_cities_path',
                        lambda: tmp_path / 'cities_custom.json')
    monkeypatch.setattr('parser_2gis.web.refdata.save_cities_db', fake_save_cities)
    monkeypatch.setattr('parser_2gis.web.refdata.load_cities_list', fake_load_cities_list)
    server._load_cities.cache_clear()
    server._load_custom_cities.cache_clear()
    try:
        city = server._add_city('Шарья')
        assert city['code'] == 'sharya'
        assert len(store) == 1 and store[0]['source'] == 'custom'
        # повторный вызов — дублей нет (дедуп по _load_cities из «БД»)
        again = server._add_city('Шарья')
        assert again['code'] == 'sharya'
        assert len(store) == 1
    finally:
        server._load_cities.cache_clear()
        server._load_custom_cities.cache_clear()
