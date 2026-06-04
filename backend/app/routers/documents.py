from __future__ import annotations

import asyncio
import io
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, BackgroundTasks
from pydantic import BaseModel

from ..optimized_processor import OptimizedProcessor

# ---------------------------------------------------------------------------
# File compression helpers
# ---------------------------------------------------------------------------
IMAGE_MAX_BYTES = 50 * 1024        # 50 KB for JPG/PNG
PDF_MAX_BYTES   = 200 * 1024       # 200 KB for PDF

def compress_image(file_path: Path) -> None:
    """Compress a JPG/PNG image in-place to under IMAGE_MAX_BYTES."""
    try:
        from PIL import Image
    except ImportError:
        print("[compress] Pillow not installed — skipping image compression")
        return

    if file_path.stat().st_size <= IMAGE_MAX_BYTES:
        return

    img = Image.open(file_path).convert("RGB")
    # Binary-search JPEG quality until under target size
    lo, hi = 5, 95
    best: bytes | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=mid, optimize=True)
        data = buf.getvalue()
        if len(data) <= IMAGE_MAX_BYTES:
            best = data
            lo = mid + 1          # try higher quality
        else:
            hi = mid - 1          # reduce quality
    if best is None:
        # Even quality=5 is too big — just save at quality 5
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=5, optimize=True)
        best = buf.getvalue()
    # Save back; rename to .jpg if needed
    out_path = file_path.with_suffix(".jpg")
    out_path.write_bytes(best)
    if out_path != file_path:
        file_path.unlink()
    print(f"[compress] Image compressed to {len(best)//1024}KB: {out_path.name}")


def compress_pdf(file_path: Path, post_ocr: bool = False) -> None:
    """Compress a PDF in-place to under PDF_MAX_BYTES using PyMuPDF.

    Pass 1 (always): lossless stream deflation — safe before or after OCR.
    Pass 2 (post_ocr=True only): re-render pages as JPEG at reduced DPI.
      This is lossy for the stored PDF but OCR/extraction has already run,
      so no text quality is lost. Achieves ~80-90% size reduction on scanned PDFs.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("[compress] PyMuPDF not installed — skipping PDF compression")
        return

    if file_path.stat().st_size <= PDF_MAX_BYTES:
        return

    doc = fitz.open(str(file_path))

    # Pass 1: lossless stream deflation
    data = doc.tobytes(deflate=True, garbage=4, clean=True)
    if len(data) <= PDF_MAX_BYTES:
        file_path.write_bytes(data)
        doc.close()
        print(f"[compress] PDF deflate-compressed to {len(data)//1024}KB: {file_path.name}")
        return

    if not post_ocr:
        # Pass 2 requires post_ocr=True — extraction hasn't run yet, skip re-render.
        doc.close()
        print(f"[compress] PDF deflate insufficient ({len(data)//1024}KB) — will re-render after extraction: {file_path.name}")
        return

    # Pass 2: re-render each page as a plain JPEG image and rebuild PDF.
    # Safe only post-OCR since this is lossy.
    # Strategy: try progressively lower JPEG quality at 96 DPI first (good visual quality),
    # then step down to 72 DPI if still over budget. Plain JPEG embedding — NOT pdfocr_tobytes —
    # so no garbage OCR text layer is added that would confuse re-extraction.
    import io
    print(f"[compress] PDF Pass 2 — re-rendering pages as JPEG: {file_path.name}")

    def _render_to_jpeg_pdf(src_doc, dpi: int, jpeg_quality: int) -> bytes:
        """Render all pages of src_doc to JPEG at given DPI/quality and return raw PDF bytes."""
        out = fitz.open()
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for page in src_doc:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
            jpeg_bytes = pix.tobytes("jpeg", jpg_quality=jpeg_quality)
            # Build a single-page PDF from the JPEG image
            tmp = fitz.open()
            tmp_page = tmp.new_page(width=pix.width, height=pix.height)
            tmp_page.insert_image(tmp_page.rect, stream=jpeg_bytes)
            out.insert_pdf(tmp)
            tmp.close()
        result = out.tobytes(deflate=True, garbage=4)
        out.close()
        return result

    candidate = None
    chosen_dpi = 120
    chosen_q = 75
    # Start at 120 DPI (1020×1320 px on A4) for good legibility, step down quality then DPI
    for dpi, quality in ((120, 75), (120, 60), (120, 45), (96, 70), (96, 55), (96, 40)):
        candidate = _render_to_jpeg_pdf(doc, dpi, quality)
        chosen_dpi, chosen_q = dpi, quality
        if len(candidate) <= PDF_MAX_BYTES:
            break

    doc.close()
    file_path.write_bytes(candidate)
    print(f"[compress] PDF re-rendered to {len(candidate)//1024}KB at {chosen_dpi}DPI/q{chosen_q}: {file_path.name}")

router = APIRouter(prefix="/documents", tags=["documents"])

class ProcessingStatus(BaseModel):
    operation_id: str
    status: str  # "processing", "active", "completed", "failed"
    progress: Optional[float] = None
    message: Optional[str] = None
    files_processed: Optional[int] = None
    total_files: Optional[int] = None

class ExtractionResult(BaseModel):
    operation_id: str
    merged_fields: Dict[str, Any]
    sources: Dict[str, Any]
    processing_time: float
    files_processed: int
    success_rate: float

# Global storage for processing status (in-memory; reset on server restart)
processing_status: Dict[str, ProcessingStatus] = {}

def _set_status(operation_id: str, **kwargs) -> None:
    """Update processing_status if entry exists; silently no-op if it was cleared (e.g. after server restart)."""
    entry = processing_status.get(operation_id)
    if entry is None:
        return
    for k, v in kwargs.items():
        setattr(entry, k, v)

# Resolve operations directory: use writable path from env var (set by Electron main.js),
# falling back to repo-relative path for dev mode.
import os as _os
OPS_DIR = Path(_os.environ.get("ESEVA_OPS_DIR") or Path(__file__).parent.parent.parent.parent / "operations")
print(f"[documents] OPS_DIR={OPS_DIR}")

@router.post("/process", response_model=ProcessingStatus)
async def process_documents(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    doc_type: str = Form(default="auto"),
):
    """Process uploaded documents and extract fields."""
    
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    
    # Generate operation ID
    operation_id = str(uuid.uuid4())
    
    # Validate file types
    allowed_extensions = {".pdf", ".jpg", ".jpeg", ".png", ".tiff"}
    for file in files:
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"File {file.filename} has unsupported extension {file_ext}"
            )
    
    # Initialize status
    status = ProcessingStatus(
        operation_id=operation_id,
        status="processing",
        progress=0.0,
        message="Starting document processing...",
        files_processed=0,
        total_files=len(files)
    )
    processing_status[operation_id] = status
    
    # Save uploaded files to operation directory
    operation_dir = OPS_DIR / operation_id
    operation_dir.mkdir(parents=True, exist_ok=True)
    (operation_dir / "extracted_data").mkdir(exist_ok=True)
    (operation_dir / "originals").mkdir(exist_ok=True)

    saved_files: List[str] = []
    for file in files:
        file_path = operation_dir / file.filename
        content = await file.read()
        file_path.write_bytes(content)

        # Preserve original uploads so QR detection can use the highest-quality source.
        original_path = operation_dir / "originals" / file.filename
        original_path.write_bytes(content)

        # Compress in-place before processing
        ext = file_path.suffix.lower()
        if ext in (".jpg", ".jpeg", ".png"):
            compress_image(file_path)
            # Path may have changed to .jpg if original was .png
            file_path = file_path.with_suffix(".jpg")
        elif ext == ".pdf":
            compress_pdf(file_path)

        saved_files.append(str(file_path))

    # Start background processing (runs in thread so blocking calls don't stall event loop)
    background_tasks.add_task(process_documents_background, operation_id, saved_files, doc_type)

    return status

def _run_processing(operation_id: str, file_paths: List[str], doc_type: str = "auto") -> None:
    """Synchronous processing — runs in a thread pool so blocking I/O is safe."""
    operation_dir = OPS_DIR / operation_id
    operation_dir.mkdir(parents=True, exist_ok=True)
    (operation_dir / "extracted_data").mkdir(exist_ok=True)

    processor = OptimizedProcessor()

    IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

    for i, file_path in enumerate(file_paths):
        progress = (i / len(file_paths)) * 0.8 + 0.1
        _set_status(operation_id, progress=progress,
                    message=f"Processing {Path(file_path).name}...",
                    files_processed=i)

        ext = Path(file_path).suffix.lower()
        if ext in IMAGE_EXTS:
            # Images are passport photos — already compressed on upload, no extraction needed
            print(f"[documents] image stored (no extraction): {Path(file_path).name}")
            continue

        try:
            result = processor.process_document(Path(file_path_obj := Path(file_path)), operation_id, doc_type=doc_type)
            if result.get("success"):
                processor.save_results(result, operation_dir)
            else:
                print(f"[documents] processing failed for {file_path_obj.name}: {result.get('error')}")
        except Exception as exc:
            print(f"[documents] exception on {Path(file_path).name}: {exc}")

        # Post-OCR compression: now safe to re-render pages at lower DPI
        if ext == ".pdf":
            compress_pdf(Path(file_path), post_ocr=True)

    # Merge individual field files into one summary
    _set_status(operation_id, message="Merging results...", progress=0.9)

    merged = processor.merge_operation_results(str(operation_dir))
    merged_file = operation_dir / "extracted_data" / "merged_fields.json"
    processor._save_json(merged, str(merged_file))

    _set_status(operation_id, status="active", progress=1.0,
                message=f"Processed {len(file_paths)} document(s)",
                files_processed=len(file_paths))


async def process_documents_background(operation_id: str, file_paths: List[str], doc_type: str = "auto") -> None:
    """Async wrapper — delegates all blocking work to a thread."""
    try:
        await asyncio.to_thread(_run_processing, operation_id, file_paths, doc_type)
    except Exception as exc:
        _set_status(operation_id, status="failed", message=f"Processing failed: {exc}")
        print(f"[documents] background task failed for {operation_id}: {exc}")

@router.post("/reprocess/{operation_id}", response_model=ProcessingStatus)
async def reprocess_operation(
    background_tasks: BackgroundTasks,
    operation_id: str,
    doc_type: str = Form(default="auto"),
):
    """Re-run processing on an operation whose files are already on disk.
    Useful when the backend was restarted before processing finished."""
    operation_dir = OPS_DIR / operation_id
    if not operation_dir.exists():
        raise HTTPException(status_code=404, detail="Operation directory not found")

    IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
    all_files = [f for f in operation_dir.iterdir() if f.is_file()]
    if not all_files:
        raise HTTPException(status_code=400, detail="No files found in operation directory")

    file_paths = [str(f) for f in all_files]

    status = ProcessingStatus(
        operation_id=operation_id,
        status="processing",
        progress=0.0,
        message="Reprocessing...",
        files_processed=0,
        total_files=len(file_paths),
    )
    processing_status[operation_id] = status
    background_tasks.add_task(process_documents_background, operation_id, file_paths, doc_type)
    return status


@router.get("/files/{operation_id}")
async def list_operation_files(operation_id: str):
    """List all files stored in an operation directory (used by the extension for photo auto-upload)."""
    operation_dir = OPS_DIR / operation_id
    if not operation_dir.exists():
        raise HTTPException(status_code=404, detail="Operation not found")
    IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
    files = []
    for f in operation_dir.iterdir():
        if f.is_file():
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "is_image": f.suffix.lower() in IMAGE_EXTS,
                "url": f"/api/documents/download/{operation_id}/{f.name}",
            })
    return {"files": sorted(files, key=lambda x: x["name"])}


@router.get("/download/{operation_id}/{filename}")
async def download_operation_file(operation_id: str, filename: str):
    """Serve a file from an operation directory (e.g. compressed passport photo)."""
    from fastapi.responses import FileResponse
    file_path = OPS_DIR / operation_id / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # Prevent path traversal
    try:
        file_path.resolve().relative_to((OPS_DIR / operation_id).resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    return FileResponse(str(file_path))


@router.get("/status/{operation_id}", response_model=ProcessingStatus)
async def get_processing_status(operation_id: str):
    """Get the status of a document processing operation."""
    if operation_id not in processing_status:
        raise HTTPException(status_code=404, detail="Operation not found")
    
    return processing_status[operation_id]

@router.get("/results/{operation_id}/raw")
async def get_extraction_results_raw(operation_id: str):
    """Get extraction results directly from file system (for testing)."""
    merged_file = OPS_DIR / operation_id / "extracted_data" / "merged_fields.json"
    if not merged_file.exists():
        raise HTTPException(status_code=404, detail="Results not found")

    with open(merged_file, "r") as f:
        return json.load(f)

@router.get("/results/{operation_id}", response_model=ExtractionResult)
async def get_extraction_results(operation_id: str):
    """Get the extraction results for a completed operation."""
    if operation_id not in processing_status:
        raise HTTPException(status_code=404, detail="Operation not found")
    
    status = processing_status[operation_id]
    if status.status not in ("active", "completed"):
        raise HTTPException(
            status_code=400, 
            detail=f"Operation not ready. Current status: {status.status}"
        )
    
    # Load merged results
    merged_file = OPS_DIR / operation_id / "extracted_data" / "merged_fields.json"
    if not merged_file.exists():
        raise HTTPException(status_code=404, detail="Results not found")

    with open(merged_file, "r") as f:
        merged_data = json.load(f)

    # Extract sources and strip metadata keys — keep only field data
    sources = merged_data.pop("sources", {})
    for meta_key in ("operation_id", "files_processed", "processing_time", "success_rate"):
        merged_data.pop(meta_key, None)

    # Calculate processing metrics
    operation_dir = OPS_DIR / operation_id
    processed_files = [f for f in (operation_dir / "extracted_data").glob("*_fields.json") if f.name != "merged_fields.json"]
    processing_time = 0.0  # Could be stored during processing
    success_rate = len(processed_files) / status.total_files if status.total_files else 0.0
    
    return ExtractionResult(
        operation_id=operation_id,
        merged_fields=merged_data,
        sources=sources,
        processing_time=processing_time,
        files_processed=len(processed_files),
        success_rate=success_rate
    )

@router.post("/operations/{operation_id}/complete")
async def complete_operation(operation_id: str):
    """Mark a document operation as completed. Called by the extension End Operation button."""
    operation_dir = OPS_DIR / operation_id
    if operation_id not in processing_status and not operation_dir.exists():
        raise HTTPException(status_code=404, detail="Operation not found")

    # Update in-memory status if present
    if operation_id in processing_status:
        processing_status[operation_id].status = "completed"

    # Write status.json to disk so state survives server restarts
    operation_dir.mkdir(parents=True, exist_ok=True)
    status_file = operation_dir / "status.json"
    with open(status_file, "w") as f:
        json.dump({"status": "completed"}, f)

    return {"operation_id": operation_id, "status": "completed"}


@router.delete("/operations/{operation_id}")
async def delete_operation(operation_id: str):
    """Delete an operation and all its data (works even after server restart)."""
    operation_dir = OPS_DIR / operation_id
    if operation_id not in processing_status and not operation_dir.exists():
        raise HTTPException(status_code=404, detail="Operation not found")

    processing_status.pop(operation_id, None)

    if operation_dir.exists():
        import shutil
        shutil.rmtree(operation_dir)

    return {"message": "Operation deleted successfully"}


@router.delete("/operations")
async def delete_operations_bulk(operation_ids: List[str]):
    """Delete multiple operations by ID. Used for cleanup of old operations."""
    import shutil
    deleted = []
    errors = []
    for op_id in operation_ids:
        try:
            processing_status.pop(op_id, None)
            op_dir = OPS_DIR / op_id
            if op_dir.exists():
                shutil.rmtree(op_dir)
            deleted.append(op_id)
        except Exception as exc:
            errors.append({"operation_id": op_id, "error": str(exc)})
    return {"deleted": deleted, "errors": errors}

@router.get("/operations")
async def list_operations():
    """List all operations — merges in-memory status with on-disk folders.

    Returns operations sorted newest-first (by folder mtime when not in memory).
    Each entry includes `name` extracted from merged_fields.json if available.
    """
    import os
    import time

    ops: List[Dict[str, Any]] = []

    # Collect all operation IDs: union of in-memory and on-disk
    disk_ids: set = set()
    if OPS_DIR.exists():
        for entry in OPS_DIR.iterdir():
            if entry.is_dir():
                disk_ids.add(entry.name)

    all_ids = disk_ids | set(processing_status.keys())

    for op_id in all_ids:
        op_dir = OPS_DIR / op_id
        mem = processing_status.get(op_id)

        # Determine status
        if mem:
            status_str = mem.status
            progress = mem.progress
            message = mem.message
            files_processed = mem.files_processed
            total_files = mem.total_files
        else:
            # On-disk only — prefer explicit status.json, then fall back to merged_fields check
            status_file = op_dir / "status.json"
            if status_file.exists():
                try:
                    with open(status_file) as sf:
                        status_str = json.load(sf).get("status", "unknown")
                except Exception:
                    status_str = "unknown"
            else:
                merged = op_dir / "extracted_data" / "merged_fields.json"
                status_str = "active" if merged.exists() else "unknown"
            progress = 1.0 if status_str in ("active", "completed") else None
            message = None
            files_processed = None
            total_files = None

        # Extract name from merged_fields.json if present
        extracted_name = None
        merged_path = op_dir / "extracted_data" / "merged_fields.json"
        if merged_path.exists():
            try:
                with open(merged_path) as f:
                    mf = json.load(f)
                extracted_name = (
                    mf.get("name")
                    or mf.get("child_name")
                    or mf.get("applicant_name")
                )
            except Exception:
                pass

        # Folder mtime as creation proxy
        created_at = None
        if op_dir.exists():
            try:
                created_at = int(os.path.getmtime(op_dir) * 1000)  # ms epoch
            except Exception:
                pass

        ops.append({
            "operation_id": op_id,
            "status": status_str,
            "progress": progress,
            "message": message,
            "files_processed": files_processed,
            "total_files": total_files,
            "name": extracted_name,
            "created_at": created_at,
        })

    # Sort newest-first
    ops.sort(key=lambda o: o["created_at"] or 0, reverse=True)

    return {"operations": ops}


@router.delete("/operations/{operation_id}")
async def delete_operation(operation_id: str):
    """Delete an operation folder and all its files."""
    import shutil
    operation_dir = OPS_DIR / operation_id
    if not operation_dir.exists():
        raise HTTPException(status_code=404, detail="Operation not found")
    
    try:
        shutil.rmtree(operation_dir)
        # Remove from in-memory status if present
        if operation_id in processing_status:
            del processing_status[operation_id]
        return {"message": f"Operation {operation_id} deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete operation: {str(e)}")


@router.delete("/operations")
async def delete_all_operations():
    """Delete all operation folders."""
    import shutil
    if not OPS_DIR.exists():
        return {"message": "No operations directory found"}
    
    try:
        # Count operations before deleting
        operation_dirs = [d for d in OPS_DIR.iterdir() if d.is_dir()]
        count = len(operation_dirs)
        
        # Delete all operation directories
        for operation_dir in operation_dirs:
            shutil.rmtree(operation_dir)
        
        # Clear in-memory status
        processing_status.clear()
        
        return {"message": f"Deleted {count} operations successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete operations: {str(e)}")
