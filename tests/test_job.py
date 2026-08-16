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
