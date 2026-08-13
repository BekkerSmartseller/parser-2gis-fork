from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Optional

from pydantic import ValidationError

from ..logger import logger
from ..paths import data_path
from .models import CatalogItem
from .models.attributes import Attribute

# ---------------------------------------------------------------------------
# Резолв «раздела» рубрикатора (верхний уровень рубрики) через data/rubrics.json
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_rubricator() -> dict:
    """Загружает рубрикатор 2GIS из data/rubrics.json."""
    try:
        with open(data_path() / 'rubrics.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def rubric_section_name(rubric_id: str | None) -> str | None:
    """Возвращает название раздела (верхнего уровня рубрикатора) для рубрики.

    Пример: для рубрики «Барбершопы» (parentCode цепочка вверх до кода '0')
    вернёт «Красота / Здоровье», для «Аптеки» — «Медицина / Здоровье / Красота».
    """
    if not rubric_id:
        return None
    rubrics = _load_rubricator()
    node = rubrics.get(str(rubric_id))
    if not node:
        return None
    # Поднимаемся по parentCode до корня (parentCode == '0').
    seen = set()
    while node and node.get('parentCode') not in (None, '', '0') and node.get('code') not in seen:
        seen.add(node.get('code'))
        parent = rubrics.get(str(node['parentCode']))
        if not parent:
            break
        node = parent
    if node and node.get('parentCode') in (None, '', '0'):
        return node.get('label') or None
    return None

# Type fallback names for non-firm objects.
TYPE_NAMES = {
    'parking': 'Парковка', 'street': 'Улица', 'road': 'Дорога',
    'crossroad': 'Перекрёсток', 'station': 'Остановка',
}

# Ключевые слова для поиска «среднего чека» среди атрибутов.
_AVERAGE_CHECK_KEYWORDS = re.compile(
    r'средн\w+\s+чек|средний\s+счёт|чек\s+от|средн\w+|абонемент\s+от|\d+\s*₽|\$\d',
    re.IGNORECASE,
)

# Группы атрибутов, которые не несут практической ценности для выгрузки.
_NOISE_GROUPS = {'Актуальность данных', 'Способы оплаты', 'Тип предприятия'}


def _adm_value(catalog_item: CatalogItem, adm_type: str) -> Optional[str]:
    """Get administrative division value by type."""
    for div in catalog_item.adm_div:
        if div.type == adm_type:
            return div.name
    return None


def _all_attributes(catalog_item: CatalogItem) -> list[Attribute]:
    """Flatten all attributes from all groups."""
    out = []
    for group in catalog_item.attribute_groups:
        for attr in group.attributes:
            out.append(attr)
    return out


def _average_check(catalog_item: CatalogItem) -> Optional[str]:
    """Try to find an "average check"/price attribute."""
    prizes = []
    for attr in _all_attributes(catalog_item):
        name = (attr.name or '').strip()
        if name and _AVERAGE_CHECK_KEYWORDS.search(name):
            prizes.append(name)
    return '; '.join(prizes) if prizes else None


def rubric_sections(catalog_item: CatalogItem) -> tuple[Optional[str], str]:
    """Build rubric info: (основная рубрика, подрубрики).

    The primary rubric is the "niche" of the firm; additional rubrics are
    sub-sections (e.g. a medical center being also "Терапевт", "Кардиолог").
    """
    primary_name = None
    additional = []
    for rubric in catalog_item.rubrics:
        if rubric.kind == 'primary':
            primary_name = rubric.name
        else:
            additional.append(rubric.name)
    return primary_name, '; '.join(additional)


def usable_attributes(catalog_item: CatalogItem) -> str:
    """All attribute names except noise groups (payment, data currency, type)."""
    attributes = []
    for group in catalog_item.attribute_groups:
        if group.name in _NOISE_GROUPS:
            continue
        for attr in group.attributes:
            if attr.name:
                attributes.append(attr.name)
    return '; '.join(attributes)


_DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _schedule_to_dict(schedule) -> Optional[dict]:
    """Расписание в JSON-совместимый вид: {day: [{"from", "to"}], ...} + meta."""
    if schedule is None:
        return None
    days = {}
    for day in _DAYS:
        day_value = getattr(schedule, day, None)
        if not day_value or not day_value.working_hours:
            continue
        days[day] = [
            {'from': wh.from_, 'to': wh.to} for wh in day_value.working_hours
        ]
    meta = {}
    for key in ('is_24x7', 'description', 'comment', 'date_from', 'date_to'):
        val = getattr(schedule, key, None)
        if val is not None:
            meta[key] = val
    if not days and not meta:
        return None
    return {**meta, 'days': days}


def _schedule_comment(catalog_item: CatalogItem) -> Optional[str]:
    """Комментарий к расписанию (основное расписание или первая группа)."""
    for schedule in (catalog_item.schedule,
                     (catalog_item.contact_groups[0].schedule
                      if catalog_item.contact_groups else None)):
        if schedule and schedule.comment:
            return schedule.comment
    return None


def extract_record(catalog_doc: Any) -> Optional[dict[str, Any]]:
    """Extract a flat, presentation-ready record from a Catalog Item document.

    Shared by the HTML writer and the web dashboard. Returns `None` for
    malformed documents or entries without a name.
    """
    try:
        item = catalog_doc['result']['items'][0]
    except (KeyError, IndexError, TypeError):
        return None

    try:
        catalog_item = CatalogItem(**item)
    except ValidationError as e:
        logger.error('Ошибка извлечения записи: %s', e.errors()[0].get('loc') if e.errors() else e)
        return None

    # Name / description
    name, description = None, None
    if catalog_item.name_ex:
        name = catalog_item.name_ex.primary
        description = catalog_item.name_ex.extension
    elif catalog_item.name:
        name = catalog_item.name
    elif catalog_item.type in TYPE_NAMES:
        name = TYPE_NAMES[catalog_item.type]
    if not name:
        return None

    rating = review_count = None
    org_rating = org_review_count = None
    if catalog_item.reviews:
        rating = catalog_item.reviews.general_rating
        review_count = catalog_item.reviews.general_review_count
        org_rating = catalog_item.reviews.org_rating
        org_review_count = catalog_item.reviews.org_review_count

    # Contacts: keep the first value of each type, comments separately.
    # Mobile numbers (RF/KZ: 7/8-9XX...) extracted into their own field.
    contacts: dict[str, str] = {}
    contact_comments: dict[str, str] = {}
    mobile_phone = None
    for group in catalog_item.contact_groups:
        for contact in group.contacts:
            if contact.type == 'phone':
                value = contact.text or contact.value
                digits = re.sub(r'\D', '', value or '')
                if mobile_phone is None and re.match(r'^(?:7|8)?9\d{9}$', digits):
                    mobile_phone = value
                contacts.setdefault('phone', value)
            elif contact.type == 'email':
                contacts.setdefault('email', contact.value)
            elif contact.url:
                contacts.setdefault(contact.type, contact.url.split('?')[0])
            if contact.comment:
                contact_comments.setdefault(contact.type, contact.comment)

    # Stations
    stations = []
    if catalog_item.links:
        for station in catalog_item.links.nearest_stations:
            stations.append({
                'name': station.name,
                'distance': station.distance,
                'route_types': station.route_types or [],
            })
    nearest_station = stations[0]['name'] if stations else None
    station_distance = stations[0]['distance'] if stations else None

    # Stations — all of them, sorted by distance (primary/nearest first).
    all_stations = []
    if catalog_item.links:
        for station in catalog_item.links.nearest_stations:
            if station.name:
                all_stations.append({
                    'name': station.name,
                    'distance': station.distance,
                })
    all_stations.sort(key=lambda s: s['distance'] if s['distance'] is not None else 10 ** 9)
    stations_str = '; '.join(
        (s['name'] + (f' ({s["distance"]} м)' if s['distance'] is not None else ''))
        for s in all_stations[:5]
    )

    # Photos
    photos = []
    for content in catalog_item.external_content:
        if content.main_photo_url:
            photos.append(content.main_photo_url)

    # Rubrics
    primary_rubric, sub_rubrics = rubric_sections(catalog_item)
    rubric_ids = '; '.join(r.id for r in catalog_item.rubrics)
    primary_rubric_id = None
    for r in catalog_item.rubrics:
        if r.kind == 'primary':
            primary_rubric_id = r.id
            break
    rubric_section = rubric_section_name(primary_rubric_id)

    # Attributes
    attributes_str = usable_attributes(catalog_item)

    return {
        'name': name,
        'description': description,
        'rubrics': [r.name for r in catalog_item.rubrics],
        'primary_rubric': primary_rubric,
        'rubric_section': rubric_section,
        'sub_rubrics': sub_rubrics,
        'rubric_ids': rubric_ids,
        'address': catalog_item.address_name,
        'address_comment': catalog_item.address_comment,
        'city': _adm_value(catalog_item, 'city'),
        'district': _adm_value(catalog_item, 'district'),
        'district_area': _adm_value(catalog_item, 'district_area'),
        'region': _adm_value(catalog_item, 'region'),
        'country': _adm_value(catalog_item, 'country'),
        'rating': rating,
        'review_count': review_count,
        'org_rating': org_rating,
        'org_review_count': org_review_count,
        'review_count_with_stars': (catalog_item.reviews.general_review_count_with_stars
                                    if catalog_item.reviews else None),
        'average_check': _average_check(catalog_item),
        'attributes': attributes_str,
        'nearest_station': nearest_station,
        'station_distance': station_distance,
        'stations': stations_str,
        'photos': photos,
        'branch_count': catalog_item.org.branch_count if catalog_item.org else None,
        'email_for_sending': catalog_item.email_for_sending,
        'firm_id': catalog_item.id.split('_')[0] if catalog_item.id else None,
        'org_id': catalog_item.org.id if catalog_item.org else None,
        'contacts': contacts,
        'mobile': mobile_phone,
        'postcode': catalog_item.address.postcode if catalog_item.address else None,
        'contact_comments': contact_comments,
        'url': catalog_item.url,
        'reviews_url': catalog_item.reviews_url,
        # Координаты для пространственных запросов (граф/Geo-рекомендации).
        'point_lat': catalog_item.point.lat if catalog_item.point else None,
        'point_lon': catalog_item.point.lon if catalog_item.point else None,
        # Расписание работы (JSONB: {days: {day: [{from,to}]}, comment, ...}).
        'schedule': _schedule_to_dict(catalog_item.schedule),
        'schedule_comment': _schedule_comment(catalog_item),
    }
