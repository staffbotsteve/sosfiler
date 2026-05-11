"""Firecrawl API wrapper for regulatory research.

The wrapper is intentionally urllib-based to avoid adding a runtime dependency
for the first JSON-file-backed research phase.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.firecrawl.dev"


class FirecrawlError(RuntimeError):
    pass


@dataclass
class FirecrawlSource:
    url: str
    title: str = ""
    markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FirecrawlSource":
        metadata = payload.get("metadata") or {}
        url = (
            payload.get("url")
            or payload.get("sourceURL")
            or metadata.get("sourceURL")
            or metadata.get("url")
            or ""
        )
        title = payload.get("title") or metadata.get("title") or ""
        return cls(
            url=url,
            title=title,
            markdown=payload.get("markdown") or payload.get("content") or "",
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "markdown": self.markdown,
            "metadata": self.metadata,
        }


@dataclass
class FirecrawlExtractResult:
    success: bool
    data: Any = None
    sources: list[FirecrawlSource] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    job_id: str = ""
    error: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FirecrawlExtractResult":
        data = payload.get("data")
        raw_sources = payload.get("sources") or []
        if isinstance(data, dict) and not raw_sources:
            raw_sources = data.get("sources") or []
        return cls(
            success=bool(payload.get("success", False)),
            data=data,
            sources=[
                FirecrawlSource.from_payload(item)
                for item in raw_sources
                if isinstance(item, dict)
            ],
            raw=payload,
            status=payload.get("status") or "",
            job_id=payload.get("id") or payload.get("jobId") or "",
            error=payload.get("error") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "sources": [source.to_dict() for source in self.sources],
            "raw": self.raw,
            "status": self.status,
            "job_id": self.job_id,
            "error": self.error,
        }


class FirecrawlClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        retries: int = 2,
        poll_interval_seconds: float = 2.0,
        max_poll_seconds: int = 120,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("FIRECRAWL_API_KEY", "")
        self.base_url = (base_url or os.getenv("FIRECRAWL_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = int(os.getenv("FIRECRAWL_TIMEOUT_SECONDS", str(timeout_seconds)))
        self.retries = retries
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_seconds = int(os.getenv("FIRECRAWL_MAX_POLL_SECONDS", str(max_poll_seconds)))
        self.max_extract_urls = int(os.getenv("FIRECRAWL_EXTRACT_MAX_URLS", "10"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise FirecrawlError("FIRECRAWL_API_KEY is not configured.")
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        body = json.dumps(payload).encode() if payload is not None else None
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            req = Request(
                url,
                data=body,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "SOSFilerRegulatoryResearch/0.1",
                },
            )
            try:
                with urlopen(req, timeout=self.timeout_seconds) as response:
                    text = response.read().decode()
                    return json.loads(text) if text else {}
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                    error_body = exc.read().decode(errors="ignore") if exc.fp else ""
                    raise FirecrawlError(f"Firecrawl HTTP {exc.code}: {error_body}") from exc
            except (OSError, URLError, TimeoutError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(2**attempt)
        raise FirecrawlError(f"Firecrawl request failed: {last_error}") from last_error

    def map(self, url: str, search: str = "", limit: int = 50) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/map",
            {
                "url": url,
                "search": search,
                "ignoreSitemap": False,
                "sitemapOnly": False,
                "includeSubdomains": True,
                "limit": limit,
                "location": {"country": "US", "languages": ["en-US"]},
            },
        )

    def scrape(self, url: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/scrape",
            {
                "url": url,
                "formats": ["markdown", "links"],
                "onlyMainContent": True,
                "location": {"country": "US", "languages": ["en-US"]},
            },
        )

    def crawl(self, url: str, limit: int = 25, max_depth: int = 2) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/crawl",
            {
                "url": url,
                "limit": limit,
                "maxDepth": max_depth,
                "ignoreSitemap": False,
                "allowBackwardLinks": True,
                "allowExternalLinks": False,
                "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
            },
        )

    def extract(
        self,
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
        enable_web_search: bool = False,
        show_sources: bool = True,
    ) -> FirecrawlExtractResult:
        urls = [url for url in urls if url]
        if len(urls) > self.max_extract_urls:
            return self._extract_chunked(
                urls=urls,
                prompt=prompt,
                schema=schema,
                enable_web_search=enable_web_search,
                show_sources=show_sources,
            )
        return self._extract_once(
            urls=urls,
            prompt=prompt,
            schema=schema,
            enable_web_search=enable_web_search,
            show_sources=show_sources,
        )

    def _extract_once(
        self,
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
        enable_web_search: bool,
        show_sources: bool,
    ) -> FirecrawlExtractResult:
        payload = self._request(
            "POST",
            "/v2/extract",
            {
                "urls": urls,
                "prompt": prompt,
                "schema": schema,
                "enableWebSearch": enable_web_search,
                "showSources": show_sources,
                "includeSubdomains": True,
                "ignoreInvalidURLs": True,
                "scrapeOptions": {
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "parsers": ["pdf"],
                    "location": {"country": "US", "languages": ["en-US"]},
                    "removeBase64Images": True,
                    "blockAds": True,
                    "timeout": self.timeout_seconds * 1000,
                },
            },
        )
        result = FirecrawlExtractResult.from_payload(payload)
        if result.job_id and result.status in {"", "processing", "pending"} and result.data in (None, [], {}):
            return self.poll_extract(result.job_id)
        return result

    def _extract_chunked(
        self,
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
        enable_web_search: bool,
        show_sources: bool,
    ) -> FirecrawlExtractResult:
        chunks = [
            urls[index:index + self.max_extract_urls]
            for index in range(0, len(urls), self.max_extract_urls)
        ]
        results = [
            self._extract_once(
                urls=chunk,
                prompt=prompt,
                schema=schema,
                enable_web_search=enable_web_search,
                show_sources=show_sources,
            )
            for chunk in chunks
        ]
        merged_data: dict[str, Any] = {}
        merged_sources: list[FirecrawlSource] = []
        raw_chunks: list[dict[str, Any]] = []
        success = True
        errors: list[str] = []
        for result in results:
            success = success and result.success
            if result.error:
                errors.append(result.error)
            raw_chunks.append(result.raw)
            merged_sources.extend(result.sources)
            if isinstance(result.data, dict):
                for key, value in result.data.items():
                    if isinstance(value, list):
                        merged_data.setdefault(key, [])
                        merged_data[key].extend(value)
                    elif key not in merged_data:
                        merged_data[key] = value
            elif isinstance(result.data, list):
                merged_data.setdefault("records", [])
                merged_data["records"].extend(result.data)
        return FirecrawlExtractResult(
            success=success,
            data=merged_data,
            sources=merged_sources,
            raw={
                "success": success,
                "status": "completed",
                "chunked": True,
                "chunk_count": len(chunks),
                "chunks": raw_chunks,
            },
            status="completed",
            error="; ".join(errors),
        )

    def poll_extract(self, job_id: str) -> FirecrawlExtractResult:
        deadline = time.time() + self.max_poll_seconds
        last = FirecrawlExtractResult(success=False, job_id=job_id, status="processing")
        while time.time() < deadline:
            payload = self._request("GET", f"/v2/extract/{job_id}")
            last = FirecrawlExtractResult.from_payload(payload)
            if last.status in {"completed", "failed", "cancelled"}:
                return last
            if last.success and last.data not in (None, [], {}):
                return last
            time.sleep(self.poll_interval_seconds)
        raise FirecrawlError(f"Timed out waiting for Firecrawl extract job {job_id}")
