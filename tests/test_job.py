# ================================
# tests/test_job.py
# Пауза/возобновление задач (ParseJob): пауза применима только к запущенной
# задаче, resume — только с паузы.
# ================================
from parser_2gis.config import Configuration
from parser_2gis.web.job import ParseJob


def test_job_pause_resume_guards():
    job = ParseJob('x', Configuration(),
                   ['https://2gis.ru/vologda/search/фитнес'])
    # Не запущена — пауза не применима.
    assert job.pause() is False
    assert job.paused is False
    job.status = 'running'
    assert job.pause() is True
    assert job.paused is True
    # Повторная пауза — идемпотентна (остаётся на паузе).
    assert job.pause() is True
    assert job.resume() is True
    assert job.paused is False
    # Resume без паузы — False.
    assert job.resume() is False


def test_job_stop_clears_pause_wait():
    """stop будит цикл ожидания паузы (иначе задача зависнет на wait)."""
    job = ParseJob('x', Configuration(),
                   ['https://2gis.ru/vologda/search/фитнес'])
    job.status = 'running'
    job.pause()
    assert job.paused is True
    job.stop()
    assert job.paused is True   # флаг остаётся, но цикл разбужен (cancelled)


def test_jobs_update_status_sql_placeholders(monkeypatch):
    """update_status: фрагмент finished_at подставляется в текст SQL, а не
    параметром ($3) — регрессия «ошибка синтаксиса» из проды."""
    captured = {}

    class _FakeCur:
        def execute(self, sql, params):
            captured['sql'] = sql
            captured['params'] = params
            return self

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            captured['sql'] = sql
            captured['params'] = params
            return _FakeCur()

    from parser_2gis.db import jobs as db_jobs
    monkeypatch.setattr(db_jobs, 'connection', lambda: _FakeConn())

    db_jobs.update_status('job1', 'done', None, finished=True)
    assert captured['sql'].count('%s') == 3
    assert captured['params'] == ['job1', None, 'job1']
    assert ', finished_at=now()' in captured['sql']

    db_jobs.update_status('job1', 'running', None, finished=False)
    assert captured['sql'].count('%s') == 3
    assert captured['params'] == ['job1', None, 'job1']
    assert ', finished_at=now()' not in captured['sql']
