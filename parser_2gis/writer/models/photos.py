from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ExternalContent(BaseModel):
    """Внешний контент филиала (фотоальбомы, видео и т.д.)."""
    # Тип контента (photo_album, video и т.д.)
    type: Optional[str] = None

    # Подтип контента (common, view и т.д.)
    subtype: Optional[str] = None

    # Количество элементов в альбоме
    count: Optional[int] = None

    # URL главного фото альбома
    main_photo_url: Optional[str] = None