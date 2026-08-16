from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Attribute(BaseModel):
    """Атрибут филиала внутри группы (например «Средний чек: 1000 ₽», «Пилатес»)."""
    # Идентификатор атрибута
    id: Optional[str] = None

    # Название атрибута (например «Месячный абонемент от 1500 ₽»)
    name: Optional[str] = None

    # Тег атрибута (машиночитаемый идентификатор, например fitness_details_pilates)
    tag: Optional[str] = None

    # Ссылка на иконку атрибута
    icon_url: Optional[str] = None

    # Признак награды/премии (группа «Премия 2ГИС»: «Лучший фитнес-клуб 2026»)
    is_award: Optional[bool] = None


class AttributeGroup(BaseModel):
    """Группа атрибутов (например «Способы оплаты», «Фитнес-клубы и тренажёрные залы»)."""
    # Название группы
    name: Optional[str] = None

    # Список атрибутов
    attributes: List[Attribute] = []

    # Ссылка на иконку группы
    icon_url: Optional[str] = None