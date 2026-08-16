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
