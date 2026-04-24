"""
Document processing, OCR, and classification module.

Handles:
- Image normalization and optimization
- OCR via pytesseract
- Document classification (Aadhaar, PAN, etc.)
- Structured field extraction
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import pytesseract
    from PIL import Image, ImageOps, ImageFilter, ImageEnhance
    import easyocr
    HAS_OCR = True
    EASYOCR_AVAILABLE = True
except ImportError:
    HAS_OCR = False
    EASYOCR_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """Result of OCR processing."""
    text: str
    confidence: float
    language: str = "eng+tam"  # English + Tamil


@dataclass
class DocumentClassification:
    """Classification result for a document."""
    doc_type: str  # "aadhaar", "pan", "passport", etc.
    confidence: float
    extracted_fields: dict[str, Any]


class DocumentProcessor:
    """Process documents: normalize, OCR, classify, extract fields."""

    def __init__(self, tesseract_path: str | None = None, use_easyocr: bool = True) -> None:
        """
        Initialize processor.

        Args:
            tesseract_path: Optional path to tesseract executable.
            use_easyocr: Use EasyOCR instead of Tesseract (faster, more accurate).
        """
        if tesseract_path:
            pytesseract.pytesseract.pytesseract_cmd = tesseract_path
        self._classifier = DocumentClassifier()
        self.use_easyocr = use_easyocr and EASYOCR_AVAILABLE
        if self.use_easyocr:
            self.reader = easyocr.Reader(['en'])  # English only
            print("Using EasyOCR for faster, more accurate OCR")
        else:
            print("Using Tesseract OCR")

    def process_image_file(
        self,
        input_path: Path,
        output_path: Path,
        max_size_kb: int = 500,
    ) -> tuple[Path, OCRResult]:
        """
        Normalize image and perform OCR.

        Args:
            input_path: Input image file
            output_path: Where to save normalized image
            max_size_kb: Max file size after compression

        Returns:
            (normalized_image_path, ocr_result)
        """
        if not HAS_OCR:
            raise RuntimeError("OCR dependencies not installed")

        # Normalize image
        normalized = self._normalize_image(input_path, max_size_kb)

        # Save normalized version
        normalized.save(str(output_path), quality=85, optimize=True)

        # Perform OCR
        ocr_result = self._run_ocr(normalized)

        return output_path, ocr_result

    def _normalize_image(
        self,
        input_path: Path,
        max_size_kb: int = 500,
    ) -> Image.Image:
        """
        Normalize image: convert to RGB/JPG, deskew, resize, compress.

        Args:
            input_path: Input image path
            max_size_kb: Target max file size

        Returns:
            Normalized PIL Image
        """
        img = Image.open(input_path)

        # Convert to RGB if necessary
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Basic deskewing by rotating based on contrast
        # (simplified; full deskew would use angle detection)
        img = self._deskew_simple(img)

        # Resize if too large (max dimension 2000px)
        max_dim = 2000
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        # Enhance contrast for OCR
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)

        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(1.2)

        return img

    def _deskew_simple(self, img: Image.Image) -> Image.Image:
        """
        Simple deskew: try small rotations and pick best contrast.

        Args:
            img: PIL Image

        Returns:
            Rotated image with best contrast
        """
        best_img = img
        best_variance = 0

        for angle in [-5, -2, -1, 0, 1, 2, 5]:
            if angle == 0:
                continue
            rotated = img.rotate(angle, expand=False, fillcolor="white")
            # Compute variance of brightness as proxy for "deskewedness"
            variance = self._image_variance(rotated)
            if variance > best_variance:
                best_variance = variance
                best_img = rotated

        return best_img

    @staticmethod
    def _image_variance(img: Image.Image) -> float:
        """Compute variance of grayscale image (proxy for skew quality)."""
        gray = img.convert("L")
        pixels = list(gray.getdata())
        if not pixels:
            return 0
        mean = sum(pixels) / len(pixels)
        variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
        return variance

    def _run_ocr(self, img: Image.Image) -> OCRResult:
        """
        Run OCR on image using EasyOCR or Tesseract.

        Args:
            img: PIL Image

        Returns:
            OCRResult with extracted text
        """
        if self.use_easyocr:
            # Use EasyOCR - faster and more accurate
            # Convert PIL to numpy array for EasyOCR
            import numpy as np
            img_array = np.array(img)
            results = self.reader.readtext(img_array)
            text = " ".join([result[1] for result in results])
            # Average confidence from EasyOCR
            confidence = sum(result[2] for result in results) / len(results) if results else 0.0
            language = "en"
        else:
            # Fallback to Tesseract
            config = "--oem 3 --psm 6 -l eng"  # English only for speed and accuracy
            text = pytesseract.image_to_string(img, config=config)
            # Rough confidence estimate for Tesseract
            confidence = min(len(text.split()) / 10, 1.0) if text.strip() else 0.0
            language = "eng"

        return OCRResult(text=text, confidence=confidence, language=language)

    def classify_document(self, ocr_text: str) -> DocumentClassification:
        """
        Classify document type based on OCR text.

        Args:
            ocr_text: OCR-extracted text

        Returns:
            DocumentClassification with doc_type and extracted fields
        """
        return self._classifier.classify(ocr_text)


class DocumentClassifier:
    """Classify documents and extract fields based on keywords."""

    # Keywords for each document type (English + Tamil transliteration)
    CLASSIFIERS = {
        "aadhaar": {
            "keywords": ["aadhaar", "आधार", "aadhar", "uid", "resident"],
            "field_extractors": ["aadhaar_number", "name", "dob", "gender"],
        },
        "pan": {
            "keywords": ["pan", "प्रमाण", "permanent account", "income tax"],
            "field_extractors": ["pan_number", "name", "father_name"],
        },
        "passport": {
            "keywords": ["passport", "पासपोर्ट", "ministry of external affairs"],
            "field_extractors": ["passport_number", "name", "dob", "issued_date"],
        },
        "voter_id": {
            "keywords": ["voter", "वोटर", "electoral", "epic"],
            "field_extractors": ["voter_id", "name"],
        },
        "driving_license": {
            "keywords": ["driving", "ड्राइविंग", "license", "मोटर वाहन"],
            "field_extractors": ["license_number", "name", "dob"],
        },
        "income_certificate": {
            "keywords": ["income", "आय", "certificate", "तहसीलदार"],
            "field_extractors": ["name", "father_name", "annual_income"],
        },
        "community_certificate": {
            "keywords": ["community", "समुदाय", "caste", "certificate"],
            "field_extractors": ["name", "community", "father_name"],
        },
    }

    def classify(self, text: str) -> DocumentClassification:
        """
        Classify document type.

        Args:
            text: OCR text

        Returns:
            DocumentClassification
        """
        text_lower = text.lower()

        best_type = "unknown"
        best_confidence = 0.0
        matched_keywords = []

        for doc_type, info in self.CLASSIFIERS.items():
            keywords = info["keywords"]
            matches = sum(1 for kw in keywords if kw.lower() in text_lower)
            if matches > 0:
                confidence = min(matches / len(keywords), 1.0)
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_type = doc_type
                    matched_keywords = [kw for kw in keywords if kw.lower() in text_lower]

        extracted = self._extract_fields(best_type, text)

        return DocumentClassification(
            doc_type=best_type,
            confidence=best_confidence,
            extracted_fields=extracted,
        )

    def _extract_fields(self, doc_type: str, text: str) -> dict[str, Any]:
        """
        Extract fields specific to document type.

        Args:
            doc_type: Document type
            text: OCR text

        Returns:
            Dictionary of extracted fields
        """
        extracted = {}

        if doc_type == "aadhaar":
            extracted = self._extract_aadhaar(text)
        elif doc_type == "pan":
            extracted = self._extract_pan(text)
        elif doc_type == "income_certificate":
            extracted = self._extract_income_cert(text)
        elif doc_type == "community_certificate":
            extracted = self._extract_community_cert(text)

        return extracted

    @staticmethod
    def _extract_aadhaar(text: str) -> dict[str, str]:
        """Extract Aadhaar fields."""
        import re

        extracted = {}
        # Aadhaar is 12 digits, often formatted as XXXX XXXX XXXX
        aadhaar_match = re.search(r"\b(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b", text)
        if aadhaar_match:
            extracted["aadhaar"] = aadhaar_match.group(1).replace(" ", "").replace("-", "")

        # Name typically appears as first line(s) with capital letters
        lines = text.split("\n")
        for line in lines[:5]:
            if line.strip() and any(c.isupper() for c in line):
                extracted["name"] = line.strip()
                break

        return extracted

    @staticmethod
    def _extract_pan(text: str) -> dict[str, str]:
        """Extract PAN fields."""
        import re

        extracted = {}
        # PAN is 10 characters: 5 letters, 4 digits, 1 letter
        pan_match = re.search(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", text)
        if pan_match:
            extracted["pan"] = pan_match.group(0)

        return extracted

    @staticmethod
    def _extract_income_cert(text: str) -> dict[str, str]:
        """Extract Income Certificate fields."""
        import re

        extracted = {}

        # Look for income amount (rupees)
        income_match = re.search(
            r"(?:annual\s+)?income[:\s]+(?:rs\.?|₹)?\s*([0-9,]+)",
            text,
            re.IGNORECASE,
        )
        if income_match:
            extracted["annual_income"] = income_match.group(1).replace(",", "")

        return extracted

    @staticmethod
    def _extract_community_cert(text: str) -> dict[str, str]:
        """Extract Community Certificate fields."""
        extracted = {}

        # Look for common community names (simplified)
        communities = ["OBC", "SC", "ST", "General", "Scheduled Caste", "Scheduled Tribe"]
        for comm in communities:
            if comm.lower() in text.lower():
                extracted["community"] = comm
                break

        return extracted
