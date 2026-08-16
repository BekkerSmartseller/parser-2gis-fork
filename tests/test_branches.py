# ================================
# tests/test_branches.py
# Юнит-тесты сбора филиалов сети (страницы /branches/...) — без сети.
# ================================
import re

from parser_2gis.chrome.dom import DOMNode
from parser_2gis.parser.parsers.main import MainParser


def _node(href: str) -> DOMNode:
    """DOM-узел ссылки `<a href="...">` (атрибуты списком, как у CDP)."""
    return DOMNode(nodeId=1, backendNodeId=2, nodeType=1, nodeName='A',
                   localName='a', nodeValue='',
                   attributes=['href', href])


def test_url_pattern_branches():
    """url_pattern принимает поиск и страницы филиалов."""
    p = MainParser.url_pattern()
    assert re.match(p, 'https://2gis.ru/krasnoyarsk/search/Bright%20Fit')
    assert re.match(p, 'https://2gis.ru/search/%D1%84%D0%B8%D1%82%D0%BD%D0%B5%D1%81?m=45.5,58.3/12')
    assert re.match(p, 'https://2gis.ru/krasnoyarsk/branches/70000001029980685')
    assert re.match(p, 'https://2gis.ru/krasnoyarsk/branches/70000001029980685'
                       '/firm/70000001030060198/92.8,56.0')
    assert not re.match(p, 'https://2gis.ru/krasnoyarsk/firm/70000001030060198')
    assert not re.match(p, 'https://example.com/search/x')


def test_is_branch_card():
    """Карточка филиала: `/firm/{id}` без `?stat=` (выдача использует `?stat=`)."""
    assert MainParser._is_branch_card(_node('/krasnoyarsk/firm/70000001030060198'))
    assert not MainParser._is_branch_card(
        _node('/krasnoyarsk/firm/70000001030060198?stat=eyJ0ZXN0IjoieSJ9'))
    assert not MainParser._is_branch_card(_node('/krasnoyarsk/branches/70000001029980685'))
    assert not MainParser._is_branch_card(_node('/krasnoyarsk/search/фитнес'))
    assert not MainParser._is_branch_card(_node('https://2gis.ru'))


def test_doc_firm_ids():
    """Числовые id организаций (префиксы до `_`) из byid-документа."""
    doc = {'result': {'items': [
        {'id': '70000001030060198_abc123'},
        {'id': '985690699468019'},
    ]}}
    assert MainParser._doc_firm_ids(doc) == ['70000001030060198', '985690699468019']
    assert MainParser._doc_firm_ids({'result': {'items': []}}) == []
    assert MainParser._doc_firm_ids({}) == []


def test_get_branch_urls_adds_city_prefix():
    """Cityless `/branches/{id}` в выдаче получает город текущей страницы —
    иначе 2GIS редиректит на «город по умолчанию» (чужие филиалы)."""
    from parser_2gis.parser.parsers.main import MainParser

    class FakeDom:
        def __init__(self, hrefs):
            self._hrefs = hrefs

        def search(self, pred):
            return [n for n in (_node(h) for h in self._hrefs) if pred(n)]

    class FakeRemote:
        def __init__(self, url, hrefs):
            self._url = url
            self._hrefs = hrefs

        def execute_script(self, script):
            return self._url

        def get_document(self):
            return FakeDom(self._hrefs)

    p = MainParser.__new__(MainParser)
    p._url = ('https://2gis.ru/vologda/search/'
              '%D1%82%D1%80%D0%B5%D0%BD%D0%B0%D0%B6%D1%91%D1%80%D0%BD%D1%8B%D0%B9'
              '%20%D0%B7%D0%B0%D0%BB/filters/sort=name')
    p._chrome_remote = FakeRemote(p._url, [
        '/vologda/search/тренажёрный зал?stat=eyJ4IjoieSJ9',   # ссылка выдачи — не филиалы
        '/branches/10978060962631985',                          # cityless -> город страницы
        '/vologda/branches/10978060962628287',                  # уже с городом — без изменений
        'https://2gis.ru/branches/10978060962628255',           # absolute cityless -> город
        'https://example.com/branches/123',                     # чужой хост — игнор
        '/branches/10978060962628255/filters',                  # с filters — игнор
    ])
    urls = p._get_branch_urls()
    assert 'https://2gis.ru/vologda/branches/10978060962631985' in urls
    assert 'https://2gis.ru/vologda/branches/10978060962628287' in urls
    assert 'https://2gis.ru/vologda/branches/10978060962628255' in urls
    assert not any(u.startswith('https://2gis.ru/branches/') for u in urls)
    assert not any('example.com' in u for u in urls)
    assert len(urls) == 3


def test_page_city_helper():
    """_page_city вытаскивает slug города и не путает служебные сегменты."""
    from parser_2gis.parser.parsers.main import _page_city
    assert _page_city('https://2gis.ru/vologda/search/фитнес') == 'vologda'
    assert _page_city('https://2gis.ru/kaliningrad/branches/70000001029980685') == 'kaliningrad'
    assert _page_city('https://2gis.ru/search/фитнес?m=45.5,58.3/12') is None
    assert _page_city('https://2gis.ru/branches/70000001029980685') is None
    assert _page_city(None) is None


def test_note_collected_tracks_orgs():
    """_note_collected ведёт счёт собранных фирм по org.id (для пропуска сетей)."""
    from parser_2gis.parser.parsers.main import MainParser
    p = MainParser.__new__(MainParser)
    p._collected_by_org = {}
    p._branch_count_by_org = {}
    p._new_seen = set()

    doc = {'result': {'items': [
        {'id': '5630027815194140_abc', 'org': {'id': '5630036405127738', 'branch_count': 4}},
        {'id': '5630027815194141_def', 'org': {'id': '5630036405127738', 'branch_count': 4}},
    ]}}
    p._note_collected(doc)
    assert p._collected_by_org['5630036405127738'] == {'5630027815194140', '5630027815194141'}
    assert p._branch_count_by_org['5630036405127738'] == 4
    assert p._new_seen == {'5630027815194140', '5630027815194141'}

    # без org.id — только seen
    p._note_collected({'result': {'items': [{'id': '70000001000000001_x'}]}})
    assert '70000001000000001' in p._new_seen
    assert '70000001000000001' not in p._collected_by_org.get('70000001000000001', set())
