from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from app.operations.models import (
    OperationCreateRequest,
    OperationCreateResponse,
    OperationFetchResponse,
    OperationSetStatusRequest,
    OperationStatus,
    OperationUnlockRequest,
    OperationUpdateStructuredRequest,
)

router = APIRouter(tags=["operations"])


def _op_store(request: Request):
    return request.app.state.op_store


def _cfg_store(request: Request):
    return request.app.state.cfg_store


@router.post("/operations", response_model=OperationCreateResponse)
def create_operation(req: Request, body: OperationCreateRequest):
    op, token, csrf_token = _op_store(req).create_operation(body.selected_service)
    return OperationCreateResponse(
        operation_id=op.operation_id,
        session_token=token,
        csrf_token=csrf_token,
        selected_service=op.selected_service,
        status=op.status,
        created_at=op.created_at,
        expires_at=op.expires_at,
    )


@router.get("/operation/{operation_id}", response_model=OperationFetchResponse)
def fetch_operation(
    req: Request,
    operation_id: str,
    token: str,
    x_tab_session_id: str | None = Header(default=None),
):
    store = _op_store(req)
    row = store.get_operation_row(operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="operation_not_found")

    try:
        store.require_valid_token(row, token)
    except PermissionError:
        raise HTTPException(status_code=403, detail="invalid_operation_token")

    if x_tab_session_id:
        try:
            store.lock_to_tab(operation_id, x_tab_session_id)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))

    op_public = store.get_public(store.get_operation_row(operation_id))
    structured = store.read_structured_data(row)

    service_config = None
    if op_public.selected_service:
        try:
            service_config = _cfg_store(req).load(op_public.selected_service).data
        except FileNotFoundError:
            service_config = None

    return OperationFetchResponse(
        operation=op_public,
        structured_data=structured,
        service_config=service_config,
    )


@router.post("/operation/{operation_id}/structured")
def update_structured(
    req: Request,
    operation_id: str,
    token: str,
    body: OperationUpdateStructuredRequest,
    x_csrf_token: str | None = Header(default=None),
):
    store = _op_store(req)
    row = store.get_operation_row(operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    try:
        store.require_valid_token(row, token)
    except PermissionError:
        raise HTTPException(status_code=403, detail="invalid_operation_token")

    if not x_csrf_token:
        raise HTTPException(status_code=403, detail="missing_csrf_token")
    try:
        store.require_valid_csrf(row, x_csrf_token)
    except PermissionError:
        raise HTTPException(status_code=403, detail="invalid_csrf_token")

    store.write_structured_data(row, body.structured_data)
    return {"ok": True}


@router.post("/operation/{operation_id}/status")
def set_status(
    req: Request,
    operation_id: str,
    token: str,
    body: OperationSetStatusRequest,
    x_csrf_token: str | None = Header(default=None),
):
    store = _op_store(req)
    row = store.get_operation_row(operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    try:
        store.require_valid_token(row, token)
    except PermissionError:
        raise HTTPException(status_code=403, detail="invalid_operation_token")

    if not x_csrf_token:
        raise HTTPException(status_code=403, detail="missing_csrf_token")
    try:
        store.require_valid_csrf(row, x_csrf_token)
    except PermissionError:
        raise HTTPException(status_code=403, detail="invalid_csrf_token")

    store.set_status(operation_id, body.status)
    return {"ok": True}


@router.post("/operation/{operation_id}/unlock")
def unlock(
    req: Request,
    operation_id: str,
    token: str,
    body: OperationUnlockRequest,
    x_csrf_token: str | None = Header(default=None),
):
    store = _op_store(req)
    row = store.get_operation_row(operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    try:
        store.require_valid_token(row, token)
    except PermissionError:
        raise HTTPException(status_code=403, detail="invalid_operation_token")

    if not x_csrf_token:
        raise HTTPException(status_code=403, detail="missing_csrf_token")
    try:
        store.require_valid_csrf(row, x_csrf_token)
    except PermissionError:
        raise HTTPException(status_code=403, detail="invalid_csrf_token")

    try:
        store.unlock(operation_id, body.tab_session_id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"ok": True}
