# ================================
# tests/test_prices_key.py
# Юнит-тесты авто-обновления публичного ключа market API 2ГИС
# (parser_2gis.db.prices). Без сети и без реальной БД.
# ================================
import pytest

from parser_2gis.db import prices as P

_DEFAULT = P._MARKET_KEY_DEFAULT


class FakeResponse:
    def __init__(self, status=200, text='', json_data=None):
        self.status_code = status
        self._text = text
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self):
        return self._json

    @property
    def text(self):
        return self._text


class FakeHttpx:
    """Заглушка httpx с настраиваемым поведением Client.get."""
    def __init__(self, responses=None, working_key=None):
        self._responses = list(responses or [])
        self.working_key = working_key
        self.calls = []
        fake = self

        class Client:
            def __init__(self, **kw):
                self._fake = fake

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None, **kw):
                fake.calls.append((url, dict(params or {})))
                key = (params or {}).get('key')
                if fake.working_key is not None and key == fake.working_key:
                    return FakeResponse(200, json_data={
                        'result': {'total': 1, 'updated_at': 'now',
                                   'items': [{'product': {'id': 'p1', 'name': 'Тест'},
                                              'offer': {'price': 100, 'currency': 'RUB'}}]}})
                if fake._responses:
                    return fake._responses.pop(0)
                return FakeResponse(500, text='no key')

        self.Client = Client


# --- вспомогательные фикстуры ---

@pytest.fixture
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(P, '_key_cached', None)
    monkeypatch.setattr(P, '_last_discovery', 0.0)
    monkeypatch.setattr(P, '_KEY_FILE', tmp_path / '.market_api_key')
    monkeypatch.delenv('MARKET_API_KEY', raising=False)
    return tmp_path


# --- _load_key ---

def test_load_key_default(clean_state):
    assert P._load_key() == _DEFAULT


def test_load_key_env(clean_state, monkeypatch):
    monkeypatch.setenv('MARKET_API_KEY', 'env-key-11111111-1111-1111-1111-111111111111')
    assert P._load_key() == 'env-key-11111111-1111-1111-1111-111111111111'


def test_load_key_file(clean_state):
    clean_state.joinpath('.market_api_key').write_text(
        'file-key-22222222-2222-2222-2222-222222222222')
    assert P._load_key() == 'file-key-22222222-2222-2222-2222-222222222222'


def test_variants_use_current_key(clean_state):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(P, '_key_cached', 'k-33333333-3333-3333-3333-333333333333')
    try:
        variants = P._variants()
    finally:
        monkeypatch.undo()
    assert all(v['key'] == 'k-33333333-3333-3333-3333-333333333333' for v in variants)
    assert {v.get('v') for v in variants} == {'2.0', '1.0', None}


# --- _discover_web_key ---

def test_discover_web_key_parses(clean_state, monkeypatch):
    html = ('var __customcfg = JSON.parse(\'{"webApiKey":"aa11bb22-cc33-dd44-ee55-ff6677889900",'
            '"webApiUrl":"https://catalog.api.2gis.ru/2.0/"}\');')
    fake = FakeHttpx(responses=[FakeResponse(200, text=html)])
    monkeypatch.setattr(P, 'httpx', fake)
    assert P._discover_web_key() == 'aa11bb22-cc33-dd44-ee55-ff6677889900'


def test_discover_web_key_throttled(clean_state, monkeypatch):
    monkeypatch.setattr(P, 'httpx', FakeHttpx(responses=[FakeResponse(200, text='no key')]))
    monkeypatch.setattr(P, '_last_discovery', 10 ** 12)  # недавно искали
    assert P._discover_web_key() is None


def test_discover_web_key_error(clean_state, monkeypatch):
    class Boom:
        class Client:
            def __enter__(self):
                raise RuntimeError('boom')
            def __exit__(self, *a):
                return False
    monkeypatch.setattr(P, 'httpx', Boom())
    assert P._discover_web_key() is None


# --- _refresh_key ---

def test_refresh_key_new(clean_state, monkeypatch):
    new = 'new-44444444-4444-4444-4444-444444444444'
    monkeypatch.setattr(P, '_discover_web_key', lambda: new)
    assert P._refresh_key() is True
    assert P._load_key() == new
    assert clean_state.joinpath('.market_api_key').read_text() == new


def test_refresh_key_same(clean_state, monkeypatch):
    monkeypatch.setattr(P, '_discover_web_key', lambda: _DEFAULT)
    assert P._refresh_key() is False


def test_refresh_key_none(clean_state, monkeypatch):
    monkeypatch.setattr(P, '_discover_web_key', lambda: None)
    assert P._refresh_key() is False


# --- _fetch_firm: автообновление ключа при 500 ---

def test_fetch_firm_auto_refresh(clean_state, monkeypatch):
    """500 на всех вариантах -> ключ обновляется с сайта -> повторный успех."""
    new_key = 'auto-55555555-5555-5555-5555-555555555555'
    fake = FakeHttpx(working_key=new_key)
    monkeypatch.setattr(P, 'httpx', fake)
    monkeypatch.setattr(P, '_discover_web_key', lambda: new_key)
    res = P._fetch_firm('9148465024104211')
    assert res is not None
    assert res['total'] == 1
    assert len(res['items']) == 1
    # хотя бы один запрос был с новым ключом
    keys = {p.get('key') for _u, p in fake.calls}
    assert new_key in keys
    # кэш обновлён
    assert P._load_key() == new_key


def test_fetch_firm_no_refresh_on_success(clean_state, monkeypatch):
    """Рабочий ключ: обновление не вызывается."""
    fake = FakeHttpx(working_key=_DEFAULT)
    monkeypatch.setattr(P, 'httpx', fake)
    monkeypatch.setattr(P, '_discover_web_key',
                        lambda: pytest.fail('не должен вызываться'))
    res = P._fetch_firm('9148465024104211')
    assert res is not None and res['total'] == 1


def test_fetch_firm_still_fails_after_refresh(clean_state, monkeypatch):
    """API лежит: после обновления ключа всё равно пусто -> None."""
    fake = FakeHttpx()  # все запросы -> 500
    monkeypatch.setattr(P, 'httpx', fake)
    monkeypatch.setattr(P, '_discover_web_key', lambda: 'fresh-66666666-6666-6666-6666-666666666666')
    assert P._fetch_firm('9148465024104211') is None
