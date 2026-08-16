#!/usr/bin/env python3
# ================================
# scripts/backfill_history_to_db.py
# Одноразовый импорт файловой истории (~/.local/share/parser-2gis/history/*.json)
# в p2gis.records (БД-режим). Идемпотентно по firm_id. Требует P2GIS_DB_URL.
#
#   P2GIS_DB_URL='postgres://...' python scripts/backfill_history_to_db.py
# ================================
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser_2gis.db import apply_schema, enabled  # noqa: E402
from parser_2gis.db.store import upsert_records  # noqa: E402
from parser_2gis.paths import user_path  # noqa: E402


def main() -> int:
    if not enabled():
        print('Ошибка: задайте P2GIS_DB_URL (БД-режим не включён).')
        return 1
    if not apply_schema():
        print('Ошибка: не удалось применить схему p2gis.')
        return 1

    history_dir = user_path() / 'history'
    files = sorted(history_dir.glob('*.json')) if history_dir.is_dir() else []
    if not files:
        print('Файловая история не найдена:', history_dir)
        return 0

    total = 0
    from datetime import timezone

    from parser_2gis.db import cache as db_cache
    for path in files:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:  # noqa: BLE001
            print(f'  пропуск {path.name}: {e}')
            continue
        docs = data.get('docs') or []
        if not docs:
            continue
        try:
            n = upsert_records(docs, job_id='backfill:' + path.stem)
        except Exception as e:  # noqa: BLE001
            print(f'  ошибка {path.name}: {e}')
            continue
        total += n
        # Кэш запросов из urls истории (last_parsed_at — created_at записи, иначе mtime).
        urls = data.get('urls') or []
        try:
            created = datetime.fromisoformat(data.get('created_at', ''))
        except Exception:  # noqa: BLE001
            created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        db_cache.mark_backfilled(urls, created)
        print(f'  {path.name}: {n} записей, {len(urls)} URL')
    print(f'Импортировано записей: {total}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
