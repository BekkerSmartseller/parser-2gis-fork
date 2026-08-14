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
