"""
Smart PDF processor that tries text extraction first, then OCR.
"""

import re
import tempfile
from pathlib import Path
from typing import Any, Dict

try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

try:
    from pdf2image import convert_from_path
    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

from .document_processor import DocumentProcessor, OCRResult, DocumentClassification


class SmartPDFProcessor:
    """Process PDFs intelligently: text extraction first, OCR as fallback."""
    
    def __init__(self, ocr_processor: DocumentProcessor):
        self.ocr_processor = ocr_processor
    
    def process_pdf(self, pdf_path: Path, output_dir: Path, max_size_kb: int = 100) -> Dict[str, Any]:
        """
        Process PDF with smart approach.
        
        Returns:
            Dict with extracted data and metadata
        """
        result = {
            "source": "unknown",
            "text": "",
            "confidence": 0.0,
            "classification": None,
            "fields": {},
            "metadata": {}
        }
        
        # Try direct text extraction first
        if HAS_PYPDF2:
            try:
                text = self._extract_text_from_pdf(pdf_path)
                if text.strip():
                    result["source"] = "embedded_text"
                    result["text"] = text
                    result["confidence"] = 0.9  # High confidence for embedded text
                    
                    # Classify and extract fields
                    classification = self.ocr_processor.classify_document(text)
                    result["classification"] = classification
                    result["fields"] = classification.extracted_fields
                    
                    return result
            except Exception as e:
                print(f"Text extraction failed: {e}")
        
        # Fall back to OCR
        if HAS_PDF2IMAGE:
            try:
                result["source"] = "ocr"
                
                # Convert first page to image
                images = convert_from_path(str(pdf_path), dpi=150)
                if images:
                    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                        images[0].save(tmp.name, 'JPEG', quality=85)
                        tmp_path = Path(tmp.name)
                    
                    try:
                        # Process with OCR
                        output_path = output_dir / f"ocr_{pdf_path.stem}.jpg"
                        normalized_path, ocr_result = self.ocr_processor.process_image_file(
                            input_path=tmp_path,
                            output_path=output_path,
                            max_size_kb=max_size_kb
                        )
                        
                        result["text"] = ocr_result.text
                        result["confidence"] = ocr_result.confidence
                        
                        # Classify and extract fields
                        classification = self.ocr_processor.classify_document(ocr_result.text)
                        result["classification"] = classification
                        result["fields"] = classification.extracted_fields
                        
                    finally:
                        tmp_path.unlink(missing_ok=True)
                        
            except Exception as e:
                print(f"OCR processing failed: {e}")
        
        return result
    
    def _extract_text_from_pdf(self, pdf_path: Path) -> str:
        """Extract text from PDF using PyPDF2."""
        text = ""
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            for page_num in range(len(reader.pages)):
                page = reader.pages[page_num]
                page_text = page.extract_text()
                text += page_text + "\n"
        return text
    
    @staticmethod
    def extract_structured_fields(text: str) -> Dict[str, str]:
        """Extract structured fields from text using patterns."""
        fields = {}
        
        # Aadhaar pattern
        aadhaar_match = re.search(r'\b(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b', text)
        if aadhaar_match:
            fields["aadhaar"] = aadhaar_match.group(1).replace(" ", "").replace("-", "")
        
        # PAN pattern
        pan_match = re.search(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b', text)
        if pan_match:
            fields["pan"] = pan_match.group(0)
        
        # Date patterns
        dates = re.findall(r'\b\d{2}[-/]\d{2}[-/]\d{4}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b', text)
        if dates:
            fields["dates"] = dates[:3]  # First 3 dates
        
        # Registration numbers (common in certificates)
        reg_match = re.search(r'[Rr]egistration\s+[Nn]o\.?\s*[:\-]?\s*([A-Z0-9\-/]+)', text)
        if reg_match:
            fields["registration_no"] = reg_match.group(1)
        
        # Look for names (lines with mostly uppercase)
        lines = text.split('\n')
        for i, line in enumerate(lines[:10]):
            clean_line = line.strip()
            if clean_line and len(clean_line) > 3:
                # Check if mostly uppercase and contains letters
                words = clean_line.split()
                if words and sum(c.isalpha() for c in clean_line) > len(clean_line) * 0.7:
                    upper_ratio = sum(c.isupper() for c in clean_line if c.isalpha()) / sum(c.isalpha() for c in clean_line)
                    if upper_ratio > 0.8:
                        fields["possible_name"] = clean_line
                        break
        
        return fields
