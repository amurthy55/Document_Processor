"""
Fast PDF processor using PyMuPDF for text extraction.
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

from .document_processor import DocumentProcessor, OCRResult, DocumentClassification


class FastPDFProcessor:
    """Fast PDF processing using PyMuPDF for text extraction."""
    
    def __init__(self, ocr_processor: Optional[DocumentProcessor] = None):
        self.ocr_processor = ocr_processor
    
    def process_pdf(self, pdf_path: Path, max_size_kb: int = 100) -> Dict[str, Any]:
        """
        Process PDF quickly using PyMuPDF.
        
        Returns:
            Dict with extracted data
        """
        result = {
            "source": "pymupdf",
            "text": "",
            "confidence": 0.0,
            "classification": None,
            "fields": {},
            "metadata": {},
            "processing_time": 0.0,
            "needs_ocr": False
        }
        
        if not HAS_PYMUPDF:
            result["source"] = "error"
            result["error"] = "PyMuPDF not installed"
            return result
        
        try:
            import time
            start = time.time()
            
            # Extract text using PyMuPDF
            doc = fitz.open(pdf_path)
            text = ""
            
            for page_num in range(min(len(doc), 3)):  # First 3 pages max
                page = doc[page_num]
                # Get text with layout preservation
                page_text = page.get_text()
                text += page_text + "\n"
            
            doc.close()
            
            result["processing_time"] = time.time() - start
            result["text"] = text
            
            # Check if we got meaningful text
            if text.strip() and len(text.strip()) > 50:
                result["confidence"] = 0.95  # High confidence for extracted text
                
                # Classify document
                if self.ocr_processor:
                    classification = self.ocr_processor.classify_document(text)
                    result["classification"] = classification
                    result["fields"] = classification.extracted_fields
                
                # Additional structured extraction
                structured_fields = self._extract_structured_fields(text)
                result["fields"].update(structured_fields)
                
            else:
                result["needs_ocr"] = True
                result["confidence"] = 0.0
                
        except Exception as e:
            result["source"] = "error"
            result["error"] = str(e)
        
        return result
    
    def _extract_structured_fields(self, text: str) -> Dict[str, str]:
        """Extract structured fields from text using patterns."""
        fields = {}
        
        # Common patterns with better regex
        patterns = {
            "aadhaar": r'\b(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b',
            "pan": r'\b[A-Z]{5}[0-9]{4}[A-Z]\b',
            "passport": r'\b[A-Z][0-9]{7,8}\b',
            "voter_id": r'\b[A-Z]{3}[0-9]{7}\b',
            "driving_license": r'\b[A-Z]{2}[0-9]{11,13}\b',
            "dob": r'(?:DOB|Date of Birth|Born|Dated)\s*[:\-]?\s*(\d{2}[-/]\d{2}[-/]\d{4})',
            "registration": r'[Rr]egistration\s+[Nn]o\.?\s*[:\-]?\s*([A-Z0-9\-/]+)',
            "application": r'[Aa]pplication\s+[Nn]o\.?\s*[:\-]?\s*([A-Z0-9\-/]+)',
            "certificate": r'[Cc]ertificate\s+[Nn]o\.?\s*[:\-]?\s*([A-Z0-9\-/]+)',
        }
        
        for field, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                fields[field] = match.group(1) if match.groups() else match.group(0)
        
        # Extract all dates
        dates = re.findall(r'\b\d{2}[-/]\d{2}[-/]\d{4}\b', text)
        if dates:
            fields["dates"] = dates[:5]  # First 5 dates
        
        # Extract mobile numbers
        mobile = re.findall(r'\b[6-9]\d{9}\b', text)
        if mobile:
            fields["mobile"] = mobile[0]
        
        # Extract email addresses
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if emails:
            fields["email"] = emails[0]
        
        # Smart name extraction
        fields.update(self._extract_names(text))
        
        return fields
    
    def _extract_names(self, text: str) -> Dict[str, str]:
        """Extract possible names from text."""
        names = {}
        lines = text.split('\n')
        
        # Look for name patterns
        for i, line in enumerate(lines[:20]):
            clean = line.strip()
            
            # Skip common headers
            skip_words = [
                'certificate', 'government', 'department', 'office', 'form', 
                'application', 'issued', 'dated', 'page', 'ref', 'sl.no',
                'this is to certify', 'to whomsoever', 'india', 'tamil nadu'
            ]
            
            if (clean and 3 < len(clean) < 60 and 
                not any(word.lower() in clean.lower() for word in skip_words)):
                
                # Pattern 1: All caps line
                if clean.replace(' ', '').isalpha() and clean.isupper():
                    names["name_caps"] = clean
                    continue
                
                # Pattern 2: Title case line
                words = clean.split()
                if (words and len(words) >= 2 and len(words) <= 4 and
                    all(word[0].isupper() for word in words if word.isalpha())):
                    names["name_title"] = clean
                    continue
                
                # Pattern 3: Name: Label format
                name_match = re.match(r'^(?:Name|Student Name|Applicant)\s*[:\-]?\s*(.+)$', clean, re.IGNORECASE)
                if name_match:
                    names["name"] = name_match.group(1).strip()
        
        return names
    
    @staticmethod
    def create_json_output(text: str, fields: dict, doc_type: str = "unknown") -> dict:
        """Create structured JSON output from extracted data."""
        import json
        
        output = {
            "document_type": doc_type,
            "extracted_text": text[:1000] if text else "",  # First 1000 chars
            "structured_fields": fields,
            "extraction_method": "pymupdf",
            "confidence": 0.95 if text else 0.0
        }
        
        return output
