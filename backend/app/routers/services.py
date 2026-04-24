from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["services"])


def _cfg_store(request: Request):
    return request.app.state.cfg_store


@router.get("/services")
def list_services(req: Request):
    return {"services": _cfg_store(req).list_services()}


@router.get("/services/match")
def match_service(req: Request, url: str = Query(...)):
    cfg = _cfg_store(req).match_by_url(url)
    if cfg is None:
        return {"service_id": None, "config": None}
    return {"service_id": cfg.service_id, "config": cfg.data}


@router.get("/services/{service_id}/config")
def get_config(req: Request, service_id: str):
    try:
        cfg = _cfg_store(req).load(service_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="service_config_not_found")
    return cfg.data
