from __future__ import annotations

import json
import os
import tempfile
import webbrowser
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..config import Configuration
from ..logger import logger
from ..paths import data_path
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


@lru_cache(maxsize=1)
def _load_cities() -> list[dict[str, Any]]:
    with open(data_path() / 'cities.json', 'r', encoding='utf-8') as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_rubrics() -> list[dict[str, Any]]:
    """Flat list of rubrics for the web generator picker."""
    with open(data_path() / 'rubrics.json', 'r', encoding='utf-8') as f:
        rubrics = json.load(f)
    out = []
    for node in rubrics.values():
        # Skip the synthetic root and group headers without a usable label.
        if node.get('code') in (None, '0') or not node.get('label'):
            continue
        out.append({
            'code': node['code'],
            'label': node['label'],
            'is_russian': bool(node.get('isRussian', True)),
            'is_non_russian': bool(node.get('isNonRussian', True)),
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


def create_app():
    """Create the Litestar app for the dashboard."""
    from litestar import Litestar, delete, get, post
    from litestar.response import File, Response
    from litestar.static_files.config import StaticFilesConfig

    static_dir = _static_dir()
    jobs = JobManager(max_concurrent=3)
    history = History()

    def _err(msg: str, code: int = 400) -> Any:
        """JSON error response (Litestar does not support (body, status) tuples)."""
        return Response(content=json.dumps({'ok': False, 'error': msg}),
                        media_type='application/json', status_code=code)

    @get('/', sync_to_thread=True, summary='Дашборд', description='Главная страница веб-интерфейса')
    def index() -> Any:
        return Response(content=(static_dir / 'index.html').read_bytes(),
                        media_type='text/html')

    @post('/api/start', sync_to_thread=True, summary='Запустить парсинг', description='Body: {urls, max_records, max_concurrent, headless, clean, filters, advanced}. Возвращает job_id')
    def api_start(data: dict[str, Any] | None = None) -> Any:
        data = data or {}
        urls = [u.strip() for u in (data.get('urls') or []) if u and u.strip()]
        if not urls:
            return _err('Не указаны ссылки')
        try:
            config = _build_config(data)
            # Update worker concurrency on the fly from the request's max_concurrent.
            job_id = jobs.start(config, urls)
        except RuntimeError as e:
            return _err(str(e), 409)
        except Exception as e:
            logger.error('Не удалось запустить парсинг: %s', e)
            return _err(str(e))
        return {'ok': True, 'job_id': job_id}

    @post('/api/stop', sync_to_thread=True, summary='Остановить задачу', description='job_id в теле')
    def api_stop(data: dict[str, Any] | None = None) -> Any:
        data = data or {}
        job_id = data.get('job_id') or None
        if job_id and job_id not in jobs._jobs:
            return _err('Задача не найдена', 404)
        return {'ok': jobs.stop(job_id)}

    @post('/api/clear', sync_to_thread=True, summary='Очистить задачу', description='job_id в теле')
    def api_clear(data: dict[str, Any] | None = None) -> Any:
        data = data or {}
        job_id = data.get('job_id') or None
        return {'ok': jobs.clear(job_id)}

    @get('/api/jobs', sync_to_thread=True, summary='Список задач', description='id, status, count')
    def api_jobs() -> Any:
        return {'jobs': jobs.list_jobs()}

    @get('/api/status', sync_to_thread=True, summary='Статус задачи', description='job_id, cursor. Без job_id - последняя')
    def api_status(cursor: int = 0, job_id: str | None = None) -> Any:
        job = jobs.get(job_id)
        if not job:
            return _err('Задача не найдена', 404)
        logs = job.logs[cursor:]
        return {
            'job_id': job.id,
            'status': job.status,
            'running': job.running,
            'count': job.count,
            'error': job.error,
            'logs': logs,
            'cursor': cursor + len(logs),
        }

    @get('/api/results', sync_to_thread=True, summary='Результаты задачи', description='job_id')
    def api_results(job_id: str | None = None) -> Any:
        job = jobs.get(job_id)
        if not job:
            return _err('Задача не найдена', 404)
        return {'records': job.results()}

    @get('/api/generator', sync_to_thread=True, summary='Данные генератора ссылок', description='countries, cities, rubrics')
    def api_generator() -> Any:
        """Data for the link generator: countries, cities, rubrics."""
        cities = [
            {'name': c['name'], 'code': c['code'], 'domain': c['domain'],
             'country_code': c['country_code']}
            for c in _load_cities()
        ]
        countries = [{'code': k, 'name': v} for k, v in COUNTRIES.items()]
        countries.sort(key=lambda c: c['name'])
        return {'countries': countries, 'cities': cities, 'rubrics': _load_rubrics()}

    @get('/api/download', sync_to_thread=True, summary='Скачать результат', description='format, job_id')
    def api_download(format: str = 'csv', job_id: str | None = None) -> Any:
        if format not in _DOWNLOAD_NAMES:
            return _err('Неизвестный формат')
        job = jobs.get(job_id)
        if not job or not job.collector:
            return _err('Нет данных')
        try:
            return _export_response(job.collector.docs, job.collector._options, format)
        except Exception as e:
            logger.error('Ошибка экспорта: %s', e)
            return _err(str(e), 500)

    @get('/api/history', sync_to_thread=True, summary='История парсингов', description='Сохранённые задачи')
    def api_history() -> Any:
        return {'items': history.list()}

    @get('/api/history/{hid:str}/results', sync_to_thread=True, summary='Записи из истории', description='hid')
    def api_history_results(hid: str) -> Any:
        docs = history.docs(hid)
        if docs is None:
            return _err('Запись не найдена', 404)
        return {'records': history.records(hid)}

    @get('/api/history/{hid:str}/download', sync_to_thread=True, summary='Скачать из истории', description='hid, format')
    def api_history_download(hid: str, format: str = 'csv') -> Any:
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

    @post('/api/history/merge', sync_to_thread=True, summary='Объединить парсинги', description='ids в теле')
    def api_history_merge(data: dict[str, Any] | None = None) -> Any:
        data = data or {}
        ids = [str(i) for i in (data.get('ids') or [])]
        if not ids:
            return _err('Не выбраны записи')
        result = history.merge_and_save(ids)
        if not result:
            return _err('Нет данных для объединения')
        new_id, count = result
        return {'ok': True, 'id': new_id, 'count': count}

    @delete('/api/history/{hid:str}', status_code=200, sync_to_thread=True, summary='Удалить из истории', description='hid')
    def api_history_delete(hid: str) -> Any:
        return {'ok': history.delete(hid)}

    return Litestar(
        route_handlers=[
            index,
            api_start,
            api_stop,
            api_clear,
            api_jobs,
            api_status,
            api_results,
            api_generator,
            api_download,
            api_history,
            api_history_results,
            api_history_download,
            api_history_merge,
            api_history_delete,
        ],
        static_files_config=[StaticFilesConfig(path='/static', directories=[str(static_dir)])],
    )


def run_server(host: str = '127.0.0.1', port: int = 8666, open_browser: bool = True) -> None:
    """Run the dashboard server (blocking)."""
    import uvicorn

    app = create_app()
    url = f'http://{host}:{port}/'
    logger.info('Веб-интерфейс запущен: %s', url)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host=host, port=port, log_level='warning')