"""
Optimized document processor for the eSeva desktop app.
Combines fast text extraction with OCR for scanned documents.
"""

import html
from html.parser import HTMLParser
import re
import time
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

from .improved_extractor import ImprovedExtractor


class _SimpleTableParser(HTMLParser):
    """Extract HTML table rows into cell lists."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_cell = False
        self._current_cell: list[str] = []
        self._current_row: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []
        elif tag == "tr":
            self._current_row = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            self._in_cell = False
            cell = html.unescape(" ".join(self._current_cell)).strip()
            cell = re.sub(r"\s+", " ", cell)
            if cell:
                self._current_row.append(cell)
        elif tag == "tr":
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = []


class OptimizedProcessor:
    """Optimized processor for scanned PDFs with smart extraction."""
    
    def __init__(self):
        self.extractor = ImprovedExtractor()
    
    def process_document(
        self,
        file_path: Path,
        operation_id: str,
        max_ocr_time: int = 120,
        doc_type: str = "auto",
    ) -> Dict[str, Any]:
        """
        Process a document with optimal strategy.

        Args:
            file_path: Path to document file
            operation_id: Operation ID for folder organisation
            max_ocr_time: Maximum seconds to spend on OCR
            doc_type: "auto" | "digital" | "scanned"
                      - "digital": skip OCR entirely, use direct text extraction
                      - "scanned": always run OCR pipeline
                      - "auto": run OCR only if embedded text is < 50 chars

        Returns:
            Dict with extracted data and metadata
        """
        result = {
            "operation_id": operation_id,
            "filename": file_path.name,
            "processing_time": 0.0,
            "method": "unknown",
            "success": False,
            "fields": {},
            "text": "",
            "confidence": 0.0,
            "needs_ocr": False,
            "error": None,
            "debug": {
                "qr": {
                    "attempted": False,
                    "status": "not_started",
                    "source_path": None,
                    "payload_preview": None,
                    "used_url_fetch": False,
                    "fetched_url": None,
                    "reason": None,
                }
            },
        }
        
        start = time.time()
        
        try:
            if not HAS_PYMUPDF:
                result["error"] = "PyMuPDF not available"
            else:
                # Check if this is an Aadhaar document (filename-based check)
                is_aadhaar = self._should_skip_qr(doc_type, "", file_path.name)
                
                if is_aadhaar:
                    # Aadhaar documents: Skip QR, use direct text extraction only
                    text = self._extract_text_fast(file_path) if doc_type != "scanned" else ""
                    has_text = len(text.strip()) > 50
                    
                    if has_text and doc_type != "scanned":
                        # Try fast text path first
                        extracted = self.extractor.extract_smart_combined(text)
                        meaningful = {k: v for k, v in extracted.items() if v}

                        if meaningful:
                            # Digital PDF with usable fields — done
                            result["method"] = "fast_text"
                            result["text"] = text
                            result["confidence"] = 0.95
                            result["success"] = True
                            result["fields"] = extracted
                        else:
                            # Embedded text exists but yielded no fields — fall through to Tesseract
                            print(f"[processor] fast_text found no fields — falling back to Tesseract: {file_path.name}")
                            has_text = False

                    if not has_text or doc_type == "scanned":
                        if doc_type == "digital":
                            # Operator said digital but no usable text — extract what little there is
                            result["method"] = "fast_text"
                            result["text"] = text
                            result["confidence"] = 0.5
                            result["success"] = True
                            result["fields"] = self.extractor.extract_smart_combined(text)
                        else:
                            # Scanned document — run OpenCV → Tesseract OCR pipeline
                            result["needs_ocr"] = True
                            ocr_text = self._apply_ocr_tesseract(file_path)

                            if ocr_text:
                                result["method"] = "tesseract"
                                result["text"] = ocr_text
                                result["confidence"] = 0.85
                                result["success"] = True
                                extracted = self.extractor.extract_smart_combined(ocr_text)
                                result["fields"] = self._merge_field_dicts(extracted, result["fields"])
                            else:
                                if not result["success"]:
                                    result["error"] = "OCR failed — no text extracted"
                else:
                    # Non-Aadhaar documents: Try QR first, stop if successful
                    text = self._extract_text_fast(file_path) if doc_type != "scanned" else ""
                    qr_fields, qr_text, qr_method, qr_debug = self._extract_qr_fields(file_path, doc_type, text)
                    result["debug"]["qr"] = qr_debug

                    if qr_fields:
                        # QR extraction successful - STOP, don't try other methods
                        if qr_text:
                            result["text"] = qr_text
                        result["method"] = qr_method
                        result["confidence"] = 0.98
                        result["success"] = True
                        result["fields"] = qr_fields
                        return result
                    else:
                        # QR failed, try direct text extraction
                        has_text = len(text.strip()) > 50

                        if has_text and doc_type != "scanned":
                            # Try fast text path first
                            extracted = self.extractor.extract_smart_combined(text)
                            meaningful = {k: v for k, v in extracted.items() if v}

                            if meaningful:
                                # Digital PDF with usable fields — done
                                result["method"] = "fast_text"
                                result["text"] = text
                                result["confidence"] = 0.95
                                result["success"] = True
                                result["fields"] = extracted
                            else:
                                # Embedded text exists but yielded no fields — fall through to Tesseract
                                print(f"[processor] fast_text found no fields — falling back to Tesseract: {file_path.name}")
                                has_text = False

                        if not has_text or doc_type == "scanned":
                            if doc_type == "digital":
                                # Operator said digital but no usable text — extract what little there is
                                result["method"] = "fast_text"
                                result["text"] = text
                                result["confidence"] = 0.5
                                result["success"] = True
                                result["fields"] = self.extractor.extract_smart_combined(text)
                            else:
                                # Scanned document — run OpenCV → Tesseract OCR pipeline
                                result["needs_ocr"] = True
                                ocr_text = self._apply_ocr_tesseract(file_path)

                                if ocr_text:
                                    result["method"] = "tesseract"
                                    result["text"] = ocr_text
                                    result["confidence"] = 0.85
                                    result["success"] = True
                                    extracted = self.extractor.extract_smart_combined(ocr_text)
                                    result["fields"] = self._merge_field_dicts(extracted, result["fields"])
                                else:
                                    if not result["success"]:
                                        result["error"] = "OCR failed — no text extracted"
                
        except Exception as e:
            result["error"] = str(e)
        
        result["processing_time"] = time.time() - start
        return result
    
    def _extract_text_fast(self, file_path: Path) -> str:
        """Extract text using PyMuPDF (fast)."""
        text = ""
        doc = fitz.open(file_path)
        
        # Process first 3 pages max
        for page_num in range(min(len(doc), 3)):
            page = doc[page_num]
            text += page.get_text() + "\n"
        
        doc.close()
        return text
    
    def _apply_ocr_tesseract(self, file_path: Path) -> Optional[str]:
        """OCR pipeline: PyMuPDF renders pages → OpenCV preprocesses → Tesseract reads text.

        Replaces the old ocrmypdf subprocess call. Works on Windows and Linux
        with no Ghostscript dependency.
        """
        try:
            import cv2
            import numpy as np
            import pytesseract
        except ImportError as exc:
            print(f"[ocr] Missing dependency: {exc} — cannot run Tesseract OCR")
            return None

        all_text: list[str] = []

        try:
            doc = fitz.open(str(file_path))
        except Exception as exc:
            print(f"[ocr] Cannot open {file_path.name}: {exc}")
            return None

        for page_num in range(min(len(doc), 5)):
            try:
                page = doc[page_num]

                # Render page at 300 DPI for good OCR quality
                mat = fitz.Matrix(300 / 72, 300 / 72)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_bytes = pix.tobytes("png")

                # Decode to OpenCV array
                arr = np.frombuffer(img_bytes, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    continue

                # --- OpenCV preprocessing ---
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

                # Denoise
                gray = cv2.fastNlMeansDenoising(gray, h=10)

                # Adaptive threshold — handles uneven lighting / faded ink
                binary = cv2.adaptiveThreshold(
                    gray, 255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY, 31, 10
                )

                # Tesseract OCR (English only)
                page_text = pytesseract.image_to_string(
                    binary,
                    lang="eng",
                    config="--psm 3 --oem 1",
                )
                all_text.append(page_text)

            except Exception as exc:
                print(f"[ocr] Page {page_num} error: {exc}")
                continue

        doc.close()
        combined = "\n".join(all_text).strip()
        return combined if combined else None

    def _extract_qr_fields(
        self,
        file_path: Path,
        doc_type: str,
        extracted_text: str,
    ) -> tuple[Dict[str, Any], str, Optional[str], Dict[str, Any]]:
        """Decode non-Aadhaar QR payloads and convert them into extracted fields."""
        qr_source_path = self._resolve_qr_source_path(file_path)
        debug = {
            "attempted": False,
            "status": "not_started",
            "source_path": str(qr_source_path),
            "payload_preview": None,
            "used_url_fetch": False,
            "fetched_url": None,
            "reason": None,
        }

        if self._should_skip_qr(doc_type, extracted_text, file_path.name):
            debug["status"] = "skipped"
            debug["reason"] = "aadhaar_like_document"
            return {}, "", None, debug

        debug["attempted"] = True

        payload = self._decode_qr_payload(qr_source_path)
        if payload:
            compact_preview = re.sub(r"\s+", " ", payload).strip()
            debug["payload_preview"] = compact_preview[:200]
        if not payload or self._looks_like_aadhaar_qr(payload):
            debug["status"] = "no_usable_payload"
            debug["reason"] = "aadhaar_like_payload" if payload else "no_qr_detected"
            return {}, "", None, debug

        source_text = payload
        method = "qr_text"
        if self._looks_like_url(payload):
            debug["used_url_fetch"] = True
            debug["fetched_url"] = payload.strip()
            fetched = self._fetch_qr_url_text(payload)
            if not fetched:
                debug["status"] = "url_fetch_failed"
                debug["reason"] = "qr_url_fetch_failed"
                return {}, "", None, debug
            source_text = fetched
            method = "qr_url"
            # Use direct mapping for QR URL data to avoid pattern matching issues
            fields = self._map_qr_structured_data(source_text)
        else:
            # For non-URL QR data, use regular extraction
            fields = self.extractor.extract_smart_combined(source_text)
        
        meaningful = {k: v for k, v in fields.items() if v}
        if meaningful:
            debug["status"] = "fields_extracted"
            debug["reason"] = f"{len(meaningful)}_fields"
            return meaningful, source_text, method, debug
        debug["status"] = "decoded_but_no_fields"
        debug["reason"] = "qr_payload_not_mappable"
        return {}, source_text, method, debug

    @staticmethod
    def _resolve_qr_source_path(file_path: Path) -> Path:
        """Prefer the preserved original upload for QR detection when available."""
        original_path = file_path.parent / "originals" / file_path.name
        return original_path if original_path.exists() else file_path

    @staticmethod
    def _should_skip_qr(doc_type: str, extracted_text: str, filename: str) -> bool:
        """Skip QR decode for Aadhaar-like documents."""
        aadhaar_markers = f"{doc_type} {filename} {extracted_text[:500]}".lower()
        return any(marker in aadhaar_markers for marker in ("aadhaar", "aadhar", "uidai", "e aadhaar", "resident"))

    @staticmethod
    def _looks_like_aadhaar_qr(payload: str) -> bool:
        """Aadhaar secure QR payloads are long opaque numeric blobs, not URL/table text."""
        compact = re.sub(r"\s+", "", payload)
        if compact.isdigit() and len(compact) > 200:
            return True
        return "uidai" in payload.lower() and "http" not in payload.lower()

    @staticmethod
    def _looks_like_url(payload: str) -> bool:
        parsed = urlparse(payload.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def _decode_qr_payload(self, file_path: Path) -> Optional[str]:
        """Decode the first QR payload found in a PDF or image."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None

        detector = cv2.QRCodeDetector()

        def _decode_image(img) -> Optional[str]:
            # Try multiple detection methods with preprocessing
            detection_methods = [
                # Method 1: Direct detection (original)
                lambda: self._try_qr_detection(detector, img),
                # Method 2: Preprocessed with contrast enhancement
                lambda: self._try_qr_detection(detector, self._preprocess_for_qr(img)),
                # Method 3: Denoised version
                lambda: self._try_qr_detection(detector, self._denoise_for_qr(img)),
            ]
            
            for method in detection_methods:
                try:
                    result = method()
                    if result:
                        return result
                except Exception:
                    continue
            return None

        if file_path.suffix.lower() == ".pdf":
            try:
                doc = fitz.open(str(file_path))
            except Exception:
                return None
            try:
                for page_num in range(min(len(doc), 5)):  # Check more pages
                    page = doc[page_num]
                    # Use higher resolution for better QR detection (300 DPI instead of 200)
                    pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
                    arr = np.frombuffer(pix.tobytes("png"), dtype=np.uint8)
                    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if img is None:
                        continue
                    value = _decode_image(img)
                    if value:
                        return value
            finally:
                doc.close()
            return None

        img = cv2.imread(str(file_path))
        if img is None:
            return None
        return _decode_image(img)
    
    @staticmethod
    def _try_qr_detection(detector, img) -> Optional[str]:
        """Try QR detection with both multi and single detection methods."""
        try:
            # Try multi-detection first
            ok, decoded_info, _points, _ = detector.detectAndDecodeMulti(img)
            if ok and decoded_info:
                for value in decoded_info:
                    value = (value or "").strip()
                    if value:
                        return value
        except Exception:
            pass
        
        try:
            # Fall back to single detection
            value, _points, _ = detector.detectAndDecode(img)
            value = (value or "").strip()
            return value or None
        except Exception:
            return None
    
    @staticmethod
    def _preprocess_for_qr(img) -> Any:
        """Preprocess image for QR detection with contrast enhancement."""
        try:
            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Apply adaptive threshold for better contrast
            binary = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                cv2.THRESH_BINARY, 11, 2
            )
            
            # Convert back to 3-channel for QR detector
            return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        except Exception:
            return img
    
    @staticmethod
    def _denoise_for_qr(img) -> Any:
        """Denoise image for QR detection."""
        try:
            # Apply denoising
            denoised = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)
            return denoised
        except Exception:
            return img

    def _fetch_qr_url_text(self, url: str) -> Optional[str]:
        """Fetch a QR URL and normalize HTML tables into labelled text."""
        try:
            request = Request(
                url.strip(),
                headers={"User-Agent": "Mozilla/5.0 eSevaCenter/1.0"},
            )
            with urlopen(request, timeout=15) as response:
                content_type = response.headers.get("Content-Type", "")
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, ValueError):
            return None

        charset_match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
        encoding = charset_match.group(1) if charset_match else "utf-8"
        html_text = raw.decode(encoding, errors="ignore")
        parser = _SimpleTableParser()
        parser.feed(html_text)

        lines: list[str] = []
        for row in parser.rows:
            if len(row) >= 2:
                key = row[0].strip().rstrip(":")
                value = " ".join(cell.strip() for cell in row[1:] if cell.strip())
                if key and value:
                    lines.append(f"{key}: {value}")

        if lines:
            return "\n".join(lines)

        visible_text = re.sub(r"<[^>]+>", "\n", html_text)
        visible_text = html.unescape(visible_text)
        visible_text = re.sub(r"[^\S\n]+", " ", visible_text)
        visible_text = re.sub(r"\n\s*\n+", "\n", visible_text)
        return visible_text.strip() or None

    @staticmethod
    def _merge_field_dicts(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
        """Merge fields without duplicating or overwriting populated values."""
        merged = dict(primary)
        for field, value in secondary.items():
            if value and not merged.get(field):
                merged[field] = value
        return merged
    
    @staticmethod
    def _map_qr_structured_data(text: str) -> Dict[str, Any]:
        """Direct mapping from structured QR URL data to master schema fields.
        
        This bypasses extract_smart_combined() for QR URL data since we already
        have clean key-value pairs from HTML table parsing.
        """
        fields = {}
        
        # Parse the structured text line by line
        for line in text.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip().lower()
                value = value.strip()
                
                # Direct mapping to master schema
                if key in ['child name', 'name']:
                    fields['name'] = value
                elif key in ['date of birth', 'dob']:
                    fields['dob'] = value.replace('-', '/')
                elif key in ['sex', 'gender']:
                    fields['gender'] = value.lower()
                elif key in ['father name']:
                    fields['father_name'] = value
                elif key in ['mother name']:
                    fields['mother_name'] = value
                elif key in ['permanent address', 'address']:
                    fields['address'] = value
                elif key in ['place of birth']:
                    fields['place_of_birth'] = value
                elif key in ['registration no', 'registration number']:
                    fields['registration_no'] = value
                elif key in ['date of regn', 'date of registration']:
                    fields['registration_date'] = value
                elif key in ['district']:
                    fields['district'] = value
                elif key in ['sub-district']:
                    fields['sub_district'] = value
                elif key in ['pincode', 'pin code']:
                    # Extract 6-digit pincode from address if not directly provided
                    pin_match = re.search(r'\b(\d{6})\b', value)
                    if pin_match:
                        fields['pincode'] = pin_match.group(1)
                    elif re.match(r'\d{6}', value):
                        fields['pincode'] = value
                elif key in ['mobile', 'phone']:
                    fields['mobile'] = value
                elif key in ['email', 'e-mail']:
                    fields['email'] = value
        
        return fields
    
    @staticmethod
    def create_operation_folder(operation_id: str) -> Path:
        """Create folder structure for an operation."""
        base_dir = Path("operations") / operation_id
        base_dir.mkdir(parents=True, exist_ok=True)
        
        # Create subfolders
        (base_dir / "raw_docs").mkdir(exist_ok=True)
        (base_dir / "processed_docs").mkdir(exist_ok=True)
        (base_dir / "extracted_data").mkdir(exist_ok=True)
        
        return base_dir
    
    def save_results(
        self,
        result: Dict[str, Any],
        operation_dir: Path
    ) -> Dict[str, Path]:
        """Save processing results to operation folder."""
        import json
        
        saved_files = {}
        
        # Save extracted text
        if result["text"]:
            text_file = operation_dir / "extracted_data" / f"{result['filename']}_text.txt"
            text_file.write_text(result["text"], encoding='utf-8')
            saved_files["text_file"] = text_file
        
        # Save extracted fields as JSON
        if result["fields"]:
            json_file = operation_dir / "extracted_data" / f"{result['filename']}_fields.json"
            json_file.write_text(json.dumps(result["fields"], indent=2), encoding='utf-8')
            saved_files["json_file"] = json_file
        
        # Save processing metadata
        metadata = {
            "operation_id": result["operation_id"],
            "filename": result["filename"],
            "processing_time": result["processing_time"],
            "method": result["method"],
            "success": result["success"],
            "confidence": result["confidence"],
            "needs_ocr": result["needs_ocr"],
            "error": result["error"],
            "field_count": len(result["fields"]),
            "debug": result.get("debug", {}),
        }
        
        meta_file = operation_dir / "extracted_data" / f"{result['filename']}_metadata.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        saved_files["metadata_file"] = meta_file
        
        return saved_files
    
    def merge_fields_from_multiple_docs(self, results: list) -> Dict[str, Any]:
        """Merge fields from multiple documents with Aadhaar priority."""
        merged = {
            "aadhaar": None,
            "name": None,
            "dob": None,
            "age": None,
            "gender": None,
            "father_name": None,
            "mother_name": None,
            "address": None,
            "pincode": None,
            "mobile": None,
            "email": None,
            "sources": {}  # Track which document provided each field
        }
        
        # Separate Aadhaar and non-Aadhaar results
        aadhaar_results = []
        other_results = []
        
        for result in results:
            if not result["success"]:
                continue
            
            # Check if this is an Aadhaar document by filename
            is_aadhaar = self._should_skip_qr("", "", result["filename"])
            if is_aadhaar:
                aadhaar_results.append(result)
            else:
                other_results.append(result)
        
        # Process Aadhaar documents first (highest priority)
        for result in aadhaar_results:
            for field, value in result["fields"].items():
                if value and not merged.get(field):
                    merged[field] = value
                    merged["sources"][field] = result["filename"]
        
        # Process non-Aadhaar documents (lower priority)
        for result in other_results:
            for field, value in result["fields"].items():
                if value and not merged.get(field):
                    merged[field] = value
                    merged["sources"][field] = result["filename"]
        
        return merged
    
    def _save_json(self, data: Dict[str, Any], file_path: str) -> None:
        """Save data to JSON file."""
        import json
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    
    def merge_operation_results(self, operation_dir: str) -> Dict[str, Any]:
        """
        Merge all results from an operation directory.
        
        Args:
            operation_dir: Path to operation directory
            
        Returns:
            Dict with merged fields and metadata
        """
        operation_path = Path(operation_dir)
        results = []
        
        # Find all result files
        for result_file in operation_path.glob("extracted_data/*_fields.json"):
            try:
                import json
                with open(result_file, 'r') as f:
                    result_data = json.load(f)
                    results.append({
                        "filename": result_file.stem.replace("_fields", ""),
                        "success": True,
                        "fields": result_data,
                        "text": ""  # We don't need text for merging
                    })
            except Exception as e:
                print(f"Error reading {result_file}: {e}")
                continue
        
        # Merge all results
        merged = self.merge_fields_from_multiple_docs(results)
        
        # Add operation metadata
        merged.update({
            "operation_id": operation_path.name,
            "files_processed": len(results),
            "processing_time": 0.0,  # Could be calculated if needed
            "success_rate": len(results) / max(1, len(list(operation_path.glob("*.pdf"))))
        })
        
        return merged
    
    def _guess_doc_type(self, text: str) -> str:
        """Guess document type from text content."""
        text_lower = text.lower()
        
        if "aadhaar" in text_lower or re.search(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', text):
            return "aadhaar"
        elif "birth" in text_lower and "certificate" in text_lower:
            return "birth_certificate"
        elif "pan" in text_lower or re.search(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b', text):
            return "pan"
        else:
            return "unknown"
