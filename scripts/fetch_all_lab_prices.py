# ================================
# scripts/fetch_all_lab_prices.py (запускать из parser-2gis-new с venv)
# Полный обход лабораторных фирм каталога и загрузка прайсов 2GIS market
# напрямую (без HTTP-сервера): parser_2gis.db.prices.fetch_many -> p2gis.branch_prices.
#
# Требует P2GIS_DB_URL (иначе запись в БД отключена).
# Идемпотентен: пропускает фирмы, у которых прайс уже есть в p2gis.branch_prices.
# Запуск:
#   P2GIS_DB_URL='postgres://...' .venv/bin/python scripts/fetch_all_lab_prices.py
#   (опц.) --brands 'Инвитро,Ситилаб' — только бренды по имени филиала
#   (опц.) --limit N — только первые N фирм
# ================================
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402

from parser_2gis.db import prices as P  # noqa: E402

BATCH = 10
DELAY = 0.15


def lab_firms(conn, brands: list[str] | None, limit: int) -> list[str]:
    sql = """
        SELECT DISTINCT b.firm_id
        FROM medexpertai.organization_branches b
        JOIN medexpertai.branch_categories bc ON bc.branch_id = b.id
        JOIN medexpertai.categories c ON c.id = bc.category_id
        WHERE b.status = 'active' AND b.firm_id IS NOT NULL AND b.firm_id <> ''
          AND lower(c.label) ~ 'анализ|диагностик'
          AND NOT EXISTS (SELECT 1 FROM p2gis.branch_prices p WHERE p.firm_id = b.firm_id)
    """
    params: list = []
    if brands:
        sql += ' AND (' + ' OR '.join(['lower(b.name) LIKE lower(%s)'] * len(brands)) + ')'
        params = [f'%{b}%' for b in brands]
    sql += ' ORDER BY b.firm_id'
    if limit:
        sql += ' LIMIT %s'
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--brands', default=None,
                    help='Ограничить бренды через запятую (по имени филиала)')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    dsn = os.environ.get('P2GIS_DB_URL', '')
    if not dsn:
        print('P2GIS_DB_URL не задан — запись в БД отключена')
        sys.exit(2)
    brands = [b.strip() for b in (args.brands or '').split(',') if b.strip()] or None

    with psycopg.connect(dsn) as conn:
        firms = lab_firms(conn, brands, args.limit)
    print(f'Лабораторных фирм к обходу: {len(firms)}', flush=True)
    if not firms:
        print('Прайсы уже загружены для всех фирм.')
        return

    total_ok = total_items = 0
    for i in range(0, len(firms), BATCH):
        batch = firms[i:i + BATCH]
        try:
            res = P.fetch_many(batch, delay=DELAY)
        except Exception as e:  # noqa: BLE001
            print(f'[warn] батч {i}: {e}', flush=True)
            continue
        for r in res or []:
            if r.get('ok'):
                total_ok += 1
                total_items += r.get('items') or 0
        if i % 300 == 0:
            print(f'[progress] {i + len(batch)}/{len(firms)} ok_firms={total_ok} '
                  f'items={total_items}', flush=True)
    print(f'ГОТОВО: фирм с прайсом={total_ok}, позиций={total_items}', flush=True)


if __name__ == '__main__':
    main()
