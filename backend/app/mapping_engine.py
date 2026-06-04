"""
Mapping engine and field similarity matching.

Supports:
- Admin mapping mode field enumeration
- DOM selector capture
- Fuzzy matching of form fields to master schema
- Configuration generation and versioning
"""

from __future__ import annotations

import difflib
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .services.config_store import ServiceConfigStore

logger = logging.getLogger(__name__)


# Master data schema that all services map to
MASTER_SCHEMA_FIELDS = [
    "applicant_name",
    "father_name",
    "mother_name",
    "dob",
    "gender",
    "aadhaar",
    "mobile",
    "email",
    "address_line1",
    "address_line2",
    "district",
    "taluk",
    "pincode",
    "income",
    "community",
    "religion",
    "marital_status",
    "occupation",
]

# Keyword mappings for fuzzy matching (English + Tamil)
FIELD_KEYWORDS = {
    "applicant_name": ["name", "नाम", "பெயர்", "full name", "applicant"],
    "father_name": ["father", "पिता", "father's", "dad", "padre"],
    "mother_name": ["mother", "माता", "माँ", "mom", "madre"],
    "dob": ["dob", "birth", "जन्म", "date of birth", "জন্মতারিখ"],
    "gender": ["gender", "sex", "लिंग", "பாலினம்"],
    "aadhaar": ["aadhaar", "aadhar", "आधार", "uid"],
    "mobile": ["mobile", "phone", "फोन", "टेलीफोन", "contact"],
    "email": ["email", "e-mail", "mail"],
    "address_line1": ["address", "पता", "street", "line 1"],
    "address_line2": ["address line 2", "line 2", "apt", "apt."],
    "district": ["district", "जिला", "district", "district name"],
    "taluk": ["taluk", "तालुका", "tahsil", "block"],
    "pincode": ["pin", "pincode", "postal", "zip", "zip code"],
    "income": ["income", "आय", "annual", "earnings"],
    "community": ["community", "समुदाय", "caste", "category"],
    "religion": ["religion", "धर्म", "faith"],
    "marital_status": ["marital", "married", "single", "status"],
    "occupation": ["occupation", "व्यवसाय", "job", "profession"],
}


@dataclass
class FormField:
    """Represents a form field enumerated from DOM."""
    field_id: str  # HTML id
    field_name: str  # HTML name attribute
    field_type: str  # text, email, select, checkbox, etc.
    placeholder: str | None
    label_text: str | None
    dom_path: str | None  # XPath or CSS selector


@dataclass
class FieldMapping:
    """Mapping between form field and master schema field."""
    master_field: str
    form_field_id: str
    similarity_score: float
    matched_keywords: list[str]
    input_type: str
    label_hint: str | None


class FieldMatcher:
    """Match form fields to master schema using fuzzy matching."""

    def __init__(self) -> None:
        """Initialize matcher with keyword dictionary."""
        self._keywords = FIELD_KEYWORDS

    def suggest_mappings(
        self,
        form_fields: list[FormField],
        min_confidence: float = 0.3,
    ) -> list[FieldMapping]:
        """
        Suggest mappings for form fields.

        Args:
            form_fields: List of form fields from DOM
            min_confidence: Minimum similarity score to include

        Returns:
            List of suggested mappings
        """
        suggestions = []

        for form_field in form_fields:
            best_master = None
            best_score = 0.0
            best_keywords = []

            field_text = self._normalize_text(
                f"{form_field.label_text} {form_field.placeholder} {form_field.field_name} {form_field.field_id}"
            )

            for master_field in MASTER_SCHEMA_FIELDS:
                keywords = self._keywords.get(master_field, [])
                keyword_matches = [
                    kw for kw in keywords if kw.lower() in field_text.lower()
                ]

                if keyword_matches:
                    # Keyword match found
                    score = len(keyword_matches) / max(len(keywords), 1)
                else:
                    # Use fuzzy string matching as fallback
                    name_score = difflib.SequenceMatcher(
                        None,
                        master_field.lower(),
                        form_field.field_name.lower(),
                    ).ratio()
                    label_score = (
                        difflib.SequenceMatcher(
                            None,
                            master_field.lower(),
                            field_text.lower(),
                        ).ratio()
                        if field_text
                        else 0
                    )
                    score = max(name_score, label_score) * 0.7

                if score > best_score:
                    best_score = score
                    best_master = master_field
                    best_keywords = keyword_matches

            if best_score >= min_confidence:
                suggestions.append(
                    FieldMapping(
                        master_field=best_master,
                        form_field_id=form_field.field_id or form_field.field_name,
                        similarity_score=best_score,
                        matched_keywords=best_keywords,
                        input_type=form_field.field_type,
                        label_hint=form_field.label_text or form_field.placeholder,
                    )
                )

        return suggestions

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text for matching."""
        return text.strip().lower() if text else ""


class ServiceConfigBuilder:
    """Build and save service configurations."""

    def __init__(self, configs_root: Path) -> None:
        """
        Initialize builder.

        Args:
            configs_root: Root directory for service configs
        """
        self._root = configs_root
        self._root.mkdir(parents=True, exist_ok=True)

    def build_config(
        self,
        service_name: str,
        url_patterns: list[str],
        field_mappings: list[FieldMapping] | dict[str, Any],
        upload_fields: list[str] | None = None,
        conditional_logic: dict | None = None,
        version: str = "1.0.0",
    ) -> dict[str, Any]:
        """
        Build service configuration dictionary.

        Args:
            service_name: Name of service
            url_patterns: URL regex patterns for service detection
            field_mappings: Mapped form fields as FieldMapping list or
                {master_field: form_field_id | mapping_object} dict
            upload_fields: Fields that accept file uploads
            conditional_logic: Conditional field visibility rules
            version: Config version

        Returns:
            Configuration dictionary
        """
        config = {
            "service_name": service_name,
            "version": version,
            "created_at": datetime.utcnow().isoformat(),
            "url_patterns": url_patterns,
            "field_mappings": {},
            "upload_fields": upload_fields or [],
            "conditional_logic": conditional_logic or {},
        }

        if isinstance(field_mappings, dict):
            for master_field, mapping in field_mappings.items():
                if isinstance(mapping, list):
                    form_field_ids = [str(v) for v in mapping if v]
                    if not form_field_ids:
                        continue
                    config["field_mappings"][master_field] = {
                        "form_field_ids": form_field_ids,
                        "input_type": "text",
                        "label_hint": None,
                        "similarity_score": 1.0,
                    }
                    if len(form_field_ids) == 1:
                        config["field_mappings"][master_field]["form_field_id"] = form_field_ids[0]
                elif isinstance(mapping, dict):
                    form_field_ids = (
                        mapping.get("form_field_ids")
                        or mapping.get("field_ids")
                        or mapping.get("targets")
                    )
                    form_field_id = mapping.get("form_field_id") or mapping.get("field_id")
                    if form_field_ids is None and form_field_id:
                        form_field_ids = [form_field_id]
                    if not form_field_ids:
                        continue
                    if not isinstance(form_field_ids, list):
                        form_field_ids = [form_field_ids]
                    form_field_ids = [str(v) for v in form_field_ids if v]
                    if not form_field_ids:
                        continue
                    config["field_mappings"][master_field] = {
                        "form_field_ids": form_field_ids,
                        "input_type": mapping.get("input_type", "text"),
                        "label_hint": mapping.get("label_hint"),
                        "similarity_score": mapping.get("similarity_score", 1.0),
                    }
                    if len(form_field_ids) == 1:
                        config["field_mappings"][master_field]["form_field_id"] = form_field_ids[0]
                else:
                    # Legacy/simple mapping style: {master_field: "form_field_id"}
                    config["field_mappings"][master_field] = {
                        "form_field_id": str(mapping),
                        "form_field_ids": [str(mapping)],
                        "input_type": "text",
                        "label_hint": None,
                        "similarity_score": 1.0,
                    }
        else:
            for mapping in field_mappings:
                config["field_mappings"][mapping.master_field] = {
                    "form_field_id": mapping.form_field_id,
                    "input_type": mapping.input_type,
                    "label_hint": mapping.label_hint,
                    "similarity_score": mapping.similarity_score,
                }

        return config

    def save_config(
        self,
        config: dict[str, Any],
        service_name: str,
        version_str: str | None = None,
    ) -> Path:
        """
        Save configuration to JSON file with versioning.

        Args:
            config: Configuration dictionary
            service_name: Service identifier
            version_str: Optional version suffix (e.g., "v2")

        Returns:
            Path to saved config file
        """
        suffix = f"_{version_str}" if version_str else ""
        filename = f"{service_name}{suffix}.json"
        filepath = self._root / filename

        # Backup existing if present
        if filepath.exists():
            backup = filepath.with_stem(filepath.stem + "_backup")
            if not backup.exists():
                backup.write_text(filepath.read_text(), encoding="utf-8")

        filepath.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Saved service config: {filepath}")
        return filepath

    def list_versions(self, service_name: str) -> list[str]:
        """
        List all versions of a service config.

        Args:
            service_name: Service name

        Returns:
            List of version identifiers
        """
        versions = []
        pattern = f"{service_name}*.json"
        for path in self._root.glob(pattern):
            if path.name.endswith("_backup.json"):
                continue
            stem = path.stem
            if "_v" in stem:
                version = stem.split("_v")[1]
                versions.append(version)
            elif stem == service_name:
                versions.append("latest")

        return sorted(versions)


class AdminMappingProcessor:
    """Process admin mapping mode: enumerate fields, suggest mappings, generate config."""

    def __init__(self, configs_root: Path) -> None:
        """
        Initialize processor.

        Args:
            configs_root: Root directory for service configs
        """
        self._matcher = FieldMatcher()
        self._config_builder = ServiceConfigBuilder(configs_root)
        self._config_store = ServiceConfigStore(configs_root)

    def process_field_inventory(
        self,
        service_name: str,
        url_patterns: list[str],
        fields: list[dict[str, Any]],
        min_confidence: float = 0.3,
    ) -> dict[str, Any]:
        """
        Process field inventory from admin mode and generate suggested config.

        Args:
            service_name: Service name
            url_patterns: URL patterns for service
            fields: Raw field inventory from extension
            min_confidence: Minimum confidence for suggestions

        Returns:
            Response with suggestions and draft config
        """
        print(f"DEBUG: process_field_inventory called with service_name={service_name}")
        logger.info(f"process_field_inventory called with service_name={service_name}")
        logger.info(f"fields received: {fields}")
        logger.info(f"fields type: {type(fields)}")
        if fields:
            logger.info(f"first field: {fields[0]}")
            logger.info(f"first field type: {type(fields[0])}")
        # Convert raw fields to FormField objects
        form_fields = [
            FormField(
                field_id=f.get("id", ""),
                field_name=f.get("name", ""),
                field_type=f.get("type", "text"),
                placeholder=f.get("placeholder"),
                label_text=f.get("label"),
                dom_path=f.get("dom_path"),
            )
            for f in fields
        ]

        # Check for existing config to preserve user mappings
        existing_mappings = {}
        try:
            existing_config = self._config_store.load(service_name)
            existing_mappings = existing_config.data.get("field_mappings", {})
        except FileNotFoundError:
            # No existing config, proceed with suggestions
            pass

        # Get suggestions but filter out fields already mapped in existing config
        all_suggestions = self._matcher.suggest_mappings(form_fields, min_confidence)
        logger.info(f"Raw suggestions count: {len(all_suggestions)}")
        logger.info(f"Existing mappings: {list(existing_mappings.keys())}")
        
        suggestions = []
        for s in all_suggestions:
            logger.info(f"Processing suggestion: {s}, type: {type(s)}")
            if hasattr(s, 'master_field') and s.master_field not in existing_mappings:
                suggestions.append(s)
        
        logger.info(f"Filtered suggestions count: {len(suggestions)}")
        
        # Debug: Check the type of first suggestion
        if suggestions:
            logger.info(f"First suggestion after filtering: {suggestions[0]}, type: {type(suggestions[0])}")
            if hasattr(suggestions[0], '__dict__'):
                logger.info(f"First suggestion attributes: {suggestions[0].__dict__}")

        # Build draft config with existing mappings + new suggestions
        combined_mappings = existing_mappings.copy()
        try:
            combined_mappings.update({s.master_field: s.form_field_id for s in suggestions})
        except AttributeError as e:
            logger.error(f"Error building combined_mappings: {e}")
            logger.error(f"suggestions content: {suggestions}")
            raise
        
        draft_config = self._config_builder.build_config(
            service_name=service_name,
            url_patterns=url_patterns,
            field_mappings=combined_mappings,
            version="1.0.0",
        )

        # Identify unmapped fields (include existing mappings)
        all_mapped_master_fields = set(combined_mappings.keys())
        unmapped_master = [
            f for f in MASTER_SCHEMA_FIELDS if f not in all_mapped_master_fields
        ]

        return {
            "service_name": service_name,
            "total_fields": len(form_fields),
            "suggestions": [
                {
                    "master_field": s.master_field,
                    "form_field": s.form_field_id,
                    "confidence": s.similarity_score,
                    "keywords_matched": s.matched_keywords,
                    "input_type": s.input_type,
                    "label": s.label_hint,
                }
                for s in suggestions
            ],
            "unmapped_schema_fields": unmapped_master,
            "draft_config": draft_config,
        }

    def finalize_config(
        self,
        service_name: str,
        config: dict[str, Any],
        approved_mappings: dict[str, str | list[str]] | None = None,
    ) -> Path:
        """
        Finalize and save configuration after admin review.

        Args:
            service_name: Service name
            config: Draft config
            approved_mappings: Admin-approved field mappings

        Returns:
            Path to saved config
        """
        if approved_mappings:
            # Normalize approved mappings to the canonical object form.
            config["field_mappings"] = {}
            for master_field, form_field_ids in approved_mappings.items():
                if not isinstance(form_field_ids, list):
                    form_field_ids = [form_field_ids]
                normalized_ids = [str(v) for v in form_field_ids if v]
                if not normalized_ids:
                    continue
                config["field_mappings"][master_field] = {
                    "form_field_ids": normalized_ids,
                    "input_type": "text",
                    "label_hint": None,
                    "similarity_score": 1.0,
                }
                if len(normalized_ids) == 1:
                    config["field_mappings"][master_field]["form_field_id"] = normalized_ids[0]

        return self._config_builder.save_config(config, service_name)
