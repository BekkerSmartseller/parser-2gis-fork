from __future__ import annotations

import logging
import queue
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import Configuration
from ..logger import logger
from ..parser import get_parser
from ..writer import FilterWriter
from ..writer.filters import any_filter_enabled
from ..writer.record import extract_record
from ..writer.writers import FileWriter
from .history import History

# Keep at most this many log lines in memory for the live progress panel.
_MAX_LOG_LINES = 5000


class CollectorWriter(FileWriter):
    """In-memory writer: collects raw catalog documents (no file output).

    Used by the web dashboard so results can be rendered as cards and later
    exported to any format on demand.
    """
    def __init__(self, writer_options) -> None:
        super().__init__('', writer_options)
        self.docs: list[Any] = []

    def __enter__(self) -> 'CollectorWriter':
        return self

    def __exit__(self, *exc_info) -> None:
        pass

    @property
    def count(self) -> int:
        return len(self.docs)

    def results(self) -> list[dict]:
        out = []
        for doc in self.docs:
            record = extract_record(doc)
            if record:
                out.append(record)
        return out

    def all_docs(self) -> list[Any]:
        return list(self.docs)

    def write(self, catalog_doc: Any) -> None:
        if not self._check_catalog_doc(catalog_doc):
            return
        self.docs.append(catalog_doc)
        if self._options.verbose:
            record = extract_record(catalog_doc)
            logger.info('Парсинг [%d] > %s', len(self.docs),
                        record['name'] if record else '...')


class _ListLogHandler(logging.Handler):
    """Logging handler that appends formatted records to a shared list."""
    def __init__(self, sink: list[str]) -> None:
        super().__init__()
        self._sink = sink
        self.setFormatter(logging.Formatter('%(asctime)s | %(message)s', datefmt='%H:%M:%S'))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink.append(self.format(record))
            if len(self._sink) > _MAX_LOG_LINES:
                del self._sink[:len(self._sink) - _MAX_LOG_LINES]
        except Exception:
            pass


class ParseJob:
    """A single background parse job (one URL set, one Chrome instance)."""
    def __init__(self, job_id: str, config: Configuration, urls: list[str],
                 fingerprints: Optional[list] = None,
                 cache_hit: bool = False) -> None:
        self.id = job_id
        self._config = config
        self._urls = urls
        self._fingerprints = fingerprints or []
        self._cache_hit = cache_hit
        self._started_at = datetime.now(timezone.utc)
        self._parser = None
        self._cancelled = False
        self.status = 'queued'  # queued | running | done | stopped | error
        self.logs: list[str] = []
        self.error: Optional[str] = None
        self.collector: Optional[CollectorWriter] = None

    @property
    def running(self) -> bool:
        return self.status == 'running'

    @property
    def count(self) -> int:
        return self.collector.count if self.collector else 0

    @property
    def db_mode(self) -> bool:
        return self._config.parser.storage == 'db'

    def start(self) -> None:
        """Start parsing in a daemon thread."""
        self.status = 'running'
        if self.db_mode:
            from ..db import jobs as db_jobs
            from ..db.store import DbCollector
            self.collector = DbCollector(self._config.writer, job_id=self.id)
            self.collector.set_fingerprints(self._fingerprints)
            self.collector.set_cache_hit(self._cache_hit)
            db_jobs.update_status(self.id, 'running', finished=False)
        else:
            self.collector = CollectorWriter(self._config.writer)
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def stop(self) -> None:
        self._cancelled = True
        if self._parser:
            try:
                self._parser.close()
            except Exception:
                pass

    def cancel_queued(self) -> None:
        """Mark a queued job as cancelled (never started)."""
        self._cancelled = True
        self.status = 'stopped'

    def _heartbeat_loop(self, stop: threading.Event) -> None:
        """Живой сигнал задачи в p2gis.jobs (каждые 30с) — для stale-детектора."""
        while not stop.wait(30):
            try:
                from ..db import jobs as db_jobs
                db_jobs.heartbeat(self.id)
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        handler = _ListLogHandler(self.logs)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)  # ensure INFO progress lines are captured
        hb_stop = threading.Event()
        try:
            assert self.collector is not None
            writer: FileWriter = self.collector
            if any_filter_enabled(self._config.filters):
                writer = FilterWriter(self.collector, self._config.filters)

            # Кэш-ответ: читаем результаты из БД, Chrome не запускаем.
            if self._cache_hit:
                n = self.collector.load_cached()
                logger.info('Парсинг запущен (кэш из БД).')
                logger.info('Результат из БД: %d записей (без запуска Chrome).', n)
                self.status = 'done'
                logger.info('Парсинг завершён (кэш из БД).')
                self._db_postprocess('cache')
                return

            logger.info('Парсинг запущен.')
            if self.db_mode:
                threading.Thread(target=self._heartbeat_loop, args=(hb_stop,),
                                 daemon=True).start()
            with writer:
                for url in self._urls:
                    if self._cancelled:
                        break
                    logger.info('Парсинг ссылки %s', url)
                    self._parser = get_parser(url, chrome_options=self._config.chrome,
                                              parser_options=self._config.parser)
                    with self._parser:
                        if not self._cancelled:
                            self._parser.parse(writer)

            self.status = 'stopped' if self._cancelled else 'done'
            logger.info('Парсинг %s.', 'остановлен' if self._cancelled else 'завершён')
            if self.db_mode and not self._cancelled:
                self._db_postprocess('ok')
        except Exception as e:
            if self._cancelled:
                # Stopping closes the browser tab mid-request, surfacing as
                # "Tab has been stopped" — a clean stop, not a failure.
                self.status = 'stopped'
                logger.info('Парсинг остановлен.')
            else:
                self.error = str(e)
                self.status = 'error'
                logger.error('Ошибка во время работы парсера.', exc_info=True)
        finally:
            hb_stop.set()
            self._parser = None
            # БД-режим: фиксируем финальный статус в p2gis.jobs и пишем журнал
            # даже для прерванных/упавших задач (request_cache не трогаем —
            # частичный результат нельзя класть в кэш).
            if self.db_mode:
                try:
                    from ..db import jobs as db_jobs
                    db_jobs.update_status(self.id, self.status, self.error, finished=True)
                    if self.status in ('stopped', 'error'):
                        self._db_postprocess(self.status)
                except Exception as e:  # noqa: BLE001
                    logger.warning('[jobs] финальный статус задачи: %s', e)
            # Persist whatever was collected — full run or partial stop — so the
            # records survive reloads and aren't lost when the user hits Stop.
            # В БД-режиме результаты уже в БД (файловая история не нужна).
            if (not self.db_mode and self.status in ('done', 'stopped')
                    and self.collector and self.collector.docs):
                try:
                    History().save(self._urls, self.collector.docs,
                                   self.collector._options.model_dump(mode='json'))
                except Exception as e:
                    logger.error('Не удалось сохранить историю: %s', e)
            logger.removeHandler(handler)

    def _db_postprocess(self, result_status: str) -> None:
        """После задачи в БД-режиме: журнал + request_cache + автосинк.

        Для прерванных/упавших исходов пишем только журнал parse_requests
        (без обновления request_cache и без синка)."""
        try:
            from ..db import cache as db_cache
            if result_status in ('ok', 'cache'):
                db_cache.record_job(self.id, self._urls, result_status,
                                    cache_hit=self._cache_hit,
                                    ttl_hours=self._config.parser.cache_ttl_hours or None,
                                    started_at=self._started_at)
                if self._config.parser.sync_after and not self._cache_hit:
                    from ..db.sync import sync_organizations
                    try:
                        res = sync_organizations(since=self._started_at)
                        logger.info('Синхронизация в целевую схему: org=%d, филиалы=%d',
                                    res.get('synced_orgs'), res.get('branches_upserted'))
                    except Exception as e:  # noqa: BLE001
                        logger.warning('Автосинк не выполнен: %s', e)
            else:
                db_cache.record_job_failed(self.id, self._urls, result_status,
                                           started_at=self._started_at)
        except Exception as e:  # noqa: BLE001
            logger.warning('[db] пост-обработка задачи: %s', e)

    def results(self) -> list[dict]:
        """Presentation-ready records for the dashboard grid."""
        if not self.collector:
            return []
        return self.collector.results()

    def export_docs(self) -> list[Any]:
        """Сырые документы для экспорта (БД-режим читает полные данные из БД)."""
        if not self.collector:
            return []
        if hasattr(self.collector, 'all_docs'):
            return self.collector.all_docs()
        return list(self.collector.docs)


class JobManager:
    """Manages concurrent parse jobs.

    A dedicated daemon worker thread drains a FIFO queue; a ``threading.Semaphore``
    bounds how many Chrome instances run at once. Each job has its own id so the
    client can poll / stop a specific one. Proxies from the request are assigned
    per job and rotated between concurrent tasks (round-robin).
    """
    def __init__(self, max_concurrent: int = 3) -> None:
        self._max_concurrent = max(1, int(max_concurrent))
        self._semaphore = threading.Semaphore(self._max_concurrent)
        self._lock = threading.Lock()
        self._queue: queue.Queue[ParseJob] = queue.Queue()
        self._jobs: dict[str, ParseJob] = {}
        self._worker_thread: Optional[threading.Thread] = None
        self._start_worker()

    def _start_worker(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return

        def worker_loop() -> None:
            while True:
                job = self._queue.get()
                with self._semaphore:
                    if job._cancelled:
                        # Cancelled while queued — never started Chrome.
                        job.status = 'stopped'
                        continue
                    job.start()  # spawns the job's own daemon parse thread

        self._worker_thread = threading.Thread(target=worker_loop, daemon=True)
        self._worker_thread.start()

    def start(self, config: Configuration, urls: list[str],
              fingerprints: Optional[list] = None,
              cache_hit: bool = False) -> str:
        """Queue a new parse job. Returns its job_id."""
        job_id = uuid.uuid4().hex[:12]
        # Deep-copy config so each job owns its ChromeOptions (proxy must not leak).
        job_config = config.model_copy(deep=True)
        job = ParseJob(job_id, job_config, urls, fingerprints=fingerprints,
                       cache_hit=cache_hit)
        with self._lock:
            self._jobs[job_id] = job
        # БД-режим: персистентная регистрация (для самовосстановления).
        if job.db_mode:
            try:
                from ..db import jobs as db_jobs
                db_jobs.create_job(job_id, urls, job_config.model_dump(mode='json'),
                                   fingerprints or [], cache_hit)
            except Exception as e:  # noqa: BLE001
                logger.warning('[jobs] регистрация задачи: %s', e)
        self._queue.put_nowait(job)
        return job_id

    def get(self, job_id: Optional[str]) -> Optional[ParseJob]:
        with self._lock:
            if job_id is None:
                jobs = list(self._jobs.values())
                return jobs[-1] if jobs else None
            return self._jobs.get(job_id)

    def stop(self, job_id: Optional[str] = None) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        if job.status == 'queued':
            job.cancel_queued()
        else:
            job.stop()
        return True

    def clear(self, job_id: Optional[str] = None) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        if job.running:
            return False
        job.collector = None
        job.logs = []
        job.error = None
        job.status = 'idle'
        return True

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [{'id': j.id, 'status': j.status, 'count': j.count}
                    for j in self._jobs.values()]

    def stop_all(self) -> None:
        for job in list(self._jobs.values()):
            if job.status == 'queued':
                job.cancel_queued()
            elif job.running:
                job.stop()
