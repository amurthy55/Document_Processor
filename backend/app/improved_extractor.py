"""
Improved document extractor that handles bilingual documents and extracts complete fields.
"""

import re
from typing import Dict, Optional, List
from pathlib import Path


class ImprovedExtractor:
    """Smart extractor that ignores Tamil and extracts complete document fields."""
    
    # Extended Tamil Unicode range (for filtering) - includes more blocks
    TAMIL_UNICODE = re.compile(r'[\u0B80-\u0BFF\u0BC6-\u0BCF\u0BD0\u0BD7\u1CD0\u1CD1\u1CD2\u200C\u200D\u20B9]')
    
    # Common Tamil words to filter out
    TAMIL_WORDS = {
        'ஆதார்', 'எண்', 'பெயர்', 'வயது', 'பாலினம்', 'முகவரி', 'சான்றிதழ்', 
        'தேதி', 'தகவல்', 'அரசு', 'இந்தியா', 'சென்னை', 'தமிழ்நாடு',
        'தந்தை', 'தாய்', 'பிறந்த', 'ஆண்', 'பெண்', 'சிறுமி', 'சிறுவன்',
        'பிறப்பு', 'மாதம்', 'திரு', 'திருமணம்', 'மணம்', 'வயது', 'பிறந்த', 'ஆலம்', 'திரு',
        'வயது', 'பிறந்த', 'ஆலம்', 'திரு', 'வயது', 'பிறந்த', 'ஆலம்', 'திரு'
    }
    
    @staticmethod
    def filter_tamil(text: str) -> str:
        """Remove Tamil characters and common Tamil words, with better bilingual handling."""
        # Process line by line for better bilingual handling
        lines = text.split('\n')
        filtered_lines = []
        
        for line in lines:
            # Check if line has significant Tamil content (>30% Tamil characters)
            tamil_chars = len(ImprovedExtractor.TAMIL_UNICODE.findall(line))
            total_chars = len(re.sub(r'\s', '', line))
            
            if total_chars > 0 and tamil_chars / total_chars > 0.3:
                # Line has too much Tamil, skip it entirely
                continue
            
            # Remove Tamil Unicode characters
            line = ImprovedExtractor.TAMIL_UNICODE.sub('', line)
            
            # Remove common Tamil words
            for word in ImprovedExtractor.TAMIL_WORDS:
                line = line.replace(word, '')
            
            # Clean up the line
            line = re.sub(r'\s+', ' ', line).strip()
            
            if line:  # Only keep non-empty lines
                filtered_lines.append(line)
        
        # Rejoin lines
        text = '\n'.join(filtered_lines)
        
        # Remove lines that are entirely whitespace
        text = re.sub(r'\n\s*\n', '\n', text)
        
        return text.strip()
    
    @staticmethod
    def _extract_labeled_value(clean_text: str, *labels: str) -> Optional[str]:
        """Extract a value from labelled key-value text."""
        for label in labels:
            pattern = rf'{re.escape(label)}\s*[:\-]?\s*(.+)'
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip(':').strip()
                value = re.sub(r'\s+', ' ', value)
                if value:
                    return value
        return None

    @staticmethod
    def extract_aadhaar_complete(text: str) -> Dict[str, str]:
        """Public entry point — filters Tamil then delegates."""
        return ImprovedExtractor._extract_aadhaar(ImprovedExtractor.filter_tamil(text))

    @staticmethod
    def _extract_aadhaar(clean_text: str) -> Dict[str, str]:
        """Extract all Aadhaar fields from pre-filtered text."""
        import re
        from datetime import datetime
        
        clean_text = clean_text  # already filtered
        
        extracted = {}
        
        # 1. Aadhaar number (12 digits)
        aadhaar_patterns = [
            r'\b(\d{4}\s\d{4}\s\d{4})\b',  # XXXX XXXX XXXX
            r'\b(\d{4}-\d{4}-\d{4})\b',    # XXXX-XXXX-XXXX
            r'\b(\d{12})\b'                # XXXXXXXXXXXX
        ]
        
        for pattern in aadhaar_patterns:
            match = re.search(pattern, clean_text)
            if match:
                extracted["aadhaar"] = match.group(1).replace(' ', '').replace('-', '')
                break
        
        # 2. Name (English only, after filtering Tamil)
        lines = clean_text.split('\n')

        def _is_clean_name(s: str) -> bool:
            """Return True if s looks like a person name (2-4 title-case words)."""
            s = s.strip()
            if not s or len(s) < 4 or len(s) > 60:
                return False
            bad = {
                'aadhaar', 'uid', 'unique', 'identification', 'authority',
                'government', 'india', 'date', 'birth', 'year', 'male',
                'female', 'address', 'pin', 'code', 'photo', 'candidate',
                'roll', 'seating', 'subject', 'exam', 'hall', 'ticket',
                'enrolment', 'certificate', 'republic', 'ministry', 'department',
                'velacherry', 'chennai', 'district', 'state', 'mobile', 'download',
            }
            if any(b in s.lower() for b in bad):
                return False
            words = s.split()
            if not (2 <= len(words) <= 4):
                return False
            if not all(re.fullmatch(r'[A-Za-z][A-Za-z]*\.?|[A-Z]\.', w) for w in words):
                return False
            if not any(len(w.rstrip('.')) > 1 for w in words):
                return False
            return True

        # Strategy 1: name appears on its own clean line immediately before a DOB line.
        # This is the most reliable anchor in Aadhaar cards (both digital and scanned).
        for i, line in enumerate(lines):
            if re.search(r'DOB[:/]|Date\s+of\s+Birth|wreit/DOB', line, re.IGNORECASE):
                # Walk up from DOB line to find the closest clean name line
                for j in range(i - 1, max(i - 5, -1), -1):
                    candidate = lines[j].strip()
                    if _is_clean_name(candidate):
                        extracted["name"] = candidate
                        break
                if "name" in extracted:
                    break

        # Strategy 2: labelled "Name:" anywhere in text
        if "name" not in extracted:
            m = re.search(r'(?:^|\n)\s*Name\s*[:\-]\s*([A-Z][A-Za-z .]+)', clean_text)
            if m and _is_clean_name(m.group(1)):
                extracted["name"] = m.group(1).strip()

        # Strategy 3: scan lines for a standalone name-like line
        if "name" not in extracted:
            for line in lines:
                if _is_clean_name(line.strip()):
                    extracted["name"] = line.strip()
                    break
        
        # 3. Date of Birth (multiple formats)
        dob_patterns = [
            r'DOB:\s*(\d{2}/\d{2}/\d{4})',
            r'Date of Birth:\s*(\d{2}/\d{2}/\d{4})',
            r'Born:\s*(\d{2}/\d{2}/\d{4})',
            r'Year of Birth:\s*(\d{4})',
            r'(\d{2}/\d{2}/\d{4})\s*(?:Age|YoB)'
        ]
        
        for pattern in dob_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                dob = match.group(1)
                extracted["dob"] = dob
                
                # Calculate age if possible
                try:
                    if '/' in dob:
                        dob_date = datetime.strptime(dob, "%d/%m/%Y")
                        age = 2025 - dob_date.year
                        extracted["age"] = str(age)
                except:
                    pass
                break
        
        # 4. Gender
        gender_patterns = [
            r'(Male|Female)',
            r'Sex:\s*(Male|Female)',
            r'Gender:\s*(Male|Female)'
        ]
        
        for pattern in gender_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                extracted["gender"] = match.group(1).lower()
                break
        
        # 5. Address — token-based extraction from the full text.
        # Aadhaar addresses always contain structured tokens (C/O, door no, locality,
        # VTC/PO/Sub District/District/State/PIN). We pull each known token individually
        # so OCR noise on the same line doesn't contaminate the result.
        addr_tokens = []

        co_m = re.search(r'C/O:\s*([A-Za-z][A-Za-z .]+?)(?=[,\n]|$)', clean_text)
        if co_m:
            addr_tokens.append(f"C/O: {co_m.group(1).strip()}")

        # Door/flat number: short pattern like "15/29 F2", anchored near C/O: context
        # to avoid matching enrolment numbers elsewhere in the document.
        door_m = re.search(
            r'C/O:.{0,80}?(\d{1,4}[/\\]\d{1,4}\s*(?:[A-Za-z]\d?)?)\s*,',
            clean_text, re.DOTALL
        )
        if door_m:
            addr_tokens.append(door_m.group(1).strip())

        # Named locality suffixes — pick the first match on a single line.
        # Require title-case words only (no ALL-CAPS OCR garbage tokens).
        for suffix in ['Enclave', 'Avenue', 'Road', 'Street', 'Nagar',
                       'Colony', 'Layout', 'Garden', 'Park', 'Lane', 'Salai']:
            m = re.search(
                rf'([A-Z][a-z]{{2,}}(?:\s+[A-Z][a-z]{{1,}})*\s+{suffix})',
                clean_text
            )
            if m:
                tok = m.group(1).strip()
                if tok not in ' '.join(addr_tokens):
                    addr_tokens.append(tok)
                break

        for label, display in [
            (r'VTC\s*:', 'VTC'), (r'PO\s*:', 'PO'),
            (r'Sub\s*District\s*:', 'Sub District'),
            (r'District\s*:', 'District'), (r'State\s*:', 'State'),
        ]:
            m = re.search(rf'{label}\s*([A-Za-z][A-Za-z .]+?)(?=[,\n]|$)', clean_text, re.IGNORECASE)
            if m:
                val = m.group(1).strip().strip(',')
                addr_tokens.append(f"{display}: {val}")

        if addr_tokens:
            addr = ', '.join(addr_tokens)
            addr = re.sub(r',\s*,', ',', addr).strip().strip(',')
            if len(addr) > 20:
                extracted["address"] = addr
        
        # 6. Pincode (6 digits)
        pincode_match = re.search(r'\b(\d{6})\b', clean_text)
        if pincode_match:
            extracted["pincode"] = pincode_match.group(1)

        # 7. Mobile number — explicit label takes priority, then bare 10-digit number
        mobile_match = re.search(r'Mobile[:\s]+([6-9]\d{9})', clean_text, re.IGNORECASE)
        if not mobile_match:
            mobile_match = re.search(r'\b([6-9]\d{9})\b', clean_text)
        if mobile_match:
            extracted["mobile"] = mobile_match.group(1)

        # 8. Father/Mother names
        for keyword in ['Father', 'Mother', 'Husband', 'S/O', 'D/O', 'W/O']:
            pattern = rf'{keyword}[:\s]*([A-Z][a-z\s]+)'
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                relation_key = keyword.lower().replace('/', '_of')
                extracted[relation_key] = match.group(1).strip()
        
        return extracted
    
    @staticmethod
    def extract_birth_certificate_complete(text: str) -> Dict[str, str]:
        """Public entry point — filters Tamil then delegates."""
        return ImprovedExtractor._extract_birth_certificate(ImprovedExtractor.filter_tamil(text))

    @staticmethod
    def _extract_birth_certificate(clean_text: str) -> Dict[str, str]:
        """Extract all Birth Certificate fields from pre-filtered text."""
        
        extracted = {}

        # Structured key-value tables from QR/verification pages are the cleanest
        # source, so prefer them before falling back to regex heuristics.
        labelled_values = {
            "district": ImprovedExtractor._extract_labeled_value(clean_text, "District"),
            "sub_district": ImprovedExtractor._extract_labeled_value(clean_text, "Sub-District", "Sub District"),
            "registration_unit": ImprovedExtractor._extract_labeled_value(clean_text, "Registration Unit"),
            "child_name": ImprovedExtractor._extract_labeled_value(clean_text, "Child Name", "Name of Child"),
            "gender": ImprovedExtractor._extract_labeled_value(clean_text, "Sex", "Gender"),
            "dob": ImprovedExtractor._extract_labeled_value(clean_text, "Date of Birth"),
            "place_of_birth": ImprovedExtractor._extract_labeled_value(clean_text, "Place of Birth"),
            "father_name": ImprovedExtractor._extract_labeled_value(clean_text, "Father Name", "Name of Father"),
            "mother_name": ImprovedExtractor._extract_labeled_value(clean_text, "Mother Name", "Name of Mother"),
            "permanent_address": ImprovedExtractor._extract_labeled_value(clean_text, "Permanent Address"),
            "address_at_birth": ImprovedExtractor._extract_labeled_value(
                clean_text,
                "Address of parents at the time of birth of the child",
                "Address of Parents at the time of birth of the child",
            ),
            "registration_no": ImprovedExtractor._extract_labeled_value(clean_text, "Registration No.", "Registration No", "Registration Number"),
            "registration_date": ImprovedExtractor._extract_labeled_value(clean_text, "Date of Regn.", "Date of Registration"),
        }

        for field, value in labelled_values.items():
            if not value:
                continue
            normalized = re.sub(r'\s+', ' ', value).strip()
            if field == "gender":
                lowered = normalized.lower()
                if lowered in {"male", "female"}:
                    extracted[field] = lowered
                else:
                    extracted[field] = normalized
            elif field == "dob" or field == "registration_date":
                extracted[field] = normalized.replace('-', '/')
            else:
                extracted[field] = normalized
        
        # Registration number (improved pattern)
        reg_patterns = [
            r'REGISTRATION NUMBER[^:]*:\s*(B-\d{4}:\d{2}-\d{4}-\d{6})',
            r'(B-\d{4}:\d{2}-\d{4}-\d{6})'
        ]
        
        for pattern in reg_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                reg_num = match.group(1)
                extracted.setdefault("registration_no", reg_num)
                break
        
        # Child name (improved patterns)
        child_name_patterns = [
            r'NAME[^:]*:\s*([A-Z\.]+[A-Z\s\.]+)',
            r'NAME\s*/\s*[^:]*:\s*([A-Z\.]+[A-Z\s\.]+)',
            r'([A-Z]+\.[A-Z]+\.[A-Z]+)\s*SEX',
            r'([A-Z]+\.[A-Z\s\.]+)\s*SEX'
        ]
        
        for pattern in child_name_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                child_name = match.group(1).strip().rstrip('/')
                # Validate it's a name (has letters, not just labels)
                if len(child_name) > 2 and not any(word in child_name.upper() for word in ['NAME', 'SEX', 'FEMALE', 'MALE']):
                    extracted.setdefault("child_name", child_name)
                    break
        
        # Father name (improved patterns)
        father_patterns = [
            r'NAME OF FATHER[^:]*:\s*([A-Z\.]+[A-Z\s\.\/]+)\s*ADDRESS',
            r'FATHER[^:]*:\s*([A-Z\.]+[A-Z\s\.\/]+)'
        ]
        
        for pattern in father_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                father_name = match.group(1).strip().rstrip('/')
                if len(father_name) > 2 and 'FATHER' not in father_name.upper() and 'ADDRESS' not in father_name.upper():
                    extracted.setdefault("father_name", father_name)
                    break
        
        # Mother name (improved patterns)
        mother_patterns = [
            r'NAME OF MOTHER[^:]*:\s*([A-Z\.]+[A-Z\s\.\/]+)\s*NAME OF FATHER',
            r'MOTHER[^:]*:\s*([A-Z\.]+[A-Z\s\.\/]+)'
        ]
        
        for pattern in mother_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                mother_name = match.group(1).strip().rstrip('/')
                if len(mother_name) > 2 and 'MOTHER' not in mother_name.upper() and 'FATHER' not in mother_name.upper():
                    extracted.setdefault("mother_name", mother_name)
                    break
        
        # Date of birth (specific patterns)
        dob_patterns = [
            r'DATE OF BIRTH[^:]*:\s*(\d{2}/\d{2}/\d{4})',
            r'DATE OF BIRTH[^:]*:\s*(\d{2}-\d{2}-\d{4})',
            r'(\d{2}/\d{2}/\d{4})\s*NINETEEN',
            r'BORN[^:]*:\s*(\d{2}/\d{2}/\d{4})'
        ]
        
        for pattern in dob_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                extracted.setdefault("dob", match.group(1).replace('-', '/'))
                break
        
        # Date of registration
        reg_date_patterns = [
            r'DATE OF REGISTRATION[^:]*:\s*(\d{2}/\d{2}/\d{4})',
            r'REGISTRATION[^:]*DATE[^:]*:\s*(\d{2}/\d{2}/\d{4})'
        ]
        
        for pattern in reg_date_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                extracted.setdefault("registration_date", match.group(1))
                break
        
        # Date of issue
        issue_date_patterns = [
            r'DATE OF ISSUE[^:]*:\s*(\d{2}/\d{2}/\d{4})',
            r'ISSUE[^:]*DATE[^:]*:\s*(\d{2}/\d{2}/\d{4})'
        ]
        
        for pattern in issue_date_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                extracted.setdefault("issue_date", match.group(1))
                break
        
        # Gender
        gender_patterns = [
            r'SEX[^:]*:\s*(MALE|FEMALE)',
            r'FEMALE|MALE'
        ]
        
        for pattern in gender_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                gender = match.group(1) if match.groups() else match.group(0)
                extracted.setdefault("gender", gender.lower())
                break
        
        # Address (improved)
        address_patterns = [
            r'ADDRESS OF PARENTS[^:]*:([A-Z0-9\s,/-]+?)\s*15/29',
            r'([A-Z0-9\s,/-]+TAMIL NADU\s*-\s*\d{6})',
            r'([A-Z\s,]+-\s*\d{6})'
        ]
        
        for pattern in address_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE | re.DOTALL)
            if match:
                addr = match.group(1).strip()
                addr = re.sub(r'\s+', ' ', addr)
                if len(addr) > 20 and len(addr) < 200:
                    extracted.setdefault("address", addr)
                    break

        if "address" not in extracted:
            if labelled_values.get("permanent_address"):
                extracted["address"] = labelled_values["permanent_address"]
            elif labelled_values.get("address_at_birth"):
                extracted["address"] = labelled_values["address_at_birth"]
        
        # Place of birth
        place_patterns = [
            r'PLACE OF BIRTH[^:]*:([A-Z\s,]+)',
            r'BORN[^:]*AT[^:]*:([A-Z\s,]+)',
            r'HOSPITAL[^:]*:([A-Z\s,]+)'
        ]
        
        for pattern in place_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                place = match.group(1).strip()
                if len(place) > 3 and len(place) < 100:
                    extracted.setdefault("place_of_birth", place)
                    break
        
        # Issuing authority
        authority_patterns = [
            r'ISSUING AUTHORITY[^:]*:([A-Z\s]+)',
            r'REGISTRAR[^:]*\([^)]+\)([A-Z\s]+)',
            r'([A-Z\s]+PANCHAYAT)'
        ]
        
        for pattern in authority_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                authority = match.group(1).strip()
                if len(authority) > 5:
                    extracted.setdefault("issuing_authority", authority)
                    break
        
        # Collect all dates if specific ones not found
        if not any(k in extracted for k in ['dob', 'registration_date', 'issue_date']):
            dates = re.findall(r'\b\d{2}[-/]\d{2}[-/]\d{4}\b', clean_text)
            if dates:
                extracted["dates"] = dates[:5]
        
        return extracted
    
    @staticmethod
    def extract_smart_combined(text: str, doc_type: str = "auto") -> Dict[str, str]:
        """Smart extraction that tries multiple strategies."""
        # Filter Tamil once here; individual extractors receive pre-filtered text
        # and must NOT call filter_tamil again to avoid double-processing.
        clean_text = ImprovedExtractor.filter_tamil(text)
        
        # Auto-detect document type if not specified
        if doc_type == "auto":
            if "aadhaar" in clean_text.lower() or re.search(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', clean_text):
                doc_type = "aadhaar"
            elif "birth" in clean_text.lower() and "certificate" in clean_text.lower():
                doc_type = "birth_certificate"
            elif "pan" in clean_text.lower() or re.search(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b', clean_text):
                doc_type = "pan"
            else:
                doc_type = "general"
        
        # Extract based on document type — pass already-clean text, skip re-filtering inside
        if doc_type == "aadhaar":
            return ImprovedExtractor._extract_aadhaar(clean_text)
        elif doc_type == "birth_certificate":
            return ImprovedExtractor._extract_birth_certificate(clean_text)
        else:
            return ImprovedExtractor._extract_general(clean_text)
    
    @staticmethod
    def _extract_general(text: str) -> Dict[str, str]:
        """General field extraction for any document type."""
        extracted = {}

        # Helper: a name-word is a capitalised word or a single letter initial
        _NW = r'[A-Za-z][A-Za-z]*\.?'
        _NAME = rf'{_NW}(?:\s+{_NW})+'

        # 1. Name — labelled patterns take priority, then proximity to dates/IDs
        name_patterns = [
            # Explicit labels
            rf'(?:Name|Full\s*Name|Applicant\s*Name|Candidate\s*Name|Student\s*Name)[:\s]+({_NAME})',
            # Title prefix
            rf'(?:Mr|Mrs|Ms|Dr|Shri|Smt)\.?\s+({_NAME})',
            # Name just before DOB / date
            rf'({_NAME})\s+(?:DOB|Date of Birth|Born|D\.O\.B)',
            # Name just before a date value
            rf'({_NAME})\s+\d{{2}}[-/]\d{{2}}[-/]\d{{4}}',
        ]

        def _valid_name(s: str) -> bool:
            words = s.split()
            if not (2 <= len(words) <= 4):
                return False
            if not all(re.fullmatch(r'[A-Za-z][A-Za-z]*\.?|[A-Z]\.', w) for w in words):
                return False
            # At least one word longer than 1 char
            if not any(len(w.rstrip('.')) > 1 for w in words):
                return False
            skip = {'male', 'female', 'india', 'birth', 'date', 'name', 'address', 'pin', 'code'}
            if any(w.lower() in skip for w in words):
                return False
            return True

        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                if _valid_name(candidate):
                    extracted["name"] = candidate
                    break
        
        # 2. DOB - Multiple formats
        dob_patterns = [
            r'(?:DOB|Date of Birth|Born|Birth Date)[:\s]*(\d{2}[-/]\d{2}[-/]\d{4})',
            r'(?:DOB|Date of Birth|Born|Birth Date)[:\s]*(\d{2}[-/]\d{2}[-/]\d{2})',
            r'(\d{2}[-/]\d{2}[-/]\d{4})\s*(?:Age|Years|Y)',
            r'(\d{2}[-/]\d{2}[-/]\d{4})\s*(?:AM|PM|No|Male|Female)',
            r'Age[:\s]*(\d{2})\s*years',
            r'Year of Birth[:\s]*(\d{4})'
        ]
        
        for pattern in dob_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dob = match.group(1)
                # Normalize format
                if len(dob) == 4:  # Just year
                    extracted["birth_year"] = dob
                else:
                    # Convert to DD/MM/YYYY format
                    dob = dob.replace('-', '/')
                    extracted["dob"] = dob
                break
        
        # 3. Gender
        gender_patterns = [
            r'(?:Sex|Gender)[:\s]*(Male|Female|M|F)',
            r'(Male|Female)\s*(?:Sex|Gender)',
            r'\b(Male|Female)\b'
        ]
        
        for pattern in gender_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                gender = match.group(1).lower()
                if gender in ['m', 'male']:
                    extracted["gender"] = "male"
                elif gender in ['f', 'female']:
                    extracted["gender"] = "female"
                break
        
        # 4. Common identifiers
        patterns = {
            "mobile": r'\b[6-9]\d{9}\b',
            "email": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            "aadhaar": r'\b(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b',
            "pan": r'\b[A-Z]{5}[0-9]{4}[A-Z]\b',
            "pincode": r'\b(\d{6})\b',
            "passport": r'\b[A-Z][0-9]{7}\b',
            "voter_id": r'\b[A-Z]{3}[0-9]{7}\b'
        }
        
        for field, pattern in patterns.items():
            if field not in extracted:
                match = re.search(pattern, text)
                if match:
                    value = match.group(1) if match.groups() else match.group(0)
                    extracted[field] = value.replace(' ', '').replace('-', '')
        
        # 5. Address - General patterns
        address_patterns = [
            r'Address[:\s]*([^\n]+?(?:\n[A-Z][a-z\s,]+){0,3}?\d{6})',
            r'([A-Z][a-z\s,]+,\s*[A-Z][a-z\s,]+,\s*[A-Z][a-z\s,]+,\s*\d{6})',
            r'([A-Z][a-z\s,]+,\s*[A-Z][a-z\s,]+,\s*\d{6})'
        ]
        
        for pattern in address_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                addr = match.group(1)
                addr = re.sub(r'\s+', ' ', addr)
                addr = addr.replace('\n', ', ')
                if len(addr) > 20 and len(addr) < 200:  # Reasonable address length
                    extracted["address"] = addr.strip()
                    break
        
        # 6. Father/Mother/Guardian names
        relation_patterns = {
            "father_name": [r'Father[:\s]*([A-Z][a-z\s]+)', r'Father\'s Name[:\s]*([A-Z][a-z\s]+)', r'S/O[:\s]*([A-Z][a-z\s]+)'],
            "mother_name": [r'Mother[:\s]*([A-Z][a-z\s]+)', r'Mother\'s Name[:\s]*([A-Z][a-z\s]+)', r'D/O[:\s]*([A-Z][a-z\s]+)'],
            "guardian_name": [r'Guardian[:\s]*([A-Z][a-z\s]+)', r'W/O[:\s]*([A-Z][a-z\s]+)']
        }
        
        for field, patterns in relation_patterns.items():
            if field not in extracted:
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        if len(name) > 3 and len(name) < 50:
                            extracted[field] = name
                            break
        
        return extracted
