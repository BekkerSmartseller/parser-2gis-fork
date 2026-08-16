# ================================
# parser_2gis/db/queries.py
# Поиск и выборки по p2gis.records (чтение из БД без Chrome).
# ================================
from __future__ import annotations

from typing import Any, Optional

from ..logger import logger
from ..writer.record import extract_record
from .connection import connection


def db_search(city: Optional[str] = None, query: Optional[str] = None,
              rubric: Optional[str] = None, limit: int = 100) -> list[dict]:
    """Поиск по собранным данным: город (code/имя) × рубрика × ключевые слова.

    Использует pg_trgm (search_text) и фильтры rubric_ids/city_code.
    Возвращает плоские записи (как /api/results).
    """
    where = ["is_active"]
    params: list[Any] = []
    if city and city.strip():
        where.append("(city_code = %s OR city ILIKE %s)")
        params += [city.strip(), '%' + city.strip() + '%']
    if rubric:
        where.append("rubric_ids @> ARRAY[%s]::text[]")
        params.append(rubric)
    order = "ORDER BY updated_at DESC"
    if query and query.strip():
        q = query.strip().lower()
        where.append("(replace(search_text, '-', ' ') ILIKE '%%' || replace(%s, '-', ' ') || '%%' "
                     "OR similarity(search_text, %s) > 0.2)")
        params += [q, q]
        order = "ORDER BY similarity(search_text, %s) DESC, updated_at DESC"
        params.append(q)
    limit = max(1, min(int(limit or 100), 5000))
    sql = "SELECT raw_doc FROM p2gis.records WHERE {} {} LIMIT %s".format(
        ' AND '.join(where), order)
    try:
        with connection() as conn:
            rows = conn.execute(sql, params + [limit]).fetchall()
        out = []
        for row in rows:
            rec = extract_record(row['raw_doc'])
            if rec:
                out.append(rec)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning('[db] db_search: %s', e)
        return []
