from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class OperationStatus(StrEnum):
    created = "created"
    active = "active"
    completed = "completed"
    failed = "failed"
    expired = "expired"


class OperationCreateRequest(BaseModel):
    selected_service: str | None = None


class OperationPublic(BaseModel):
    operation_id: str
    selected_service: str | None = None
    status: OperationStatus
    created_at: str
    expires_at: str
    locked_by_tab: str | None = None


class OperationCreateResponse(BaseModel):
    operation_id: str
    session_token: str
    csrf_token: str
    selected_service: str | None = None
    status: OperationStatus
    created_at: str
    expires_at: str


class OperationFetchResponse(BaseModel):
    operation: OperationPublic
    structured_data: dict[str, Any] = Field(default_factory=dict)
    service_config: dict[str, Any] | None = None


class OperationUpdateStructuredRequest(BaseModel):
    structured_data: dict[str, Any] = Field(default_factory=dict)


class OperationSetStatusRequest(BaseModel):
    status: OperationStatus


class OperationUnlockRequest(BaseModel):
    tab_session_id: str
