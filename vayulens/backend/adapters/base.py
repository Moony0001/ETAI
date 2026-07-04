"""Adapter pattern base class.

Every data source implements `AbstractSourceAdapter` and returns a list of
typed pydantic records from `fetch()`. Adapters:
  * declare whether they are `available` (keys present / no auth needed),
  * NEVER crash the pipeline — on missing keys or network errors they log a
    warning and return an empty list,
  * cache raw responses under data/raw/<name>/ so reruns don't hammer the API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.config import HTTP_TIMEOUT_S, RAW_DIR

logger = logging.getLogger("vayulens.adapters")


class AbstractSourceAdapter(ABC):
    """Common interface + caching for all data-source adapters."""

    #: short machine name, also the cache subdirectory
    name: str = "base"
    #: human description for logs / UI
    description: str = ""

    def __init__(self, cache_ttl_s: float = 3600.0) -> None:
        self.cache_ttl_s = cache_ttl_s
        self._cache_dir = RAW_DIR / self.name
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # -- capability -------------------------------------------------------
    @property
    def available(self) -> bool:
        """Whether this adapter can actually fetch (keys present, etc.)."""
        return True

    def warn_unavailable(self) -> None:
        logger.warning(
            "[%s] adapter unavailable (missing key/config) — skipping gracefully.",
            self.name,
        )

    # -- the contract -----------------------------------------------------
    @abstractmethod
    def fetch(self, **kwargs: Any) -> list[Any]:
        """Return a list of normalised pydantic records. Never raises."""
        raise NotImplementedError

    # -- caching helpers --------------------------------------------------
    def _cache_path(self, key: str, ext: str = "json") -> Path:
        digest = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self._cache_dir / f"{digest}.{ext}"

    def _read_cache(self, key: str) -> Optional[Any]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        if self.cache_ttl_s >= 0 and (time.time() - path.stat().st_mtime) > self.cache_ttl_s:
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, key: str, payload: Any) -> None:
        try:
            self._cache_path(key).write_text(json.dumps(payload))
        except (TypeError, OSError) as exc:  # pragma: no cover - best effort
            logger.debug("[%s] cache write failed: %s", self.name, exc)

    def _read_text_cache(self, key: str) -> Optional[str]:
        path = self._cache_path(key, ext="txt")
        if not path.exists():
            return None
        if self.cache_ttl_s >= 0 and (time.time() - path.stat().st_mtime) > self.cache_ttl_s:
            return None
        try:
            return path.read_text()
        except OSError:
            return None

    def _write_text_cache(self, key: str, text: str) -> None:
        try:
            self._cache_path(key, ext="txt").write_text(text)
        except OSError as exc:  # pragma: no cover
            logger.debug("[%s] text cache write failed: %s", self.name, exc)

    # -- http helpers -----------------------------------------------------
    def get_json(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        cache: bool = True,
    ) -> Optional[Any]:
        """GET returning parsed JSON, cached. Returns None on any failure."""
        key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
        if cache:
            cached = self._read_cache(key)
            if cached is not None:
                logger.debug("[%s] cache hit %s", self.name, url)
                return cached
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("[%s] request failed (%s): %s", self.name, url, exc)
            return None
        if cache:
            self._write_cache(key, data)
        return data

    def get_text(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        cache: bool = True,
    ) -> Optional[str]:
        """GET returning raw text (e.g. CSV), cached. Returns None on failure."""
        key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
        if cache:
            cached = self._read_text_cache(key)
            if cached is not None:
                logger.debug("[%s] cache hit %s", self.name, url)
                return cached
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            text = resp.text
        except httpx.HTTPError as exc:
            logger.warning("[%s] request failed (%s): %s", self.name, url, exc)
            return None
        if cache:
            self._write_text_cache(key, text)
        return text
