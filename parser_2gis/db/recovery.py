# ================================
# parser_2gis/db/recovery.py
# Самовосстановление после рестарта (БД-режим):
#   1. Задачи, застрявшие в queued/running (процесс был убит), помечаются
#      interrupted и пере-очередятся — URL и конфиг сохранены в p2gis.jobs,
#      собранные записи в p2gis.records выживают (upsert по firm_id).
#   2. Расписания refresh_schedules, застрявшие в last_status='running',
#      помечаются failed (честный UI); next_run не трогаем.
# ================================
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..web.job import JobManager

logger = logging.getLogger(__name__)


def recover_after_restart(jobs: 'JobManager') -> int:
    """Пере-очередь прерванных задач + сброс застрявших расписаний.

    Вызывается при старте сервера ПОСЛЕ apply_schema и seed_refdata_db.
    Возвращает число пере-очередённых задач. Best-effort: ошибка не должна
    валить запуск сервера."""
    n = 0
    try:
        from ..config import Configuration
        from ..db import jobs as db_jobs
        from .scheduler import mark_stale_running_schedules

        for item in db_jobs.mark_all_active_interrupted():
            try:
                cfg = Configuration.model_validate(item['config'])
                jobs.start(cfg, item['urls'], fingerprints=item['fingerprints'],
                           cache_hit=item['cache_hit'])
                n += 1
                logger.info('[recovery] задача %s пере-очередена (%d URL)',
                            item['job_id'], len(item['urls']))
            except Exception as e:  # noqa: BLE001
                logger.warning('[recovery] пере-очередь задачи %s: %s',
                               item['job_id'], e)
        try:
            mark_stale_running_schedules()
        except Exception as e:  # noqa: BLE001
            logger.warning('[recovery] сброс расписаний: %s', e)
    except Exception as e:  # noqa: BLE001
        logger.warning('[recovery] не удалось: %s', e)
    return n
