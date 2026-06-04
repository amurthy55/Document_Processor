"""
Admin mapping mode router for Manifest V3 extension.

Endpoints for field enumeration, mapping suggestions, and config generation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.mapping_engine import AdminMappingProcessor

router = APIRouter(tags=["admin"])


def _cfg_store(request: Request):
    return request.app.state.cfg_store


def _admin_processor(request: Request) -> AdminMappingProcessor:
    """Get or create admin processor from app state."""
    if not hasattr(request.app.state, "admin_processor"):
        configs_root = request.app.state.cfg_store._root
        request.app.state.admin_processor = AdminMappingProcessor(configs_root)
    return request.app.state.admin_processor


class AdminFieldInventoryRequest(BaseModel):
    """Request body for field inventory processing."""
    service_name: str
    url_patterns: list[str]
    fields: list[dict]  # Raw field data from extension


class AdminMappingApprovalRequest(BaseModel):
    """Request to finalize config after admin review."""
    service_name: str
    config: dict
    approved_mappings: dict[str, str | list[str]] | None = None


@router.post("/admin/process-fields")
def process_field_inventory(req: Request, body: AdminFieldInventoryRequest):
    """
    Process field inventory from admin mode.

    Receives enumerated form fields from extension in admin mode,
    suggests mappings to master schema, and returns draft config.

    Args:
        body.service_name: Name of service
        body.url_patterns: URL patterns for matching
        body.fields: List of form fields with id, name, type, label, etc.

    Returns:
        {
            suggestions: [{master_field, form_field, confidence, ...}],
            unmapped_schema_fields: [...],
            draft_config: {...}
        }
    """
    import logging
    log = logging.getLogger(__name__)
    log.info(f"Router: process_field_inventory called for service={body.service_name}")
    
    processor = _admin_processor(req)

    try:
        log.info(f"Router: Calling processor.process_field_inventory...")
        result = processor.process_field_inventory(
            service_name=body.service_name,
            url_patterns=body.url_patterns,
            fields=body.fields,
            min_confidence=0.3,
        )
        log.info(f"Router: processor.process_field_inventory completed successfully")
        return result
    except Exception as e:
        log.error(f"Router: Error in process_field_inventory: {e}")
        import traceback
        log.error(f"Router: Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Processing failed: {str(e)}")


@router.post("/admin/finalize-config")
def finalize_config(req: Request, body: AdminMappingApprovalRequest):
    """
    Finalize and save configuration after admin review.

    Args:
        body.service_name: Service name
        body.config: Draft configuration to save
        body.approved_mappings: Admin-approved field mappings

    Returns:
        {
            ok: bool,
            config_path: str,
            service_name: str,
            message: str,
            warning: str (optional)
        }
    """
    processor = _admin_processor(req)
    cfg_store = _cfg_store(req)

    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.warning(f"[finalize] configs_root={cfg_store._root!r}  service_name={body.service_name!r}")
    try:
        # Check for URL pattern conflicts
        new_patterns = set(body.config.get("url_patterns", []))
        conflicts = []
        
        # Check all existing configs for URL conflicts
        for service_id in cfg_store.list_services():
            try:
                existing_config = cfg_store.load(service_id).data
            except Exception:
                continue
            
            # Skip if it's the same service (by name)
            if existing_config.get("service_name") == body.service_name:
                continue
            
            existing_patterns = set(existing_config.get("url_patterns", []))
            if new_patterns & existing_patterns:
                conflicts.append({
                    "service": existing_config.get("service_name"),
                    "file": f"{service_id}.json",
                    "overlapping_patterns": list(new_patterns & existing_patterns)
                })
        
        # If conflicts exist, return error
        if conflicts:
            return {
                "ok": False,
                "error": "url_pattern_conflict",
                "message": f"URL patterns already used by '{conflicts[0]['service']}'",
                "conflicts": conflicts,
                "suggestion": f"Create new service '{body.service_name}' or use existing service '{conflicts[0]['service']}'"
            }
        
        config_path = processor.finalize_config(
            service_name=body.service_name,
            config=body.config,
            approved_mappings=body.approved_mappings,
        )
        
        response = {
            "ok": True,
            "config_path": str(config_path),
            "service_name": body.service_name,
            "message": "Configuration saved successfully"
        }
        
        if conflicts:
            response["warning"] = f"URL patterns overlap with existing service '{conflicts[0]['service']}'"
        
        return response
    except Exception as e:
        import traceback as _tb
        _log.error(f"[finalize] FULL TRACEBACK:\n{_tb.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Finalization failed: {str(e)}")
