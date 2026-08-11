from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class NearestStation(BaseModel):
    """Ближайшая остановка общественного транспорта."""
    # Идентификатор остановки
    id: str

    # Название остановки
    name: Optional[str] = None

    # Расстояние до остановки в метрах
    distance: Optional[int] = None

    # Типы маршрутов (bus, trolleybus, shuttle_bus, tram и т.д.)
    route_types: Optional[List[str]] = None


class Links(BaseModel):
    """Ссылки и связанные объекты филиала (остановки, парковки, входы)."""
    # Ближайшие остановки общественного транспорта
    nearest_stations: List[NearestStation] = []