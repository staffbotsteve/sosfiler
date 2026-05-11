"""Provider abstraction for regulatory research discovery and extraction."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .firecrawl_client import FirecrawlClient, FirecrawlExtractResult


@dataclass
class ProviderStatus:
    name: str
    configured: bool
    capabilities: list[str]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "configured": self.configured,
            "capabilities": self.capabilities,
            "notes": self.notes,
        }


@dataclass
class LocalDiscoveryResponse:
    provider_name: str
    data: dict[str, Any] | None = None
    error: str = ""
    raw: dict[str, Any] | None = None
    source_urls: list[str] = field(default_factory=list)


class ResearchProvider(Protocol):
    name: str

    @property
    def configured(self) -> bool:
        ...

    def status(self) -> ProviderStatus:
        ...

    def extract(
        self,
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
        enable_web_search: bool = False,
        show_sources: bool = True,
    ) -> FirecrawlExtractResult:
        ...

    def discover_local_sources(
        self,
        batch: dict[str, Any],
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
    ) -> LocalDiscoveryResponse:
        ...


class FirecrawlResearchProvider:
    name = "firecrawl"

    def __init__(self, client: FirecrawlClient | None = None) -> None:
        self.client = client or FirecrawlClient()

    @property
    def configured(self) -> bool:
        return self.client.configured

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            configured=self.configured,
            capabilities=["source_discovery", "content_extraction", "pdf_extraction", "structured_json"],
            notes="Primary active provider for phase-one JSON-file research.",
        )

    def extract(
        self,
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
        enable_web_search: bool = False,
        show_sources: bool = True,
    ) -> FirecrawlExtractResult:
        return self.client.extract(
            urls=urls,
            prompt=prompt,
            schema=schema,
            enable_web_search=enable_web_search,
            show_sources=show_sources,
        )

    def discover_local_sources(
        self,
        batch: dict[str, Any],
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
    ) -> LocalDiscoveryResponse:
        result = self.extract(
            urls=urls,
            prompt=prompt,
            schema=schema,
            enable_web_search=True,
            show_sources=True,
        )
        return LocalDiscoveryResponse(
            provider_name=self.name,
            data=result.data if isinstance(result.data, dict) else None,
            raw=result.to_dict(),
            source_urls=urls,
        )


class ConfiguredStubProvider:
    """Named provider slot for a provider that is not implemented in this phase."""

    def __init__(
        self,
        name: str,
        env_key: str,
        capabilities: list[str],
        notes: str,
    ) -> None:
        self.name = name
        self.env_key = env_key
        self.capabilities = capabilities
        self.notes = notes

    @property
    def configured(self) -> bool:
        return bool(os.getenv(self.env_key, "").strip())

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            configured=self.configured,
            capabilities=self.capabilities,
            notes=self.notes,
        )

    def extract(
        self,
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
        enable_web_search: bool = False,
        show_sources: bool = True,
    ) -> FirecrawlExtractResult:
        return FirecrawlExtractResult(
            success=False,
            data={},
            status="unsupported",
            error=f"{self.name} extraction provider is not implemented yet.",
        )

    def discover_local_sources(
        self,
        batch: dict[str, Any],
        urls: list[str],
        prompt: str,
        schema: dict[str, Any],
    ) -> LocalDiscoveryResponse:
        return LocalDiscoveryResponse(
            provider_name=self.name,
            error=f"{self.name} discovery provider is not implemented yet.",
            source_urls=urls,
        )


def provider_registry() -> list[ResearchProvider]:
    return [
        FirecrawlResearchProvider(),
        ConfiguredStubProvider(
            "tavily",
            "TAVILY_API_KEY",
            ["source_discovery", "crawl", "search"],
            "Planned discovery provider for official county/city source finding.",
        ),
        ConfiguredStubProvider(
            "exa",
            "EXA_API_KEY",
            ["source_discovery", "content_retrieval"],
            "Planned content provider for clean page text and source expansion.",
        ),
        ConfiguredStubProvider(
            "apify",
            "APIFY_API_TOKEN",
            ["scheduled_crawl", "dataset_export"],
            "Planned crawler provider for long-running official-site crawls.",
        ),
        ConfiguredStubProvider(
            "accela",
            "ACCELA_CLIENT_ID",
            ["partner_api", "local_license_records", "permit_records"],
            "Planned direct integration for municipalities using Accela civic platforms.",
        ),
        ConfiguredStubProvider(
            "browserbase",
            "BROWSERBASE_API_KEY",
            ["portal_automation", "status_check", "document_download"],
            "Planned browser automation provider for government portals and document retrieval.",
        ),
    ]


def provider_statuses() -> list[dict[str, Any]]:
    return [provider.status().to_dict() for provider in provider_registry()]


def active_extraction_provider() -> ResearchProvider:
    return FirecrawlResearchProvider()


def active_discovery_providers() -> list[ResearchProvider]:
    providers = provider_registry()
    firecrawl = [provider for provider in providers if provider.name == "firecrawl"]
    configured_discovery = [
        provider for provider in providers
        if provider.name != "firecrawl" and provider.configured and "source_discovery" in provider.status().capabilities
    ]
    return firecrawl + configured_discovery
