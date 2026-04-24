from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..optimized_processor import OptimizedProcessor

router = APIRouter()

class SyncExtractionResult(BaseModel):
    operation_id: str
    success: bool
    fields: Dict[str, Any]
    error: str = None
    processing_time: float

@router.post("/process-sync", response_model=SyncExtractionResult)
async def process_documents_sync(files: List[UploadFile] = File(...)):
    """Process documents synchronously and return results immediately."""
    import time
    import json
    
    # Generate operation ID
    operation_id = str(uuid.uuid4())
    
    # Create operation directory (use writable path from env var set by Electron)
    import os as _os
    _ops_root = Path(_os.environ.get("ESEVA_OPS_DIR") or Path(__file__).parent.parent.parent.parent / "operations")
    operations_dir = _ops_root / operation_id
    operations_dir.mkdir(parents=True, exist_ok=True)
    (operations_dir / "extracted_data").mkdir(exist_ok=True)
    (operations_dir / "originals").mkdir(exist_ok=True)
    
    # Initialize processor
    processor = OptimizedProcessor()
    
    # Process each file and merge results
    all_fields = {}
    processing_time = 0.0
    error = None
    
    try:
        for file in files:
            # Save uploaded file
            file_path = operations_dir / file.filename
            content = await file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            with open(operations_dir / "originals" / file.filename, "wb") as f:
                f.write(content)
            
            # Process document
            start_time = time.time()
            result = processor.process_document(file_path, operation_id)
            processing_time += time.time() - start_time
            
            if result.get('success', False):
                # Save individual results
                processor.save_results(result, operations_dir)
                
                # Merge fields
                fields = result.get('fields', {})
                for key, value in fields.items():
                    if value and (key not in all_fields or not all_fields[key]):
                        all_fields[key] = value
            else:
                error = result.get('error', 'Unknown error')
        
        # Save merged results
        merged_data = {
            **all_fields,
            "operation_id": operation_id,
            "files_processed": len(files),
            "processing_time": processing_time,
            "success_rate": len([f for f in files if not error]) / len(files) if files else 0.0
        }
        
        merged_file = operations_dir / "extracted_data" / "merged_fields.json"
        with open(merged_file, "w") as f:
            json.dump(merged_data, f, indent=2)
        
        return SyncExtractionResult(
            operation_id=operation_id,
            success=error is None,
            fields=all_fields,
            error=error,
            processing_time=processing_time
        )
        
    except Exception as e:
        return SyncExtractionResult(
            operation_id=operation_id,
            success=False,
            fields={},
            error=str(e),
            processing_time=processing_time
        )
