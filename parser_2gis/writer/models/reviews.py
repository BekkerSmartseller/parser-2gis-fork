from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Reviews(BaseModel):
    # Общий рейтинг
    general_rating: Optional[float] = None

    # Общее кол-во отзывов
    general_review_count: Optional[int] = None

    # Общее кол-во отзывов со звёздами
    general_review_count_with_stars: Optional[int] = None

    # Рейтинг организации (все филиалы)
    org_rating: Optional[float] = None

    # Кол-во отзывов организации (все филиалы)
    org_review_count: Optional[int] = None

    # Кол-во отзывов организации со звёздами (все филиалы)
    org_review_count_with_stars: Optional[int] = None
