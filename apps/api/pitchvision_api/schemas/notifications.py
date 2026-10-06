"""/api/v1/notifications bodies (SCR-51)."""

import uuid
from datetime import datetime
from typing import Any

from pitchvision_schemas import NotificationType, notification_path
from pydantic import BaseModel, Field

from pitchvision_api.db.models import Notification


class NotificationOut(BaseModel):
    id: uuid.UUID
    type: NotificationType
    video_id: uuid.UUID | None
    video_title: str | None
    data: dict[str, Any]
    path: str = Field(description="Web route the notification opens (SCR-51 E02)")
    created_at: datetime
    read_at: datetime | None

    @classmethod
    def of(cls, n: Notification, video_title: str | None) -> "NotificationOut":
        return cls(id=n.id, type=n.type, video_id=n.video_id, video_title=video_title, data=n.data or {},  # type: ignore[arg-type]
                   path=notification_path(n.type, str(n.video_id) if n.video_id else None), created_at=n.created_at,
                   read_at=n.read_at)  # fmt: skip


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread_count: int
    next_cursor: str | None = None


class MarkRead(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    all: bool = Field(False, description="Mark every notification as read (E01 Mark all as read)")
