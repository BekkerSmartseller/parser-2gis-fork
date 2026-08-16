from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import webbrowser
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Optional

from litestar.openapi.controller import OpenAPIController
from pydantic import BaseModel, ConfigDict, Field

from ..config import Configuration
from ..logger import logger
from ..paths import user_path
from ..version import version
from ..writer import WriterOptions, get_writer
from .history import History
from .job import JobManager

# Download file names per format.
_DOWNLOAD_NAMES = {'csv': '2gis.csv', 'xlsx': '2gis.xlsx',
                   'json': '2gis.json', 'html': '2gis.html'}

# Country code -> human name (for the link generator).
COUNTRIES = {
    'ru': 'Россия', 'kz': 'Казахстан', 'by': 'Беларусь', 'az': 'Азербайджан',
    'kg': 'Киргизия', 'uz': 'Узбекистан', 'cz': 'Чехия', 'eg': 'Египет',
    'it': 'Италия', 'sa': 'Саудовская Аравия', 'cy': 'Кипр', 'ae': 'ОАЭ',
    'cl': 'Чили', 'qa': 'Катар', 'om': 'Оман', 'bh': 'Бахрейн',
    'kw': 'Кувейт', 'iq': 'Ирак',
}


_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}


def _os_proxy() -> Optional[str]:
    """Прокси из переменных окружения ОС для Chrome (без авторизации).

    Chrome не умеет креды в `--proxy-server` (URL вида
    `http://user:pass@host:port` даёт net::ERR_NO_SUPPORTED_PROXIES, без
    кредов — ERR_INVALID_AUTH_CREDENTIALS). Поэтому прокси с логином/паролем
    пропускаем — Chrome пойдёт напрямую (прямое подключение к 2GIS работает,
    а парсер и так обходит анти-бот). Схему socks:// нормализуем в socks5://
    (иначе Chrome падает с ERR_SOCKS_CONNECTION_FAILED).
    """
    for name in ('https_proxy', 'http_proxy', 'HTTPS_PROXY', 'HTTP_PROXY',
                 'ALL_PROXY', 'all_proxy'):
        val = os.environ.get(name)
        if not val or not val.strip():
            continue
        val = val.strip()
        if val.lower().startswith('socks://'):
            val = 'socks5://' + val[len('socks://'):]
        try:
            parsed = urllib.parse.urlsplit(val)
        except ValueError:
            continue
        if parsed.username or parsed.password:
            # Chrome не поддерживает авторизацию прокси — не пробрасываем.
            continue
        return val
    return None


def _configure_chrome(cfg: Configuration) -> None:
    """Настраивает Chrome для серверных вызовов (geocode/route).

    Headless + системный прокси, если его можно отдать Chrome (без кредов)."""
    proxy = _os_proxy()
    if proxy:
        cfg.chrome.proxy = proxy
    cfg.chrome.headless = True


def _translit_slug(name: str) -> str:
    """Кириллица -> латинский slug для кода города в URL 2GIS."""
    s = (name or '').strip().lower().replace('ё', 'e')
    out = []
    for ch in s:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum():
            out.append(ch)
    return ''.join(out).strip('-_') or name.strip().lower()


class GeocodeRequest(BaseModel):
    """Тело POST /api/geocode: адрес для поиска в 2GIS."""
    model_config = ConfigDict(extra='ignore')

    query: str = Field(description='Адрес или запрос (например «Московский проспект 273»)',
                       examples=['Московский проспект 273'])
    city: Optional[str] = Field(default=None,
                                description='Город-контекст (добавляется в slug URL, например «Калининград»)',
                                examples=['Калининград'])
    lat: Optional[float] = Field(default=None, description='Широта города-якоря (резерв)')
    lon: Optional[float] = Field(default=None, description='Долгота города-якоря (резерв)')


class RouteRequest(BaseModel):
    """Тело POST /api/route: точки маршрута и режим транспорта."""
    model_config = ConfigDict(extra='ignore')

    from_lat: float = Field(description='Широта точки А', examples=[54.71])
    from_lon: float = Field(description='Долгота точки А', examples=[20.51])
    to_lat: float = Field(description='Широта точки Б', examples=[54.72])
    to_lon: float = Field(description='Долгота точки Б', examples=[20.53])
    transport_mode: str = Field(default='car',
                                description='Режим: car / transit / walk / bike',
                                examples=['transit'])
    city: Optional[str] = Field(default=None,
                                description='Город (кириллица или латинский slug), например «kaliningrad»',
                                examples=['kaliningrad'])
    from_id: Optional[str] = Field(default=None,
                                   description='ID точки А в 2GIS (из /api/geocode) — точная привязка',
                                   examples=['111222333444'])
    to_id: Optional[str] = Field(default=None,
                                 description='ID точки Б в 2GIS (из /api/geocode) — точная привязка',
                                 examples=['555666777888'])


# --- Модели запросов POST ---

class FilterOptions(BaseModel):
    """Фильтры результата (секция `filters` в POST /api/start)."""
    model_config = ConfigDict(extra='ignore')

    dedup_franchises: bool = Field(default=False, description='Убирать дубли франшиз (сеть показывать один раз)')
    dedup_across_niches: bool = Field(default=True, description='Убирать дубли между нишами')
    require_phone: bool = Field(default=False, description='Оставлять только организации с телефоном')
    require_whatsapp: bool = Field(default=False, description='Оставлять только с WhatsApp')
    require_social: bool = Field(default=False, description='Оставлять только с соцсетями')
    require_email: bool = Field(default=False, description='Оставлять только с e-mail')
    require_website: bool = Field(default=False, description='Оставлять только с сайтом')
    min_rating: float = Field(default=0.0, ge=0, le=5,
                              description='Минимальный рейтинг (0–5)')
    min_reviews: int = Field(default=0, ge=0,
                             description='Минимальное количество отзывов')


class AdvancedOptions(BaseModel):
    """Расширенные настройки (секция `advanced` в POST /api/start)."""
    model_config = ConfigDict(extra='ignore')

    disable_images: Optional[bool] = Field(default=None, description='Не загружать изображения (быстрее)')
    start_maximized: Optional[bool] = Field(default=None, description='Запускать Chrome развёрнутым')
    memory_limit: Optional[int] = Field(default=None, gt=0,
                                        description='Лимит памяти Chrome, МБ')
    skip_404_response: Optional[bool] = Field(default=None, description='Пропускать ответы 404 2GIS')
    delay_between_clicks: Optional[int] = Field(default=None, ge=0,
                                                description='Пауза между кликами по позициям, мс')
    add_rubrics: Optional[bool] = Field(default=None, description='Добавлять рубрики в CSV')
    add_comments: Optional[bool] = Field(default=None, description='Добавлять комментарии в CSV')
    remove_empty_columns: Optional[bool] = Field(default=None, description='Убирать пустые колонки')
    remove_duplicates: Optional[bool] = Field(default=None, description='Убирать дубли строк')
    columns_per_entity: Optional[int] = Field(default=None, ge=1, le=5,
                                              description='Колонок на сущность (1–5)')
    encoding: Optional[str] = Field(default=None, description='Кодировка файла (utf-8, cp1251, ...)')
    collect_branches: Optional[bool] = Field(
        default=None, description='Собирать все филиалы сетей (со страницы /branches/)')
    skip_seen_firms: Optional[bool] = Field(
        default=None, description='Пропускать организации из прошлых задач (кэш seen_firms)')
    storage: Optional[str] = Field(
        default=None, description='Хранилище: files (по умолчанию) или db '
                                  '(TimescaleDB; кэш запросов, планировщик, синхронизация). '
                                  'Без значения — db, если задан P2GIS_DB_URL')
    cache_ttl_hours: Optional[int] = Field(
        default=None, gt=0, description='TTL кэша запросов в часах (БД-режим; по умолчанию 168 = 7 дней)')
    sync_after: Optional[bool] = Field(
        default=None, description='Синхронизировать собранные данные в целевую схему после завершения задачи (БД-режим)')


class StartRequest(BaseModel):
    """Тело POST /api/start: запуск парсинга."""
    model_config = ConfigDict(extra='ignore')

    urls: list[str] = Field(description='Ссылки на 2GIS (поиск или фирма)',
                            examples=[['https://2gis.ru/kaliningrad/search/фитнес']])
    max_records: int = Field(default=100, gt=0,
                             description='Лимит записей (кликов по позициям выдачи)')
    max_concurrent: Optional[int] = Field(default=None, gt=0,
                                          description='Сколько Chrome одновременно (если не задано — 3)')
    headless: bool = Field(default=True, description='Запускать Chrome в фоне (headless)')
    clean: bool = Field(default=False, description='«Чистый вид» CSV (без пустых колонок)')
    filters: FilterOptions = Field(default_factory=FilterOptions, description='Фильтры результата')
    advanced: AdvancedOptions = Field(default_factory=AdvancedOptions, description='Расширенные настройки')


class JobIdRequest(BaseModel):
    """Тело POST /api/stop и /api/clear."""
    model_config = ConfigDict(extra='ignore')

    job_id: Optional[str] = Field(default=None, description='ID задачи (без него — последняя)',
                                  examples=['ab12cd34ef56'])


class AddCityRequest(BaseModel):
    """Тело POST /api/cities: добавление города в справочник."""
    model_config = ConfigDict(extra='ignore')

    name: str = Field(description='Название города', examples=['Шарья'])
    code: Optional[str] = Field(default=None,
                                description='Код города (латиница) — без него генерируется транслитом',
                                examples=['sharya'])
    domain: str = Field(default='ru', description='Домен страны (ru, kz, by, ...)')
    country_code: str = Field(default='ru', description='Код страны')
    region: Optional[str] = Field(default=None,
                                  description='Регион/область города (для LLM-поиска по области)',
                                  examples=['Костромская область'])


class MergeRequest(BaseModel):
    """Тело POST /api/history/merge: объединение записей истории."""
    model_config = ConfigDict(extra='ignore')

    ids: list[str] = Field(description='ID записей истории для объединения',
                           examples=[['20260811-160924-376519', '20260811-161200-123456']])


class ScheduleRequest(BaseModel):
    """Тело POST/PUT /api/schedules: расписание автообновления."""
    model_config = ConfigDict(extra='ignore')

    name: str = Field(description='Название расписания', examples=['Фитнес: Москва/СПб'])
    cron: Optional[str] = Field(default=None,
                                description='Cron-выражение (например "0 3 * * *")')
    interval_minutes: Optional[int] = Field(default=None, ge=1,
                                            description='Или простой интервал в минутах')
    cities: list[str] = Field(default_factory=list, description='Коды городов (slug)')
    rubrics: list[str] = Field(default_factory=list, description='Коды рубрик (rubricId)')
    queries: list[str] = Field(default_factory=list, description='Текстовые запросы')
    max_concurrent: Optional[int] = Field(default=None, gt=0,
                                          description='Сколько Chrome одновременно')
    ttl_hours: Optional[int] = Field(default=None, gt=0, description='TTL кэша, часов')
    sync_after: bool = Field(default=True,
                             description='Синхронизировать в целевую схему после задачи')
    enabled: bool = Field(default=True, description='Расписание активно')


class SyncRequest(BaseModel):
    """Тело POST /api/sync: фильтры синхронизации организаций."""
    model_config = ConfigDict(extra='ignore')

    since: Optional[str] = Field(default=None, description='ISO-метка: синхронизировать с неё')
    limit: Optional[int] = Field(default=None, gt=0, description='Максимум записей за вызов')
    city: Optional[str] = Field(default=None, description='Город (код или имя)')
    rubric_id: Optional[str] = Field(default=None, description='Рубрика (rubricId)')
    deactivate: bool = Field(default=True,
                             description='Деактивировать филиалы сети, отсутствующие в наборе')
    sync_prices: bool = Field(default=True,
                              description='Дополнительно синхронизировать прайс-каталог '
                                          '(p2gis.branch_prices -> целевая схема)')


class PricesRequest(BaseModel):
    """Тело POST /api/prices: загрузка прайс-каталога фирм с market API."""
    model_config = ConfigDict(extra='ignore')

    firm_ids: list[str] = Field(
        description='firm_id филиалов (branch_id в 2GIS)', min_length=1,
        examples=[['9148465024074680']])
    locale: str = Field(default='ru_RU', description='Локаль (ru_RU)')
    delay: Optional[float] = Field(default=None, ge=0,
                                   description='Пауза между фирмами (сек)')


class PricesReadRequest(BaseModel):
    """Тело POST /api/prices/read: чтение прайса из БД."""
    model_config = ConfigDict(extra='ignore')

    firm_id: str = Field(description='firm_id филиала', examples=['9148465024074680'])
    limit: Optional[int] = Field(default=None, ge=1, le=5000,
                                 description='Максимум позиций')


# --- Модели ответов ---

class OkResponse(BaseModel):
    """Универсальный ответ `{ok: bool}`."""
    ok: bool = Field(description='Успех операции')


class StartOk(BaseModel):
    ok: bool = Field(description='Всегда true')
    job_id: str = Field(description='ID запущенной задачи', examples=['ab12cd34ef56'])


class MergeOk(BaseModel):
    ok: bool = Field(description='Всегда true')
    id: str = Field(description='ID новой объединённой записи',
                    examples=['20260814-210500-654321'])
    count: int = Field(description='Количество записей после объединения', examples=[40])


class JobInfo(BaseModel):
    id: str = Field(description='ID задачи', examples=['ab12cd34ef56'])
    status: str = Field(description='queued | running | done | stopped | error | idle')
    count: int = Field(description='Собранные записи', examples=[97])


class JobsResponse(BaseModel):
    jobs: list[JobInfo] = Field(description='Список задач')


class StatusResponse(BaseModel):
    job_id: str = Field(description='ID задачи')
    status: str = Field(description='queued | running | done | stopped | error')
    running: bool = Field(description='Выполняется ли сейчас')
    count: int = Field(description='Собранные записи')
    error: Optional[str] = Field(description='Текст ошибки (если была)')
    logs: list[str] = Field(description='Журнал задачи (начиная с cursor)')
    cursor: int = Field(description='Следующий offset для продолжения чтения логов')


class ResultsResponse(BaseModel):
    records: list[dict[str, Any]] = Field(description='Записи результата (динамические колонки)')


class CountryInfo(BaseModel):
    code: str = Field(description='Код страны', examples=['ru'])
    name: str = Field(description='Название страны', examples=['Россия'])


class CityInfo(BaseModel):
    name: str = Field(description='Название города', examples=['Шарья'])
    code: str = Field(description='Код города (slug)', examples=['sharya'])
    domain: str = Field(description='Домен страны', examples=['ru'])
    country_code: str = Field(description='Код страны', examples=['ru'])
    region: Optional[str] = Field(default=None, description='Регион/область города',
                                  examples=['Костромская область'])


class RubricInfo(BaseModel):
    code: str = Field(description='ID рубрики', examples=['fitness_club'])
    label: str = Field(description='Название рубрики', examples=['Фитнес-клубы'])
    is_russian: bool = Field(description='Показывать в российских городах')
    is_non_russian: bool = Field(description='Показывать в зарубежных городах')
    group: str = Field(description='Верхнеуровневая рубрика-группа', examples=['Спорт'])


class GeneratorResponse(BaseModel):
    countries: list[CountryInfo] = Field(description='Страны')
    cities: list[CityInfo] = Field(description='Города (базовые + добавленные)')
    rubrics: list[RubricInfo] = Field(description='Рубрики')


class CitiesResponse(BaseModel):
    cities: list[CityInfo] = Field(description='Города (базовые + добавленные)')


class CityAddOk(BaseModel):
    ok: bool = Field(description='Всегда true')
    city: CityInfo = Field(description='Добавленный/найденный город')


class HistoryItem(BaseModel):
    id: str = Field(description='ID записи', examples=['20260814-210000-123456'])
    created_at: Optional[str] = Field(description='Дата создания (ISO)')
    urls: list[str] = Field(description='Ссылки парсинга')
    count: int = Field(description='Количество записей')


class HistoryResponse(BaseModel):
    items: list[HistoryItem] = Field(description='Записи истории (новые сверху)')


class RefreshResponse(BaseModel):
    ok: bool = Field(description='Успех обновления')
    status: str = Field(description='ok | skipped | busy | error')
    cities: Optional[int] = Field(default=None, description='Сколько городов получено')
    rubrics: Optional[int] = Field(default=None, description='Сколько рубрик получено')
    updated_at: Optional[str] = Field(default=None, description='Время обновления (ISO)')
    error: Optional[str] = Field(default=None, description='Текст ошибки (если была)')


def _custom_cities_path() -> Path:
    """Файл пользовательских городов (добавленных через API)."""
    path = user_path(False) / 'cities_custom.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def _load_custom_cities() -> list[dict[str, Any]]:
    p = _custom_cities_path()
    if not p.exists():
        return []
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _db_enabled() -> bool:
    """БД-режим доступен (P2GIS_DB_URL задан и БД отвечает)."""
    try:
        from ..db import enabled
        return enabled()
    except Exception:  # noqa: BLE001
        return False


def _save_custom_cities(entries: list[dict[str, Any]]) -> None:
    p = _custom_cities_path()
    tmp = p.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    _load_custom_cities.cache_clear()
    _load_cities.cache_clear()


def _add_city(name: str, code: str | None = None, domain: str = 'ru',
              country_code: str = 'ru', region: str | None = None) -> dict[str, Any]:
    """Добавляет город в список (base + custom). Идемпотентно по code/имени.

    В БД-режиме город также пишется в p2gis.cities (source='custom').
    """
    name = (name or '').strip()
    if not name:
        raise ValueError('name required')
    code = (code or _translit_slug(name)).strip()
    domain = (domain or 'ru').strip()
    country_code = (country_code or 'ru').strip()
    region = (region or '').strip() or None

    # дедуп: уже есть в base или custom?
    all_cities = _load_cities()
    for c in all_cities:
        if c.get('code') == code or c.get('name', '').strip().lower() == name.lower():
            if region and not c.get('region'):
                c = dict(c)
                c['region'] = region
            return dict(c)

    entry = {'name': name, 'code': code, 'domain': domain, 'country_code': country_code}
    if region:
        entry['region'] = region

    # БД-режим: upsert в p2gis.cities (source='custom'), кэш сбрасывается.
    if _db_enabled():
        try:
            from .refdata import save_cities_db
            save_cities_db([entry], source='custom')
            _load_cities.cache_clear()
        except Exception as e:  # noqa: BLE001
            logger.warning('[server] не удалось сохранить город в БД: %s', e)

    custom = _load_custom_cities()
    for c in custom:
        if c.get('code') == code:
            return dict(c)
    custom.append(entry)
    _save_custom_cities(custom)
    return dict(entry)


@lru_cache(maxsize=1)
def _load_cities() -> list[dict[str, Any]]:
    """Города для генератора/поиска. БД-режим: из p2gis.cities (вкл. custom).
    Файловый режим: base cities.json + пользовательские cities_custom.json."""
    from .refdata import load_cities_list
    cities = load_cities_list()
    if _db_enabled():
        return cities
    # добавляем пользовательские города (без дублей по code)
    seen = {c.get('code') for c in cities if c.get('code')}
    for c in _load_custom_cities():
        if c.get('code') and c['code'] not in seen:
            cities.append(c)
            seen.add(c['code'])
    return cities


@lru_cache(maxsize=1)
def _load_rubrics() -> list[dict[str, Any]]:
    """Flat list of rubrics for the web generator picker. БД-режим: из p2gis.rubrics."""
    from .refdata import load_rubrics_dict
    return _flatten_rubrics(load_rubrics_dict())


def _flatten_rubrics(rubrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Рубрикатор {code: node} -> плоский список для генератора ссылок."""

    def top_group(node: dict[str, Any]) -> str:
        """Верхнеуровневая рубрика-группа (parentCode '0'), которой принадлежит node."""
        cur = node
        seen: set[str] = set()
        while cur:
            code = str(cur.get('code') or '')
            parent = str(cur.get('parentCode') or '0')
            if parent == '0':
                return cur.get('label') or ''
            if code in seen:
                break
            seen.add(code)
            cur = rubrics.get(parent)
        return node.get('label') or ''

    out = []
    for node in rubrics.values():
        # Skip the synthetic root and group headers without a usable label.
        if node.get('code') in (None, '0') or not node.get('label'):
            continue
        out.append({
            'code': node['code'],
            'label': node['label'],
            'parent_code': str(node.get('parentCode') or '0'),
            'is_russian': bool(node.get('isRussian', True)),
            'is_non_russian': bool(node.get('isNonRussian', True)),
            'group': top_group(node),
        })
    out.sort(key=lambda r: r['label'].lower())
    return out


def _build_config(data: dict[str, Any]) -> Configuration:
    """Build a Configuration from the web request payload."""
    config = Configuration()
    config.chrome.headless = bool(data.get('headless', True))
    config.parser.max_records = max(1, int(data.get('max_records', 100)))
    # Default to the full column set; "clean view" is an explicit opt-in.
    config.writer.csv.clean = bool(data.get('clean', False))

    # Concurrent jobs / proxies (per request).
    if data.get('max_concurrent'):
        config.parser.max_concurrent = max(1, int(data['max_concurrent']))

    adv = data.get('advanced', {}) or {}
    if adv:
        config.chrome.disable_images = bool(adv.get('disable_images', config.chrome.disable_images))
        config.chrome.start_maximized = bool(adv.get('start_maximized', config.chrome.start_maximized))
        if adv.get('memory_limit'):
            config.chrome.memory_limit = max(1, int(adv['memory_limit']))
        config.parser.skip_404_response = bool(adv.get('skip_404_response', config.parser.skip_404_response))
        config.parser.delay_between_clicks = max(0, int(adv.get('delay_between_clicks', 0) or 0))
        config.writer.csv.add_rubrics = bool(adv.get('add_rubrics', config.writer.csv.add_rubrics))
        config.writer.csv.add_comments = bool(adv.get('add_comments', config.writer.csv.add_comments))
        config.writer.csv.remove_empty_columns = bool(adv.get('remove_empty_columns', config.writer.csv.remove_empty_columns))
        config.writer.csv.remove_duplicates = bool(adv.get('remove_duplicates', config.writer.csv.remove_duplicates))
        if adv.get('columns_per_entity'):
            config.writer.csv.columns_per_entity = min(5, max(1, int(adv['columns_per_entity'])))
        if adv.get('encoding'):
            config.writer.encoding = str(adv['encoding'])
        if adv.get('collect_branches') is not None:
            config.parser.collect_branches = bool(adv['collect_branches'])
        if adv.get('skip_seen_firms') is not None:
            config.parser.skip_seen_firms = bool(adv['skip_seen_firms'])
        if adv.get('storage') is not None:
            config.parser.storage = str(adv['storage'])
        if adv.get('cache_ttl_hours') is not None:
            config.parser.cache_ttl_hours = max(1, int(adv['cache_ttl_hours']))
        if adv.get('sync_after') is not None:
            config.parser.sync_after = bool(adv['sync_after'])

    # Хранилище по умолчанию: db, если задан P2GIS_DB_URL (иначе files).
    # Явный advanced.storage имеет приоритет; «Авто» в UI = это правило.
    if adv.get('storage') is None and _db_enabled():
        config.parser.storage = 'db'
    if config.parser.storage not in ('db', 'files'):
        config.parser.storage = 'files'
    if config.parser.storage == 'db' and not _db_enabled():
        # БД недоступна (нет P2GIS_DB_URL или пул упал) — откат на файлы.
        config.parser.storage = 'files'

    f = data.get('filters', {}) or {}
    config.filters.dedup_franchises = bool(f.get('dedup_franchises'))
    config.filters.dedup_across_niches = bool(f.get('dedup_across_niches', True))
    config.filters.require_phone = bool(f.get('require_phone'))
    config.filters.require_whatsapp = bool(f.get('require_whatsapp'))
    config.filters.require_social = bool(f.get('require_social'))
    config.filters.require_email = bool(f.get('require_email'))
    config.filters.require_website = bool(f.get('require_website'))
    config.filters.min_rating = float(f.get('min_rating', 0) or 0)
    config.filters.min_reviews = int(f.get('min_reviews', 0) or 0)
    return config


def _export_response(docs, writer_opts: WriterOptions, fmt: str) -> Any:
    """Write `docs` to a temp file in `fmt` and return a FileResponse."""
    from litestar.response import File

    tmp_dir = tempfile.mkdtemp(prefix='p2gis_web_')
    out_path = os.path.join(tmp_dir, _DOWNLOAD_NAMES[fmt])
    with get_writer(out_path, fmt, writer_opts) as writer:
        for doc in docs:
            writer.write(doc)
    return File(path=out_path, filename=_DOWNLOAD_NAMES[fmt])


@lru_cache(maxsize=1)
def _static_dir() -> Path:
    return Path(__file__).with_name('static')


class _LocalDocsController(OpenAPIController):
    """OpenAPI-контроллер с локальным ReDoc-бандлом (без CDN).

    По умолчанию Litestar грузит ReDoc с cdn.redoc.ly, который в этом окружении
    из браузера недоступен -> страница /schema/redoc пустая. Отдаём
    redoc.standalone.js из собственного /static и не тянем Google Fonts.
    """
    redoc_js_url = '/static/redoc.standalone.js'
    redoc_google_fonts = False


def create_app():
    """Create the Litestar app for the dashboard."""
    from litestar import Litestar, delete, get, post, put
    from litestar.exceptions import HTTPException
    from litestar.openapi import OpenAPIConfig, ResponseSpec
    from litestar.openapi.spec import Example
    from litestar.params import Body, PathParameter, QueryParameter
    from litestar.response import Response
    from litestar.static_files.config import StaticFilesConfig

    static_dir = _static_dir()
    jobs = JobManager(max_concurrent=3)
    history = History()
    from ..db.scheduler import Scheduler
    scheduler = Scheduler(jobs)

    def _err(msg: str, code: int = 400) -> Any:
        """JSON error response (Litestar does not support (body, status) tuples)."""
        return Response(content=json.dumps({'ok': False, 'error': msg}),
                        media_type='application/json', status_code=code)

    @get('/', sync_to_thread=True, summary='Дашборд', description='Главная страница веб-интерфейса')
    def index() -> Any:
        return Response(content=(static_dir / 'index.html').read_bytes(),
                        media_type='text/html')

    @post('/api/start', status_code=200, sync_to_thread=True, summary='Запустить парсинг',
          description='Запускает фоновый парсинг ссылок 2GIS. '
                      'urls — ссылки (поиск или фирма); max_records — лимит записей; '
                      'max_concurrent — сколько Chrome одновременно; filters/advanced — '
                      'фильтры и расширенные настройки. Ответ: {ok, job_id}. '
                      'При невалидном/неполном теле — 400, при превышении лимита задач — 409.',
          responses={
              200: ResponseSpec(StartOk, description='OK: задача запущена',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': True, 'job_id': 'ab12cd34ef56'})]),
              400: ResponseSpec(dict, description='Невалидное тело/нет ссылок',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': 'Не указаны ссылки'})]),
              409: ResponseSpec(dict, description='Превышен лимит задач',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': '...'})]),
              500: ResponseSpec(dict, description='Ошибка сервера',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': '...'})]),
          })
    def api_start(data: StartRequest = Body(
        title='Параметры парсинга',
        description='urls обязателен; остальные параметры опциональны.',
        examples=[Example(value={'urls': ['https://2gis.ru/kaliningrad/search/фитнес'],
                                 'max_records': 100, 'max_concurrent': 3})])) -> Any:
        urls = [u.strip() for u in (data.urls or []) if u and u.strip()]
        if not urls:
            return _err('Не указаны ссылки')
        try:
            config = _build_config(data.model_dump())
            # Update worker concurrency on the fly from the request's max_concurrent.
            # БД-режим: если все URL уже «свежие» в request_cache — отдаём из БД,
            # не запуская Chrome (кэш-задача читает p2gis.records).
            if config.parser.storage == 'db':
                from ..db import cache as db_cache
                fingerprints = [db_cache.fingerprint_for_url(u) for u in urls]
                if all(f is not None for f in fingerprints):
                    ttl = config.parser.cache_ttl_hours or None
                    status = db_cache.request_status(
                        [f['fingerprint'] for f in fingerprints], ttl)
                    if all(status.get(f['fingerprint'], {}).get('fresh')
                           for f in fingerprints):
                        job_id = jobs.start(config, urls, fingerprints=fingerprints,
                                            cache_hit=True)
                        return {'ok': True, 'job_id': job_id, 'cache_hit': True}
            job_id = jobs.start(config, urls)
        except RuntimeError as e:
            return _err(str(e), 409)
        except Exception as e:
            logger.error('Не удалось запустить парсинг: %s', e)
            return _err(str(e))
        return {'ok': True, 'job_id': job_id}

    @post('/api/refresh', status_code=200, sync_to_thread=True, summary='Обновить справочники 2GIS',
          description='Перезагружает cities.json и rubrics.json из data.2gis.com '
                      '(Chrome + перехват API). Обновлённые файлы сохраняются в '
                      'user refdata и используются загрузчиками сразу. При '
                      'одновременном обновлении возвращает status=busy.',
          responses={
              200: ResponseSpec(RefreshResponse, description='OK',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={
                                    'ok': True, 'status': 'ok', 'cities': 204,
                                    'rubrics': 1786,
                                    'updated_at': '2026-08-16T01:00:00+00:00'})]),
              500: ResponseSpec(dict, description='Ошибка обновления',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={
                                    'ok': False, 'status': 'error', 'error': '...'})]),
          })
    def api_refresh() -> Any:
        from .refdata import refresh_reference_data
        return refresh_reference_data(force=True)

    @post('/api/geocode', status_code=200, sync_to_thread=True, summary='Геокодинг адреса через 2GIS',
          description='Открывает поиск 2GIS по адресу (со slug города), перехватывает '
                      'catalog API (markers/clustered) и возвращает координаты лучшего '
                      'результата + id объекта 2GIS (для точной привязки точек маршрута). '
                      'При невалидном/неполном теле — 400.',
          responses={
              200: ResponseSpec(dict, description='OK: координаты + id',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={
                                    'ok': True, 'lat': 54.71, 'lon': 20.51,
                                    'name': 'Московский проспект, 273',
                                    'address': None, 'id': '111222333444'})]),
              404: ResponseSpec(dict, description='Не найдено',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={
                                    'ok': False,
                                    'error': '2GIS не нашёл адрес (нет точных совпадений)'})]),
              500: ResponseSpec(dict, description='Ошибка сервера',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': '...'})]),
          })
    def api_geocode(data: GeocodeRequest = Body(
        title='Параметры геокодинга',
        description='query — адрес/запрос; city/lat/lon — контекст города.',
        examples=[Example(value={'query': 'Московский проспект 273',
                                 'city': 'Калининград'})])) -> Any:
        if not (data.query or '').strip():
            return _err('query обязателен')
        try:
            from ..parser.geocoder import Geocoder
            cfg = Configuration()
            _configure_chrome(cfg)
            with Geocoder(cfg.chrome) as geocoder:
                point = geocoder.geocode(data.query, city=data.city,
                                         city_lat=data.lat, city_lon=data.lon,
                                         timeout=45)
        except Exception as e:
            logger.error('Ошибка геокодинга: %s', e)
            return _err(str(e), 500)
        if not point:
            return _err('2GIS не нашёл адрес (нет точных совпадений)', 404)
        return {'ok': True, **point}

    @post('/api/route', status_code=200, sync_to_thread=True, summary='Построить маршрут через 2GIS',
          description='Открывает страницу directions 2GIS в Chrome и парсит SSR-итинерарий '
                      '(маршруты 2GIS рендерит на сервере). transport_mode: car/transit/walk/bike. '
                      'from_id/to_id — ID точек 2GIS из /api/geocode (формат lon,lat;ID, точная '
                      'привязка). Ответ: mode, duration_s, distance_m, segments, variants. '
                      'Если 2GIS не смог — код 404. При невалидном/неполном теле — 400.',
          responses={
              200: ResponseSpec(dict, description='OK: маршрут',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={
                                    'ok': True, 'mode': 'transit', 'duration_s': 3660,
                                    'distance_m': None, 'walk_duration_s': 1380,
                                    'transfers': 0,
                                    'segments': [
                                        {'type': 'walk', 'mode': 'walk', 'route': '',
                                         'name': 'Пешком', 'duration_s': None,
                                         'from': '', 'to': ''},
                                        {'type': 'bus', 'mode': 'bus', 'route': '28',
                                         'name': 'Автобус: 28', 'duration_s': 960,
                                         'from': '', 'to': ''},
                                        {'type': 'walk', 'mode': 'walk', 'route': '',
                                         'name': 'Пешком', 'duration_s': None,
                                         'from': '', 'to': ''},
                                    ],
                                    'variants': [
                                        {'mode': 'transit', 'duration_s': 3660,
                                         'distance_m': None, 'walk_duration_s': 1380,
                                         'transfers': 0, 'segments': []},
                                    ]})]),
              404: ResponseSpec(dict, description='Маршрут не построен',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={
                                    'ok': False,
                                    'error': '2GIS не построил маршрут (нет данных/недоступен)'})]),
              500: ResponseSpec(dict, description='Ошибка сервера',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': '...'})]),
          })
    def api_route(data: RouteRequest = Body(
        title='Параметры маршрута',
        description='from_lat/from_lon/to_lat/to_lon — координаты; '
                    'transport_mode: car/transit/walk/bike; '
                    'from_id/to_id — ID точек 2GIS из /api/geocode.',
        examples=[Example(value={'from_lat': 54.71, 'from_lon': 20.51,
                                 'to_lat': 54.72, 'to_lon': 20.53,
                                 'transport_mode': 'transit', 'city': 'kaliningrad',
                                 'from_id': '111222333444',
                                 'to_id': '555666777888'})])) -> Any:
        transport_mode = (data.transport_mode or 'car').strip().lower()
        try:
            from ..parser.router import RouteBuilder
            cfg = Configuration()
            _configure_chrome(cfg)
            with RouteBuilder(cfg.chrome) as builder:
                route = builder.build(
                    data.from_lat, data.from_lon, data.to_lat, data.to_lon,
                    transport_mode=transport_mode, city=data.city,
                    from_id=data.from_id, to_id=data.to_id, timeout=60)
        except Exception as e:
            logger.error('Ошибка маршрута: %s', e)
            return _err(str(e), 500)
        if not route:
            return _err('2GIS не построил маршрут (нет данных/недоступен)', 404)
        return {'ok': True, **route}

    @post('/api/stop', status_code=200, sync_to_thread=True, summary='Остановить задачу',
          description='Останавливает задачу (job_id в теле; без job_id — последнюю). '
                      'При невалидном/неполном теле — 400, если задачи нет — 404.',
          responses={
              200: ResponseSpec(OkResponse, description='OK',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': True})]),
              400: ResponseSpec(dict, description='Невалидное тело',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': '...'})]),
              404: ResponseSpec(dict, description='Задача не найдена',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': 'Задача не найдена'})]),
          })
    def api_stop(
        data: JobIdRequest = Body(
            title='Параметры',
            description='job_id — ID задачи (опционально, без него — последняя).',
            examples=[Example(value={'job_id': 'ab12cd34ef56'})],
        ),
    ) -> Any:
        if data.job_id and data.job_id not in jobs._jobs:
            return _err('Задача не найдена', 404)
        return {'ok': jobs.stop(data.job_id)}

    @post('/api/pause', status_code=200, sync_to_thread=True, summary='Поставить задачу на паузу',
          description='Пауза между URL: текущая страница дорабатывает, следующие ссылки '
                      'не начнутся до /api/resume. job_id в теле; без job_id — последняя задача.',
          responses={
              200: ResponseSpec(OkResponse, description='OK',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': True})]),
              400: ResponseSpec(dict, description='Невалидное тело',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': '...'})]),
              404: ResponseSpec(dict, description='Задача не найдена',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': 'Задача не найдена'})]),
          })
    def api_pause(
        data: JobIdRequest = Body(
            title='Параметры',
            description='job_id — ID задачи (опционально, без него — последняя).',
            examples=[Example(value={'job_id': 'ab12cd34ef56'})],
        ),
    ) -> Any:
        if data.job_id and data.job_id not in jobs._jobs:
            return _err('Задача не найдена', 404)
        return {'ok': jobs.pause(data.job_id)}

    @post('/api/resume', status_code=200, sync_to_thread=True, summary='Снять паузу с задачи',
          description='Возобновляет парсинг после /api/pause. job_id в теле; без job_id — '
                      'последняя задача.',
          responses={
              200: ResponseSpec(OkResponse, description='OK',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': True})]),
              400: ResponseSpec(dict, description='Невалидное тело',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': '...'})]),
              404: ResponseSpec(dict, description='Задача не найдена',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': 'Задача не найдена'})]),
          })
    def api_resume(
        data: JobIdRequest = Body(
            title='Параметры',
            description='job_id — ID задачи (опционально, без него — последняя).',
            examples=[Example(value={'job_id': 'ab12cd34ef56'})],
        ),
    ) -> Any:
        if data.job_id and data.job_id not in jobs._jobs:
            return _err('Задача не найдена', 404)
        return {'ok': jobs.resume(data.job_id)}

    @post('/api/clear', status_code=200, sync_to_thread=True, summary='Очистить задачу',
          description='Очищает результат задачи (job_id в теле; без job_id — последнюю). '
                      'При невалидном/неполном теле — 400.',
          responses={
              200: ResponseSpec(OkResponse, description='OK',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': True})]),
              400: ResponseSpec(dict, description='Невалидное тело',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': '...'})]),
          })
    def api_clear(
        data: JobIdRequest = Body(
            title='Параметры',
            description='job_id — ID задачи (опционально, без него — последняя).',
            examples=[Example(value={'job_id': 'ab12cd34ef56'})],
        ),
    ) -> Any:
        return {'ok': jobs.clear(data.job_id)}

    @get('/api/jobs', sync_to_thread=True, summary='Список задач',
         description='Все задачи с id, статусом и числом записей.',
         responses={
             200: ResponseSpec(JobsResponse, description='OK',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={'jobs': [
                                   {'id': 'ab12cd34ef56', 'status': 'done', 'count': 97},
                                   {'id': 'cd34ef56ab12', 'status': 'running', 'count': 12},
                               ]})]),
         })
    def api_jobs() -> Any:
        return {'jobs': jobs.list_jobs()}

    @get('/api/status', sync_to_thread=True, summary='Статус задачи',
         description='Прогресс задачи: статус, записи, журнал с позиции cursor. '
                     'Без job_id — последняя задача. Если задачи нет — 404.',
         responses={
             200: ResponseSpec(StatusResponse, description='OK',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={
                                   'job_id': 'ab12cd34ef56', 'status': 'done',
                                   'running': False, 'count': 97, 'error': None,
                                   'logs': ['21:00:00 | Парсинг запущен.',
                                            '21:00:05 | Парсинг завершён.'],
                                   'cursor': 2})]),
             404: ResponseSpec(dict, description='Задача не найдена',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={
                                   'ok': False, 'error': 'Задача не найдена'})]),
         })
    def api_status(
        cursor: Annotated[int, QueryParameter(default=0, description='Offset чтения журнала')] = 0,
        job_id: Annotated[Optional[str], QueryParameter(
            default=None, description='ID задачи (без него — последняя)')] = None,
    ) -> Any:
        job = jobs.get(job_id)
        if not job:
            return _err('Задача не найдена', 404)
        logs = job.logs[cursor:]
        return {
            'job_id': job.id,
            'status': job.status,
            'running': job.running,
            'paused': job.paused,
            'count': job.count,
            'error': job.error,
            'logs': logs,
            'cursor': cursor + len(logs),
        }

    @get('/api/results', sync_to_thread=True, summary='Результаты задачи',
         description='Записи результата задачи (динамические колонки). '
                     'Без job_id — последняя задача. Если задачи нет — 404.',
         responses={
             200: ResponseSpec(ResultsResponse, description='OK',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={'records': [
                                   {'name': 'Фитнес-клуб', 'address': 'Калининград, Московский проспект, 273',
                                    'url': 'https://2gis.ru/kaliningrad/firm/70000001000000000'},
                               ]})]),
             404: ResponseSpec(dict, description='Задача не найдена',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={
                                   'ok': False, 'error': 'Задача не найдена'})]),
         })
    def api_results(
        job_id: Annotated[Optional[str], QueryParameter(
            default=None, description='ID задачи (без него — последняя)')] = None,
    ) -> Any:
        job = jobs.get(job_id)
        if not job:
            return _err('Задача не найдена', 404)
        return {'records': job.results()}

    @get('/api/generator', sync_to_thread=True, summary='Данные генератора ссылок',
         description='Страны, города (базовые + добавленные) и рубрики для конструктора ссылок.',
         responses={
             200: ResponseSpec(GeneratorResponse, description='OK',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={
                                   'countries': [{'code': 'ru', 'name': 'Россия'}],
                                   'cities': [{'name': 'Шарья', 'code': 'sharya',
                                               'domain': 'ru', 'country_code': 'ru'}],
                                   'rubrics': [{'code': 'fitness_club', 'label': 'Фитнес-клубы',
                                                'is_russian': True, 'is_non_russian': True,
                                                'group': 'Спорт'}],
                               })]),
         })
    def api_generator() -> Any:
        """Data for the link generator: countries, cities, rubrics."""
        cities = [
            {'name': c['name'], 'code': c['code'], 'domain': c['domain'],
             'country_code': c['country_code'], 'region': c.get('region')}
            for c in _load_cities()
        ]
        countries = [{'code': k, 'name': v} for k, v in COUNTRIES.items()]
        countries.sort(key=lambda c: c['name'])
        return {'countries': countries, 'cities': cities, 'rubrics': _load_rubrics()}

    @post('/api/cities', status_code=200, sync_to_thread=True, summary='Добавить город',
          description='Добавляет город в справочник (если отсутствует), идемпотентно по '
                      'code/имени. code необязателен — генерируется транслитом. '
                      'При невалидном/неполном теле — 400.',
          responses={
              200: ResponseSpec(CityAddOk, description='OK: город',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': True, 'city': {
                                    'name': 'Шарья', 'code': 'sharya',
                                    'domain': 'ru', 'country_code': 'ru'}})]),
              400: ResponseSpec(dict, description='Нет названия / невалидное тело',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={
                                    'ok': False, 'error': 'Название города обязательно'})]),
          })
    def api_add_city(
        data: AddCityRequest = Body(
            title='Город',
            description='name — обязателен; code генерируется транслитом, если не задан.',
            examples=[Example(value={'name': 'Шарья', 'code': 'sharya'})],
        ),
    ) -> Any:
        try:
            city = _add_city(
                name=data.name,
                code=(data.code or '').strip() or None,
                domain=data.domain,
                country_code=data.country_code,
                region=data.region,
            )
        except ValueError as e:
            return _err(str(e), 400)
        return {'ok': True, 'city': city}

    @get('/api/cities', sync_to_thread=True, summary='Список городов',
         description='Города: базовый справочник + добавленные через API.',
         responses={
             200: ResponseSpec(CitiesResponse, description='OK',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={'cities': [
                                   {'name': 'Шарья', 'code': 'sharya',
                                    'domain': 'ru', 'country_code': 'ru',
                                    'region': 'Костромская область'}]})]),
         })
    def api_cities() -> Any:
        return {'cities': [
            {'name': c['name'], 'code': c['code'], 'domain': c['domain'],
             'country_code': c['country_code'], 'region': c.get('region')}
            for c in _load_cities()
        ]}

    @get('/api/download', sync_to_thread=True, summary='Скачать результат',
         description='Скачивает результат задачи файлом. format: csv/xlsx/json/html. '
                     'Без job_id — последняя задача. Неизвестный формат — 400, нет данных — 404.',
         responses={
             200: ResponseSpec(None, description='Файл результата (CSV/XLSX/JSON/HTML)',
                               media_type='application/octet-stream'),
             400: ResponseSpec(dict, description='Неизвестный формат',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={'ok': False, 'error': 'Неизвестный формат'})]),
             404: ResponseSpec(dict, description='Нет данных',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={'ok': False, 'error': 'Нет данных'})]),
         })
    def api_download(
        format: Annotated[str, QueryParameter(default='csv', description='Формат: csv / xlsx / json / html')] = 'csv',
        job_id: Annotated[Optional[str], QueryParameter(
            default=None, description='ID задачи (без него — последняя)')] = None,
    ) -> Any:
        if format not in _DOWNLOAD_NAMES:
            return _err('Неизвестный формат')
        job = jobs.get(job_id)
        if not job or not job.collector:
            return _err('Нет данных')
        try:
            docs = job.export_docs()
            if not docs:
                return _err('Нет данных')
            return _export_response(docs, job.collector._options, format)
        except Exception as e:
            logger.error('Ошибка экспорта: %s', e)
            return _err(str(e), 500)

    @get('/api/history', sync_to_thread=True, summary='История парсингов',
         description='Сохранённые парсинги (новые сверху).',
         responses={
             200: ResponseSpec(HistoryResponse, description='OK',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={'items': [
                                   {'id': '20260814-210000-123456',
                                    'created_at': '2026-08-14T21:00:00',
                                    'urls': ['https://2gis.ru/kaliningrad/search/фитнес'],
                                    'count': 25}]})]),
         })
    def api_history() -> Any:
        return {'items': history.list()}

    @get('/api/history/{hid:str}/results', sync_to_thread=True, summary='Записи из истории',
         description='Записи сохранённого парсинга. Если записи нет — 404.',
         responses={
             200: ResponseSpec(ResultsResponse, description='OK',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={'records': [
                                   {'name': 'Фитнес-клуб', 'address': 'Калининград, Московский проспект, 273'},
                               ]})]),
             404: ResponseSpec(dict, description='Запись не найдена',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={
                                   'ok': False, 'error': 'Запись не найдена'})]),
         })
    def api_history_results(hid: Annotated[str, PathParameter(description='ID записи (YYYYMMDD-HHMMSS-ffffff)')]) -> Any:
        docs = history.docs(hid)
        if docs is None:
            return _err('Запись не найдена', 404)
        return {'records': history.records(hid)}

    @get('/api/history/{hid:str}/download', sync_to_thread=True, summary='Скачать из истории',
         description='Скачивает сохранённый парсинг файлом. format: csv/xlsx/json/html. '
                     'Неизвестный формат — 400, записи нет — 404.',
         responses={
             200: ResponseSpec(None, description='Файл результата (CSV/XLSX/JSON/HTML)',
                               media_type='application/octet-stream'),
             400: ResponseSpec(dict, description='Неизвестный формат',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={'ok': False, 'error': 'Неизвестный формат'})]),
             404: ResponseSpec(dict, description='Запись не найдена',
                               media_type='application/json', generate_examples=False,
                               examples=[Example(value={
                                   'ok': False, 'error': 'Запись не найдена'})]),
         })
    def api_history_download(
        hid: Annotated[str, PathParameter(description='ID записи (YYYYMMDD-HHMMSS-ffffff)')],
        format: Annotated[str, QueryParameter(default='csv', description='Формат: csv / xlsx / json / html')] = 'csv',
    ) -> Any:
        if format not in _DOWNLOAD_NAMES:
            return _err('Неизвестный формат')
        docs = history.docs(hid)
        if not docs:
            return _err('Запись не найдена', 404)
        try:
            opts = WriterOptions(**history.writer_options(hid))
        except Exception:
            opts = WriterOptions()
        try:
            return _export_response(docs, opts, format)
        except Exception as e:
            logger.error('Ошибка экспорта истории: %s', e)
            return _err(str(e), 500)

    @post('/api/history/merge', status_code=200, sync_to_thread=True, summary='Объединить парсинги',
          description='Объединяет записи истории (дедуп по телефону/ID фирмы) в новую запись. '
                      'ids обязателен. При невалидном/неполном теле — 400, нет данных — 400.',
          responses={
              200: ResponseSpec(MergeOk, description='OK: новая запись',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': True,
                                                         'id': '20260814-210500-654321',
                                                         'count': 40})]),
              400: ResponseSpec(dict, description='Нет ids / нет данных',
                                media_type='application/json', generate_examples=False,
                                examples=[Example(value={'ok': False, 'error': 'Не выбраны записи'})]),
          })
    def api_history_merge(
        data: MergeRequest = Body(
            title='Параметры',
            description='ids — список ID записей истории для объединения.',
            examples=[Example(value={'ids': ['20260811-160924-376519', '20260811-161200-123456']})],
        ),
    ) -> Any:
        ids = [str(i) for i in (data.ids or [])]
        if not ids:
            return _err('Не выбраны записи')
        result = history.merge_and_save(ids)
        if not result:
            return _err('Нет данных для объединения')
        new_id, count = result
        return {'ok': True, 'id': new_id, 'count': count}

    @delete('/api/history/{hid:str}', status_code=200, sync_to_thread=True, summary='Удалить из истории',
            description='Удаляет запись истории.',
            responses={
                200: ResponseSpec(OkResponse, description='OK',
                                  media_type='application/json', generate_examples=False,
                                  examples=[Example(value={'ok': True})]),
            })
    def api_history_delete(hid: Annotated[str, PathParameter(description='ID записи (YYYYMMDD-HHMMSS-ffffff)')]) -> Any:
        return {'ok': history.delete(hid)}

    # --- БД-режим (TimescaleDB) ---

    def _require_db() -> bool:
        try:
            from ..db import enabled as _db_enabled
            return _db_enabled()
        except Exception:  # noqa: BLE001
            return False

    @get('/api/db/search', sync_to_thread=True, summary='Поиск из БД (без Chrome)',
         description='Поиск по собранным данным p2gis.records: город × рубрика × ключевые слова '
                     '(pg_trgm по search_text). Работает без запуска Chrome.',
         responses={200: ResponseSpec(ResultsResponse, description='OK',
                                      media_type='application/json', generate_examples=False)})
    def api_db_search(
        city: Annotated[Optional[str], QueryParameter(
            default=None, description='Город (код или часть названия)')] = None,
        q: Annotated[Optional[str], QueryParameter(
            default=None, description='Ключевые слова (название/адрес/рубрика)')] = None,
        rubric: Annotated[Optional[str], QueryParameter(
            default=None, description='Рубрика (rubricId)')] = None,
        limit: Annotated[int, QueryParameter(default=100,
                                             description='Максимум записей (до 5000)')] = 100,
    ) -> Any:
        if not _require_db():
            return _err('БД не настроена (задайте P2GIS_DB_URL)', 400)
        from ..db.queries import db_search
        return {'records': db_search(city=city, query=q, rubric=rubric, limit=limit)}

    @get('/api/db/cache', sync_to_thread=True, summary='Кэш запросов',
         description='Записи request_cache со свежестью (fresh/stale по TTL).',
         responses={200: ResponseSpec(dict, description='OK',
                                      media_type='application/json', generate_examples=False)})
    def api_db_cache() -> Any:
        if not _require_db():
            return _err('БД не настроена (задайте P2GIS_DB_URL)', 400)
        from ..db import cache as db_cache
        return {'items': db_cache.cache_rows()}

    @get('/api/db/coverage', sync_to_thread=True, summary='Покрытие данных',
         description='Записей по городу×рубрике, последнее обновление и свежесть.',
         responses={200: ResponseSpec(dict, description='OK',
                                      media_type='application/json', generate_examples=False)})
    def api_db_coverage() -> Any:
        if not _require_db():
            return _err('БД не настроена (задайте P2GIS_DB_URL)', 400)
        from ..db import cache as db_cache
        return {'items': db_cache.coverage()}

    @post('/api/refresh-stale', status_code=200, sync_to_thread=True,
          summary='Обновить протухшие запросы',
          description='Пере-парсит все запросы, чей кэш протух или отсутствует '
                      '(через JobManager, общий лимит воркеров).',
          responses={200: ResponseSpec(dict, description='OK',
                                       media_type='application/json', generate_examples=False)})
    def api_refresh_stale() -> Any:
        if not _require_db():
            raise HTTPException(status_code=400,
                                detail='БД не настроена (задайте P2GIS_DB_URL)')
        from ..db import cache as db_cache
        stale = db_cache.stale_fingerprints()
        if not stale:
            return {'ok': True, 'started': 0, 'message': 'Нет протухших запросов'}
        config = _build_config({'urls': [s['url'] for s in stale],
                                'max_concurrent': 1,
                                'advanced': {'storage': 'db'}})
        job_id = jobs.start(config, [s['url'] for s in stale],
                            fingerprints=[{k: s[k] for k in
                                           ('fingerprint', 'city_code', 'rubric_id',
                                            'query_text', 'url')} for s in stale])
        return {'ok': True, 'started': len(stale), 'job_id': job_id}

    @get('/api/schedules', sync_to_thread=True, summary='Расписания автообновления',
         description='Список расписаний планировщика (БД-режим).',
         responses={200: ResponseSpec(dict, description='OK',
                                      media_type='application/json', generate_examples=False)})
    def api_schedules() -> Any:
        if not _require_db():
            raise HTTPException(status_code=400,
                                detail='БД не настроена (задайте P2GIS_DB_URL)')
        from ..db.scheduler import list_schedules
        return {'items': list_schedules()}

    @post('/api/schedules', status_code=200, sync_to_thread=True,
          summary='Создать расписание',
          description='Создаёт расписание автообновления (cron или интервал).',
          responses={200: ResponseSpec(dict, description='OK',
                                       media_type='application/json', generate_examples=False)})
    def api_schedule_create(data: ScheduleRequest = Body(
        title='Расписание',
        description='name обязателен; cron или interval_minutes — одно из двух.',
        examples=[Example(value={'name': 'Фитнес: Москва', 'cron': '0 3 * * *',
                                 'cities': ['moskva'], 'rubrics': ['268']})])) -> Any:
        if not _require_db():
            raise HTTPException(status_code=400,
                                detail='БД не настроена (задайте P2GIS_DB_URL)')
        from ..db.scheduler import create_schedule
        try:
            return {'ok': True, 'schedule': create_schedule(
                name=data.name, cron=data.cron, interval_minutes=data.interval_minutes,
                cities=data.cities, rubrics=data.rubrics, queries=data.queries,
                max_concurrent=data.max_concurrent, ttl_hours=data.ttl_hours,
                sync_after=data.sync_after, enabled_flag=data.enabled)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @put('/api/schedules/{sid:int}', status_code=200, sync_to_thread=True,
         summary='Изменить расписание',
         description='Обновляет поля расписания.',
         responses={200: ResponseSpec(dict, description='OK',
                                      media_type='application/json', generate_examples=False),
                    404: ResponseSpec(dict, description='Не найдено',
                                      media_type='application/json', generate_examples=False)})
    def api_schedule_update(
        sid: Annotated[int, PathParameter(description='ID расписания')],
        data: ScheduleRequest = Body(title='Поля', description='Обновляемые поля.'),
    ) -> Any:
        if not _require_db():
            return _err('БД не настроена (задайте P2GIS_DB_URL)', 400)
        from ..db.scheduler import update_schedule
        try:
            sched = update_schedule(sid, **data.model_dump())
        except LookupError:
            return _err('Расписание не найдено', 404)
        except ValueError as e:
            return _err(str(e), 400)
        return {'ok': True, 'schedule': sched}

    @delete('/api/schedules/{sid:int}', status_code=200, sync_to_thread=True,
            summary='Удалить расписание',
            description='Удаляет расписание.',
            responses={200: ResponseSpec(OkResponse, description='OK',
                                         media_type='application/json', generate_examples=False)})
    def api_schedule_delete(sid: Annotated[int, PathParameter(description='ID расписания')]) -> Any:
        if not _require_db():
            return _err('БД не настроена (задайте P2GIS_DB_URL)', 400)
        from ..db.scheduler import delete_schedule
        return {'ok': delete_schedule(sid)}

    @post('/api/schedules/{sid:int}/run', status_code=200, sync_to_thread=True,
          summary='Запустить расписание сейчас',
          description='Немедленно ставит задачи расписания в очередь.',
          responses={200: ResponseSpec(dict, description='OK',
                                       media_type='application/json', generate_examples=False),
                     404: ResponseSpec(dict, description='Не найдено',
                                       media_type='application/json', generate_examples=False)})
    def api_schedule_run(sid: Annotated[int, PathParameter(description='ID расписания')]) -> Any:
        if not _require_db():
            return _err('БД не настроена (задайте P2GIS_DB_URL)', 400)
        try:
            return {'ok': True, **scheduler.run_schedule(sid)}
        except LookupError:
            return _err('Расписание не найдено', 404)
        except RuntimeError as e:
            return _err(str(e), 400)

    @post('/api/schedules/{sid:int}/toggle', status_code=200, sync_to_thread=True,
          summary='Включить/выключить расписание',
          description='Переключает флаг enabled.',
          responses={200: ResponseSpec(dict, description='OK',
                                       media_type='application/json', generate_examples=False)})
    def api_schedule_toggle(sid: Annotated[int, PathParameter(description='ID расписания')]) -> Any:
        if not _require_db():
            return _err('БД не настроена (задайте P2GIS_DB_URL)', 400)
        from ..db.scheduler import toggle_schedule
        try:
            return {'ok': True, 'schedule': toggle_schedule(sid)}
        except LookupError:
            return _err('Расписание не найдено', 404)

    @post('/api/sync', status_code=200, sync_to_thread=True,
          summary='Синхронизировать организации в целевую схему',
          description='Переносит p2gis.records в целевую схему (org + филиалы, upsert по firm_id, '
                      'деактивация исчезнувших). Без since — с последнего курсора.',
          responses={200: ResponseSpec(dict, description='OK',
                                       media_type='application/json', generate_examples=False)})
    def api_sync(data: Optional[SyncRequest] = Body(
        title='Параметры',
        description='Все фильтры опциональны; можно вызывать и без тела.',
        default=None,
            examples=[Example(value={'limit': 20000})])) -> Any:
        if not _require_db():
            raise HTTPException(status_code=400,
                                detail='БД не настроена (задайте P2GIS_DB_URL)')
        from ..db.sync import sync_organizations, sync_prices
        since = None
        if data and data.since:
            try:
                since = datetime.fromisoformat(data.since)
            except ValueError:
                return _err('since: некорректный ISO-формат', 400)
        try:
            res = sync_organizations(
                since=since, limit=(data.limit if data and data.limit else 20000),
                city=(data.city if data else None),
                rubric_id=(data.rubric_id if data else None),
                deactivate=(data.deactivate if data else True))
            if data is None or data.sync_prices:
                try:
                    res['prices'] = sync_prices()
                except Exception as e:  # noqa: BLE001
                    logger.warning('Синхронизация прайсов пропущена: %s', e)
                    res['prices'] = {'error': str(e)}
        except Exception as e:
            logger.error('Ошибка синхронизации: %s', e)
            return _err(str(e), 500)
        return {'ok': True, **res}

    @get('/api/sync/status', sync_to_thread=True, summary='Статус синхронизации',
         description='Курсор и последняя ошибка синхронизации в целевую схему.',
         responses={200: ResponseSpec(dict, description='OK',
                                      media_type='application/json', generate_examples=False)})
    def api_sync_status() -> Any:
        from ..db.sync import sync_status
        return sync_status()

    @post('/api/prices', status_code=200, sync_to_thread=True,
          summary='Загрузить прайс-каталог фирм (вкладка «Цены»)',
          description='Собирает цены с market-backend.api.2gis.ru (без Chrome) '
                      'и в БД-режиме сохраняет в p2gis.branch_prices. '
                      'В файловом режиме возвращает данные без сохранения.',
          responses={200: ResponseSpec(dict, description='OK',
                                       media_type='application/json', generate_examples=False)})
    def api_prices(data: PricesRequest = Body(
            title='Параметры',
            description='firm_ids — список branch_id филиалов.',
            examples=[Example(value={'firm_ids': ['9148465024074680'],
                                     'locale': 'ru_RU'})])) -> Any:
        from ..db import prices as prices_db
        res = prices_db.fetch_many(data.firm_ids, locale=data.locale,
                                   delay=data.delay if data.delay is not None else 0.4)
        return {'ok': True, 'results': res}

    @post('/api/prices/read', status_code=200, sync_to_thread=True,
          summary='Прочитать прайс фирмы из БД',
          description='Возвращает позиции прайс-каталога из p2gis.branch_prices.',
          responses={200: ResponseSpec(dict, description='OK',
                                       media_type='application/json', generate_examples=False)})
    def api_prices_read(data: PricesReadRequest = Body(
            title='Параметры',
            description='firm_id + лимит позиций.',
            examples=[Example(value={'firm_id': '9148465024074680', 'limit': 100})])) -> Any:
        from ..db import prices as prices_db
        items = prices_db.list_firm_prices(data.firm_id,
                                           limit=data.limit if data.limit else 500)
        return {'ok': True, 'firm_id': data.firm_id, 'items': items, 'count': len(items)}

    app = Litestar(
        route_handlers=[
            index,
            api_start,
            api_stop,
            api_clear,
            api_jobs,
            api_status,
            api_results,
            api_generator,
            api_refresh,
            api_add_city,
            api_cities,
            api_download,
            api_history,
            api_history_results,
            api_history_download,
            api_history_merge,
            api_history_delete,
            api_geocode,
            api_route,
            api_db_search,
            api_db_cache,
            api_db_coverage,
            api_refresh_stale,
            api_schedules,
            api_schedule_create,
            api_schedule_update,
            api_schedule_delete,
            api_schedule_run,
            api_schedule_toggle,
            api_sync,
            api_sync_status,
            api_prices,
            api_prices_read,
        ],
        static_files_config=[StaticFilesConfig(path='/static', directories=[str(static_dir)])],
        openapi_config=OpenAPIConfig(
            title='Parser2GIS API', version=version,
            openapi_controller=_LocalDocsController),
    )
    app.state.scheduler = scheduler
    return app


def run_server(host: str = '127.0.0.1', port: int = 8666, open_browser: bool = True) -> None:
    """Run the dashboard server (blocking)."""
    import uvicorn

    app = create_app()
    # БД-режим: сначала применяем схему и сидируем справочники, потом запускаем
    # планировщик и фоновое обновление справочников (чтобы БД-запись шла в уже
    # существующие таблицы — раньше гонка оставляла p2gis.cities/rubrics пустыми).
    try:
        from ..db import apply_schema, enabled
        if enabled():
            if apply_schema():
                from .refdata import seed_refdata_db
                seed_refdata_db()
                scheduler = getattr(app.state, 'scheduler', None)
                if scheduler is not None:
                    # Самовосстановление после рестарта: пере-очередь прерванных
                    # задач (p2gis.jobs) + сброс застрявших расписаний.
                    try:
                        from ..db.recovery import recover_after_restart
                        jobs_mgr = getattr(app.state, 'jobs', None) or scheduler._jobs
                        n = recover_after_restart(jobs_mgr)
                        if n:
                            logger.info('Самовосстановление: пере-очередено задач: %d', n)
                    except Exception as e:  # noqa: BLE001
                        logger.warning('Самовосстановление не выполнено: %s', e)
                    scheduler.start()
            else:
                logger.error('Схема p2gis не применена — БД-режим недоступен.')
    except Exception as e:  # noqa: BLE001
        logger.exception('Не удалось инициализировать БД-режим: %s', e)

    # Автообновление справочников (при запуске + раз в сутки). Только в
    # реальном сервере — тесты/импорты не трогают сеть.
    try:
        from .refdata import start_background_refresh
        start_background_refresh()
    except Exception:  # noqa: BLE001
        logger.exception('Не удалось запустить фоновое обновление справочников')

    url = f'http://{host}:{port}/'
    logger.info('Веб-интерфейс запущен: %s', url)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        uvicorn.run(app, host=host, port=port, log_level='warning')
    finally:
        try:
            from ..db.connection import close_pool
            close_pool()
        except Exception:  # noqa: BLE001
            pass
