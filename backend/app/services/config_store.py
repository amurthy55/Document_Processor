from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ServiceConfig:
    service_id: str
    data: dict


class ServiceConfigStore:
    def __init__(self, configs_root: Path) -> None:
        self._root = configs_root
        self._root.mkdir(parents=True, exist_ok=True)

    def list_services(self) -> list[str]:
        return sorted([p.stem for p in self._root.glob("*.json")])

    def load(self, service_id: str) -> ServiceConfig:
        path = self._root / f"{service_id}.json"
        if not path.exists():
            raise FileNotFoundError("service_config_not_found")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ServiceConfig(service_id=service_id, data=data)

    @staticmethod
    def _pattern_specificity(pattern: str, url: str) -> tuple[int, int]:
        """
        Score how specifically a pattern matches a URL.
        Higher is better.

        Returns:
            (specificity_score, pattern_length)
        """
        p = (pattern or "").strip()
        if not p:
            return (0, 0)

        u = urlparse(url)
        host = u.hostname or ""
        path = u.path or ""
        full = url

        # Literal URL (no regex metacharacters) is strongest, especially exact full URL.
        meta_chars = set(r".^$*+?{}[]\|()")
        has_regex_meta = any(ch in meta_chars for ch in p)
        if not has_regex_meta:
            if p == full:
                return (1000, len(p))
            if p == host + path:
                return (980, len(p))
            if p == host:
                return (950, len(p))
            if p in full:
                return (900, len(p))

        # Regex-based matches: prefer anchored and host/path-specific expressions.
        score = 100
        if p.startswith("^"):
            score += 120
        if p.endswith("$"):
            score += 80
        if host and host in p:
            score += 220
        if path and path != "/" and path in p:
            score += 260
        if "/" in p:
            score += 80
        if "https" in p or "http" in p:
            score += 40

        # Penalize very broad wildcard regexes.
        wildcard_penalty = p.count(".*") * 20 + p.count(".+") * 15
        score -= wildcard_penalty

        return (score, len(p))

    def match_by_url(self, url: str) -> ServiceConfig | None:
        # Rank matches by specificity first, then recency as tie-breaker.
        best_cfg = None
        best_rank = None  # (specificity, pattern_length, mtime)

        for path in self._root.glob("*.json"):
            service_id = path.stem
            cfg = self.load(service_id)
            patterns = cfg.data.get("url_patterns") or []
            mtime = path.stat().st_mtime

            for pat in patterns:
                if not pat or not pat.strip():
                    continue
                try:
                    if not re.search(pat, url):
                        continue
                except re.error:
                    continue

                specificity, plen = self._pattern_specificity(pat, url)
                rank = (specificity, plen, mtime)
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best_cfg = cfg

        return best_cfg
