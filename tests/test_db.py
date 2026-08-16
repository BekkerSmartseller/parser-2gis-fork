# ================================
# tests/test_db.py
# Юнит-тесты БД-режима (TimescaleDB): fingerprint, планировщик, синк-маппинг,
# поведение без подключённой БД. Без сети и без реальной БД.
# ================================
import pytest

from parser_2gis.db import cache as db_cache
from parser_2gis.db import connection as db_conn
from parser_2gis.db import scheduler as db_sched
from parser_2gis.db import sync as db_sync


# --- fingerprint / нормализация запросов ---

def test_normalize_query():
    assert db_cache.normalize_query('Фитнес-Клуб Москва') == 'клуб москва фитнес'
    assert db_cache.normalize_query('фитнес') == 'фитнес'
    assert db_cache.normalize_query('') == ''
    assert db_cache.normalize_query(None) == ''
    # короткие токены отбрасываются
    assert db_cache.normalize_query('и в кафе') == 'кафе'


def test_fingerprint_for_url_rubric():
    f = db_cache.fingerprint_for_url(
        'https://2gis.ru/kazan/search/Фитнес-клубы/rubricId/268/filters/sort=name')
    assert f is not None
    assert f['fingerprint'] == 'kazan|r:268'
    assert f['city_code'] == 'kazan'
    assert f['rubric_id'] == '268'


def test_fingerprint_for_url_text():
    f = db_cache.fingerprint_for_url('https://2gis.ru/moscow/search/Лаборатория')
    assert f is not None
    assert f['fingerprint'].startswith('moscow|q:')
    assert 'лаборатория' in f['query_text'].lower()


def test_fingerprint_for_url_coordinate_search():
    # Пространственный поиск без городского префикса НЕ кэшируется (всегда пере-парсинг).
    assert db_cache.fingerprint_for_url(
        'https://2gis.ru/search/фитнес%20клуб?m=45.5,58.3/12') is None
    assert db_cache.fingerprint_for_url(
        'https://2gis.ru/search/фитнес клуб') is None


def test_fingerprint_for_url_unsupported():
    # Фирма/филиалы — кэшированию не подлежат.
    assert db_cache.fingerprint_for_url('https://2gis.ru/moscow/firm/700000010') is None
    assert db_cache.fingerprint_for_url('https://2gis.ru/moscow/branches/700000010') is None
    # Безгородные URL (координатные/голые /search/) — тоже не кэшируются.
    assert db_cache.fingerprint_for_url('https://2gis.ru/search/лаборатория') is None


# --- request_status без БД -> «промах» (не свежий) ---

def test_request_status_db_disabled(monkeypatch):
    def _boom():
        raise RuntimeError('no db')
    monkeypatch.setattr(db_cache, 'connection', _boom)
    st = db_cache.request_status(['kazan|r:268'], ttl_hours=168)
    assert st['kazan|r:268']['fresh'] is False
    assert st['kazan|r:268']['status'] == 'unknown'


def test_freshness_logic():
    from datetime import datetime, timedelta, timezone

    def row(last):
        return {'fingerprint': 'k|r:1', 'city_code': 'k', 'rubric_id': '1',
                'query_text': 'x', 'url': 'u', 'records_found': 5,
                'status': 'ok', 'error': None, 'last_parsed_at': last}

    now = datetime.now(timezone.utc)
    fresh = db_cache._freshness(row(now - timedelta(hours=1)), 168)
    stale = db_cache._freshness(row(now - timedelta(days=10)), 168)
    never = db_cache._freshness(row(None), 168)
    assert fresh['fresh'] is True
    assert stale['fresh'] is False
    assert never['fresh'] is False and never['age_hours'] is None


# --- планировщик: URL-строитель ---

def test_build_urls(monkeypatch):
    monkeypatch.setattr(db_sched, '_cities_map',
                        lambda: {'moskva': {'name': 'Москва', 'domain': 'ru'},
                                 'almaty': {'name': 'Алматы', 'domain': 'kz'}})
    monkeypatch.setattr(db_sched, '_rubrics_map',
                        lambda: {'268': {'label': 'Фитнес-клубы'}})
    urls = db_sched.build_urls(['moskva'], ['268'], ['аптеки'])
    assert len(urls) == 2
    assert urls[0] == 'https://2gis.ru/moskva/search/' \
        + urllib_quote('Фитнес-клубы') + '/rubricId/268/filters/sort=name'
    assert '/search/' in urls[1] and urllib_quote('аптеки') in urls[1]


def urllib_quote(s):
    import urllib.parse
    return urllib.parse.quote(s)


# --- планировщик: вычисление next_run / due ---

def test_compute_next_interval():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    sched = {'cron': None, 'interval_minutes': 60, 'last_run': None}
    nxt = db_sched._compute_next(sched, now)
    assert nxt is not None and nxt > now


def test_compute_next_cron():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    sched = {'cron': '* * * * *', 'interval_minutes': None, 'last_run': None}
    nxt = db_sched._compute_next(sched, now)
    assert nxt is not None and now <= nxt <= now + timedelta(minutes=2)


def test_is_due():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    assert db_sched._is_due({'enabled': True, 'cron': '* * * * *',
                             'interval_minutes': None, 'next_run': None}, now) is True
    assert db_sched._is_due({'enabled': True, 'cron': None,
                             'interval_minutes': None, 'next_run': None}, now) is False
    assert db_sched._is_due({'enabled': False, 'cron': '* * * * *',
                             'interval_minutes': None, 'next_run': None}, now) is False
    future = now + timedelta(hours=2)
    assert db_sched._is_due({'enabled': True, 'cron': '* * * * *',
                             'interval_minutes': None, 'next_run': future}, now) is False


def test_validate_cron_invalid():
    with pytest.raises(ValueError):
        db_sched.validate_cron('not a cron')
    db_sched.validate_cron('0 3 * * *')
    db_sched.validate_cron(None)


# --- синхронизация: маппинг строки p2gis.records -> филиал medexpertai ---

def test_record_to_row_rubric_ids_stripped():
    """rubric_ids сохраняются без ведущих пробелов ('268; 4515' -> ['268','4515'])."""
    from parser_2gis.db.store import _record_to_row
    rec = {'firm_id': 'f1', 'org_id': 'o1', 'name': 'X', 'rubrics': ['Фитнес-клубы'],
           'rubric_ids': '268; 4515', 'contacts': {}}
    doc = {'result': {'items': [{'org': {'name': 'Орг'}}]}}
    row = _record_to_row(rec, doc, 'j1')
    assert row[22] == ['268', '4515']


def test_count_records_text_matching_sql(monkeypatch):
    """Текстовая ветка использует нормализацию дефиса + pg_trgm similarity."""
    recorded = {}

    class FakeRows:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class FakeConn:
        def execute(self, sql, params=None):
            recorded['sql'] = sql
            recorded['params'] = params
            return FakeRows([{'count': 5}])

    class FakeCtx:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db_cache, 'connection', lambda: FakeCtx())
    n = db_cache.count_records({'city_code': 'kostroma', 'rubric_id': None,
                                'query_text': 'фитнес клуб'})
    assert n == 5
    assert "replace(search_text, '-', ' ')" in recorded['sql']
    assert "similarity(search_text, %s)" in recorded['sql']
    assert recorded['params'] == ['kostroma', 'фитнес клуб', 'фитнес клуб']

    n = db_cache.count_records({'city_code': 'kostroma', 'rubric_id': '268',
                                'query_text': 'x'})
    assert "rubric_ids @> ARRAY[%s]::text[]" in recorded['sql']
    assert recorded['params'] == ['kostroma', '268']


def test_row_to_branch():
    row = {
        'firm_id': '700000010_abc', 'org_id': 'org1', 'name': 'Кафе Уют',
        'description': None, 'address': 'ул. Ленина, 1', 'address_comment': None,
        'city': 'Калининград', 'district': None, 'region': None, 'country': None,
        'postcode': None, 'lat': 54.7, 'lon': 20.5, 'phone': '+7401', 'mobile': None,
        'websites': ['http://uyt.ru'], 'socials': {'telegram': 't.me/x'},
        'rubrics': ['Кафе'], 'photos': [], 'schedule': None, 'schedule_comment': None,
        'url': 'https://2gis.com/firm/700000010', 'reviews_url': None,
        'nearest_station': None, 'station_distance': None, 'average_check': None,
        'rating': 4.5, 'review_count': 10, 'raw_doc': {'result': {'items': []}},
    }
    b = db_sync._row_to_branch(row)
    assert b['firm_id'] == '700000010_abc'
    assert b['website'] == 'http://uyt.ru'
    assert b['rubrics'] == ['Кафе']
    assert b['rating'] == 4.5
    assert b['socials'] is not None



def test_sync_insert_has_on_conflict():
    """INSERT филиалов синка устойчив к дублям firm_id (uq_org_branches_firm)."""
    from parser_2gis.db.sync import _BRANCH_INSERT_SQL
    assert 'ON CONFLICT (firm_id) WHERE firm_id IS NOT NULL DO UPDATE SET' in _BRANCH_INSERT_SQL
    assert 'organization_id=EXCLUDED.organization_id' in _BRANCH_INSERT_SQL
    assert 'updated_at=now()' in _BRANCH_INSERT_SQL

def test_sync_status_no_db(monkeypatch):
    monkeypatch.setattr(db_sync, 'enabled', lambda: False)
    st = db_sync.sync_status()
    assert st['enabled'] is False


def test_sync_to_medexpertai_no_db(monkeypatch):
    monkeypatch.setattr(db_sync, 'enabled', lambda: False)
    with pytest.raises(RuntimeError):
        db_sync.sync_to_medexpertai()


# --- apply_schema без DSN ---

def test_apply_schema_no_dsn(monkeypatch):
    monkeypatch.setattr(db_conn, 'dsn', lambda: '')
    assert db_conn.apply_schema() is False
