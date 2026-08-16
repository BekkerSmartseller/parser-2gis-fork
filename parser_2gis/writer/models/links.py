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


class NearestMetro(BaseModel):
    """Ближайшая станция метро (id + дистанция)."""
    # Идентификатор станции
    id: str

    # Расстояние до станции в метрах
    distance: Optional[int] = None


class NearestParking(BaseModel):
    """Ближайшая парковка (2GIS отдаёт только id)."""
    id: str


class Entrance(BaseModel):
    """Вход в здание (геометрия)."""
    id: str
    geometry: Optional[dict] = None
    is_primary: Optional[bool] = None
    is_visible_on_map: Optional[bool] = None


class Links(BaseModel):
    """Ссылки и связанные объекты филиала (остановки, парковки, входы)."""
    # Ближайшие остановки общественного транспорта
    nearest_stations: List[NearestStation] = []

    # Ближайшие станции метро
    nearest_metro: List[NearestMetro] = []

    # Ближайшие парковки (только id)
    nearest_parking: List[NearestParking] = []

    # Входы в здание
    entrances: List[Entrance] = []

    # Входы из базы данных (внутренние)
    database_entrances: List[Entrance] = []