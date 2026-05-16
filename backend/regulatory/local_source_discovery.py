"""Local county/city source discovery for regulatory research batches."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from .paths import regulatory_data_dir
from .record_store import write_json
from .research_providers import ResearchProvider, active_discovery_providers
from .schemas import utc_now


LOCAL_CANDIDATE_PATH = regulatory_data_dir() / "local_source_candidates.json"

HIGH_SIGNAL_TERMS = {
    "assumed",
    "dba",
    "fictitious",
    "business-name",
    "business_name",
    "businesslicense",
    "business-license",
    "business_license",
    "business-tax",
    "business_tax",
    "license",
    "licenses",
    "permit",
    "permits",
    "fee",
    "fees",
    "application",
    "applications",
    "registration",
    "renewal",
    "home-occupation",
    "home_occupation",
}

LOW_SIGNAL_TERMS = {
    "appraisal",
    "property-tax",
    "property_tax",
    "election",
    "elections",
    "voter",
    "jury",
    "parks",
    "news",
    "calendar",
    "employment",
    "careers",
}

LOCAL_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "jurisdiction_name": {"type": "string"},
                    "jurisdiction_level": {"type": "string"},
                    "state": {"type": "string"},
                    "authority": {"type": "string"},
                    "official_url": {"type": "string"},
                    "domain": {"type": "string"},
                    "filing_topics": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["jurisdiction_name", "jurisdiction_level", "official_url", "filing_topics"],
            },
        }
    },
    "required": ["source_candidates"],
}


@dataclass
class LocalSourceCandidate:
    candidate_id: str
    batch_id: str
    jurisdiction_name: str
    jurisdiction_level: str
    state: str
    authority: str
    official_url: str
    domain: str
    filing_topics: list[str]
    source_kind: str
    confidence: str = "seeded"
    notes: str = ""
    discovered_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["discovered_at"] = self.discovered_at or utc_now()
        return payload


LOCAL_SOURCE_SEEDS: dict[str, list[dict[str, Any]]] = {
    "TX": [
        {"name": "Harris County", "level": "county", "authority": "County Clerk", "urls": ["https://www.cclerk.hctx.net/", "https://www.harriscountytx.gov/"], "topics": ["assumed_name", "dba"]},
        {"name": "Dallas County", "level": "county", "authority": "County Clerk", "urls": ["https://www.dallascounty.org/government/county-clerk/", "https://www.dallascounty.org/government/county-clerk/recording/assumed-names/assumed-names-procedures.php"], "topics": ["assumed_name", "dba"]},
        {"name": "Tarrant County", "level": "county", "authority": "County Clerk", "urls": ["https://www.tarrantcountytx.gov/en/county-clerk.html", "https://www.tarrantcountytx.gov/en/county-clerk/vital-records/assumed-names.html"], "topics": ["assumed_name", "dba"]},
        {"name": "Bexar County", "level": "county", "authority": "County Clerk", "urls": ["https://www.bexar.org/2955/Assumed-Business-NamesDBAs"], "topics": ["assumed_name", "dba"]},
        {"name": "Travis County", "level": "county", "authority": "County Clerk", "urls": ["https://countyclerk.traviscountytx.gov/"], "topics": ["assumed_name", "dba"]},
        {"name": "Collin County", "level": "county", "authority": "County Clerk", "urls": ["https://www.collincountytx.gov/County-Clerk/Pages/Assumed-Names.aspx"], "topics": ["assumed_name", "dba"]},
        {"name": "Denton County", "level": "county", "authority": "County Clerk", "urls": ["https://www.dentoncounty.gov/277/Assumed-Names-DBAs"], "topics": ["assumed_name", "dba"]},
        {"name": "Fort Bend County", "level": "county", "authority": "County Clerk", "urls": ["https://www.fortbendcountytx.gov/government/departments/county-clerk/dba-assumed-name"], "topics": ["assumed_name", "dba"]},
        {"name": "Williamson County", "level": "county", "authority": "County Clerk", "urls": ["https://www.wilcotx.gov/countyclerk"], "topics": ["assumed_name", "dba"]},
        {"name": "Ector County", "level": "county", "authority": "County Clerk", "urls": ["https://newtools.cira.state.tx.us/page/ector.AssumedName"], "topics": ["assumed_name", "dba"]},
        {"name": "Rockwall County", "level": "county", "authority": "County Clerk", "urls": ["https://www.rockwallcountytexas.com/658/Assumed-Name-DBA"], "topics": ["assumed_name", "dba"]},
        {"name": "Ellis County", "level": "county", "authority": "County Clerk", "urls": ["https://elliscountytx.gov/819/Assumed-Names"], "topics": ["assumed_name", "dba"]},
        {"name": "City of Houston", "level": "city", "authority": "Permitting Center", "urls": ["https://www.houstonpermittingcenter.org/"], "topics": ["business_license", "permits"]},
        {"name": "City of San Antonio", "level": "city", "authority": "Development Services", "urls": ["https://www.sa.gov/Directory/Departments/DSD"], "topics": ["business_license", "permits"]},
        {"name": "City of Dallas", "level": "city", "authority": "Special Collections", "urls": ["https://dallascityhall.com/departments/special-collections/"], "topics": ["business_license", "business_tax"]},
        {"name": "City of Austin", "level": "city", "authority": "Development Services", "urls": ["https://www.austintexas.gov/department/development-services"], "topics": ["business_license", "permits"]},
        {"name": "City of Fort Worth", "level": "city", "authority": "Development Services", "urls": ["https://www.fortworthtexas.gov/departments/development-services"], "topics": ["business_license", "permits"]},
    ],
    "CA": [
        {"name": "Los Angeles County", "level": "county", "authority": "Registrar-Recorder/County Clerk", "urls": ["https://www.lavote.gov/home/county-clerk/fictitious-business-names"], "topics": ["fictitious_business_name", "dba"]},
        {"name": "San Diego County", "level": "county", "authority": "Assessor/Recorder/County Clerk", "urls": ["https://arcc.sandiegocounty.gov/"], "topics": ["fictitious_business_name", "dba"]},
        {"name": "Orange County", "level": "county", "authority": "Clerk-Recorder", "urls": ["https://ocrecorder.com/"], "topics": ["fictitious_business_name", "dba"]},
        {"name": "Riverside County", "level": "county", "authority": "Assessor-County Clerk-Recorder", "urls": ["https://rivcoacr.org/"], "topics": ["fictitious_business_name", "dba"]},
        {"name": "Santa Clara County", "level": "county", "authority": "Clerk-Recorder", "urls": ["https://clerkrecorder.sccgov.org/"], "topics": ["fictitious_business_name", "dba"]},
        {"name": "City of Los Angeles", "level": "city", "authority": "Office of Finance", "urls": ["https://finance.lacity.gov/"], "topics": ["business_tax", "business_license"]},
        {"name": "City of San Diego", "level": "city", "authority": "City Treasurer", "urls": ["https://www.sandiego.gov/treasurer/taxesfees/btax"], "topics": ["business_tax", "business_license"]},
        {"name": "City and County of San Francisco", "level": "city", "authority": "Treasurer and Tax Collector", "urls": ["https://sftreasurer.org/business"], "topics": ["business_registration", "business_tax"]},
        {"name": "City of San Jose", "level": "city", "authority": "Finance Department", "urls": ["https://www.sanjoseca.gov/your-government/departments-offices/finance/business-tax-registration"], "topics": ["business_tax", "business_license"]},
        {"name": "City of Sacramento", "level": "city", "authority": "Finance Department", "urls": ["https://www.cityofsacramento.gov/finance"], "topics": ["business_operations_tax", "business_license"]},
    ],
    "FL": [
        {"name": "Miami-Dade County", "level": "county", "authority": "Tax Collector", "urls": ["https://www.miamidade.gov/global/service.page?Mduid_service=ser1499797465167132"], "topics": ["local_business_tax", "business_tax_receipt"]},
        {"name": "Broward County", "level": "county", "authority": "Records, Taxes and Treasury", "urls": ["https://www.broward.org/RecordsTaxesTreasury/TaxesFees/Pages/Default.aspx"], "topics": ["local_business_tax", "business_tax_receipt"]},
        {"name": "Palm Beach County", "level": "county", "authority": "Constitutional Tax Collector", "urls": ["https://www.pbctax.com/local-business-tax"], "topics": ["local_business_tax", "business_tax_receipt"]},
        {"name": "Hillsborough County", "level": "county", "authority": "Tax Collector", "urls": ["https://www.hillstax.org/services/business-tax/"], "topics": ["local_business_tax", "business_tax_receipt"]},
        {"name": "Orange County", "level": "county", "authority": "Tax Collector", "urls": ["https://www.octaxcol.com/local-business-tax/"], "topics": ["local_business_tax", "business_tax_receipt"]},
        {"name": "City of Miami", "level": "city", "authority": "Finance Department", "urls": ["https://www.miami.gov/Business-Licenses"], "topics": ["business_tax_receipt", "business_license"]},
        {"name": "City of Tampa", "level": "city", "authority": "Business Tax Division", "urls": ["https://www.tampa.gov/business-tax"], "topics": ["business_tax_receipt", "business_license"]},
        {"name": "City of Orlando", "level": "city", "authority": "Permitting Services", "urls": ["https://www.orlando.gov/Building-Development/Permits-Inspections/Business-Tax-Receipts"], "topics": ["business_tax_receipt", "business_license"]},
    ],
}


def _candidate_id(batch_id: str, url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"{batch_id}_{digest}"


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _candidate_text(candidate: LocalSourceCandidate) -> str:
    parsed = urlparse(candidate.official_url)
    return " ".join(
        [
            candidate.official_url,
            parsed.path,
            candidate.jurisdiction_name,
            candidate.jurisdiction_level,
            candidate.authority,
            " ".join(candidate.filing_topics),
            candidate.notes,
        ]
    ).lower()


def _has_high_signal(candidate: LocalSourceCandidate) -> bool:
    text = _candidate_text(candidate)
    return any(term in text for term in HIGH_SIGNAL_TERMS)


def _has_low_signal(candidate: LocalSourceCandidate) -> bool:
    text = _candidate_text(candidate)
    return any(term in text for term in LOW_SIGNAL_TERMS)


def _is_broad_landing_page(candidate: LocalSourceCandidate) -> bool:
    parsed = urlparse(candidate.official_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        return True
    generic_parts = {"government", "departments", "department", "directory", "services", "default.aspx"}
    return len(path_parts) <= 2 and not _has_high_signal(candidate) and any(part.lower() in generic_parts for part in path_parts)


def candidate_score(candidate: LocalSourceCandidate) -> int:
    score = 0
    text = _candidate_text(candidate)
    path = urlparse(candidate.official_url).path.lower()
    if _has_high_signal(candidate):
        score += 100
    if any(term in text for term in {"dba", "assumed", "fictitious", "business-name", "business_name"}):
        score += 60
    if any(term in path for term in {"dba", "assumed", "fictitious", "business-name", "business_name"}):
        score += 90
    if any(term in text for term in {"business-license", "business_license", "licenses", "special_collections"}):
        score += 45
    if any(term in path for term in {"business-license", "business_license", "licenses", "special_collections"}):
        score += 55
    if any(term in text for term in {"permit", "permits", "home-occupation", "home_occupation"}):
        score += 30
    if any(term in path for term in {"permit", "permits", "home-occupation", "home_occupation"}):
        score += 35
    if candidate.source_kind != "curated_seed":
        score += 20
    if candidate.jurisdiction_level.lower() == "county":
        score += 30
    elif candidate.jurisdiction_level.lower() == "city":
        score += 25
    elif candidate.jurisdiction_level.lower() == "state":
        score -= 35
    if "application" in text:
        score += 12
    if "fee" in text:
        score += 4
    if path.rstrip("/").endswith(("/recording", "/forms", "/fee-information", "/search-copies-of-records")):
        score -= 35
    if _is_broad_landing_page(candidate):
        score -= 40
    if _has_low_signal(candidate):
        score -= 80
    return score


def state_code_from_batch(batch: dict[str, Any]) -> str:
    return str(batch.get("jurisdiction_id", "")).split("_", 1)[0].upper()


def seeded_local_candidates(batch: dict[str, Any]) -> list[LocalSourceCandidate]:
    state = state_code_from_batch(batch)
    candidates: list[LocalSourceCandidate] = []
    for item in LOCAL_SOURCE_SEEDS.get(state, []):
        for url in item.get("urls", []):
            candidates.append(
                LocalSourceCandidate(
                    candidate_id=_candidate_id(batch["batch_id"], url),
                    batch_id=batch["batch_id"],
                    jurisdiction_name=item["name"],
                    jurisdiction_level=item["level"],
                    state=state,
                    authority=item.get("authority", ""),
                    official_url=url,
                    domain=_domain(url),
                    filing_topics=item.get("topics", []),
                    source_kind="curated_seed",
                    confidence="seeded",
                )
            )
    return candidates


def local_source_discovery_prompt(batch: dict[str, Any]) -> str:
    state = state_code_from_batch(batch)
    filings = ", ".join(batch.get("required_filings", []))
    return (
        f"Find official county and city government source pages in {state} for {filings}. "
        "Return only official government pages, not blogs, law firms, registered-agent companies, "
        "or directory mirrors. Prioritize county clerk/recorder pages for DBA, FBN, and assumed-name "
        "filings; city finance, treasurer, licensing, tax, permitting, and zoning pages for general "
        "business licenses, business tax certificates/receipts, home occupation permits, renewals, "
        "amendments, and closures. Include fee pages and application portals whenever available."
    )


def discovery_urls(batch: dict[str, Any], max_urls: int | None = None) -> list[str]:
    roots = [f"https://{domain.strip().rstrip('/')}/*" for domain in batch.get("official_domains", [])]
    roots.extend(f"{candidate.official_url.rstrip('/')}/*" for candidate in seeded_local_candidates(batch))
    urls = sorted(set(roots))
    if max_urls is not None and max_urls > 0:
        return urls[:max_urls]
    return urls


def local_extraction_urls(batch: dict[str, Any], candidates: list[LocalSourceCandidate]) -> list[str]:
    urls = [candidate.official_url.rstrip("/") for candidate in candidates]
    return sorted(set(urls))


def candidates_from_provider_data(batch: dict[str, Any], provider_name: str, data: dict[str, Any] | None) -> list[LocalSourceCandidate]:
    data = data or {}
    items = data.get("source_candidates", []) if isinstance(data, dict) else []
    candidates: list[LocalSourceCandidate] = []
    state = state_code_from_batch(batch)
    for item in items:
        if not isinstance(item, dict) or not item.get("official_url"):
            continue
        url = item["official_url"]
        candidates.append(
            LocalSourceCandidate(
                candidate_id=_candidate_id(batch["batch_id"], url),
                batch_id=batch["batch_id"],
                jurisdiction_name=item.get("jurisdiction_name", ""),
                jurisdiction_level=item.get("jurisdiction_level", ""),
                state=item.get("state") or state,
                authority=item.get("authority", ""),
                official_url=url,
                domain=item.get("domain") or _domain(url),
                filing_topics=item.get("filing_topics") or [],
                source_kind=f"{provider_name}_web_discovery",
                confidence=item.get("confidence") or "discovered",
                notes=item.get("notes") or "",
            )
        )
    return candidates


def merge_candidates(candidates: list[LocalSourceCandidate]) -> list[LocalSourceCandidate]:
    by_url: dict[str, LocalSourceCandidate] = {}
    for candidate in candidates:
        key = candidate.official_url.rstrip("/").lower()
        existing = by_url.get(key)
        if not existing:
            by_url[key] = candidate
            continue
        existing.filing_topics = sorted(set(existing.filing_topics + candidate.filing_topics))
        if candidate.source_kind == "curated_seed":
            existing.source_kind = candidate.source_kind
        if not existing.authority:
            existing.authority = candidate.authority
    level_rank = {"county": 0, "County": 0, "city": 1, "City": 1, "state": 2, "State": 2}
    return sorted(
        by_url.values(),
        key=lambda item: (
            -candidate_score(item),
            0 if item.source_kind != "curated_seed" else 1,
            level_rank.get(item.jurisdiction_level, 9),
            item.state,
            item.jurisdiction_name,
            item.official_url,
        ),
    )


def select_extraction_candidates(candidates: list[LocalSourceCandidate]) -> list[LocalSourceCandidate]:
    """Keep pages likely to contain actionable filing data, not generic homepages."""
    if not candidates:
        return []
    high_signal = [candidate for candidate in candidates if candidate_score(candidate) >= 70]
    high_signal_non_broad = [candidate for candidate in high_signal if not _is_broad_landing_page(candidate)]
    local_high_signal = [
        candidate for candidate in high_signal_non_broad
        if candidate.jurisdiction_level.lower() in {"county", "city"}
    ]
    if local_high_signal:
        return local_high_signal
    if high_signal_non_broad:
        return high_signal_non_broad
    if high_signal:
        return high_signal
    non_low_signal = [candidate for candidate in candidates if not _has_low_signal(candidate)]
    if non_low_signal:
        return non_low_signal
    return candidates


def discover_local_sources(
    batch: dict[str, Any],
    providers: list[ResearchProvider] | None = None,
    enable_web_discovery: bool = True,
    max_candidates: int | None = None,
    candidate_offset: int = 0,
) -> tuple[list[LocalSourceCandidate], dict[str, Any]]:
    seeded = seeded_local_candidates(batch)
    discovered: list[LocalSourceCandidate] = []
    provider_results: list[dict[str, Any]] = []
    errors: list[str] = []
    providers = providers or active_discovery_providers()
    if enable_web_discovery:
        for provider in providers:
            if not provider.configured:
                provider_results.append({
                    "provider": provider.name,
                    "configured": False,
                    "candidate_count": 0,
                    "error": "Provider is not configured.",
                })
                continue
            response = provider.discover_local_sources(
                batch=batch,
                urls=discovery_urls(batch, max_urls=max_candidates),
                prompt=local_source_discovery_prompt(batch),
                schema=LOCAL_SOURCE_SCHEMA,
            )
            provider_candidates = candidates_from_provider_data(batch, response.provider_name, response.data)
            discovered.extend(provider_candidates)
            if response.error:
                errors.append(f"{response.provider_name}: {response.error}")
            provider_results.append({
                "provider": response.provider_name,
                "configured": True,
                "candidate_count": len(provider_candidates),
                "error": response.error,
                "source_urls": response.source_urls,
                "raw": response.raw,
            })
    candidates = select_extraction_candidates(merge_candidates(seeded + discovered))
    total_candidate_count = len(candidates)
    if candidate_offset > 0:
        candidates = candidates[candidate_offset:]
    if max_candidates is not None and max_candidates > 0:
        candidates = candidates[:max_candidates]
    artifact = {
        "batch_id": batch["batch_id"],
        "captured_at": utc_now(),
        "enable_web_discovery": enable_web_discovery,
        "seeded_count": len(seeded),
        "discovered_count": len(discovered),
        "candidate_count": len(candidates),
        "total_candidate_count": total_candidate_count,
        "max_candidates": max_candidates,
        "candidate_offset": candidate_offset,
        "error": "; ".join(errors),
        "providers": provider_results,
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
    return candidates, artifact


def write_local_source_candidates(batch_id: str, artifact: dict[str, Any]) -> None:
    existing: dict[str, Any] = {"version": 1, "batches": {}}
    if LOCAL_CANDIDATE_PATH.exists():
        import json

        existing = json.loads(LOCAL_CANDIDATE_PATH.read_text())
    existing.setdefault("version", 1)
    existing.setdefault("batches", {})
    existing["batches"][batch_id] = artifact
    existing["updated_at"] = utc_now()
    write_json(LOCAL_CANDIDATE_PATH, existing)
