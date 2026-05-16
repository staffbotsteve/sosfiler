"""Build targeted source-discovery queries for each jurisdiction batch."""

from __future__ import annotations

from .schemas import ResearchBatch


STATE_FILING_TERMS = [
    "LLC formation articles organization fee online filing",
    "corporation incorporation articles incorporation fee online filing",
    "nonprofit corporation articles incorporation fee",
    "foreign LLC registration application authority fee",
    "foreign corporation qualification application authority fee",
    "LLP LP professional entity formation forms fees",
    "annual report statement information franchise tax public information report",
    "renewal recurring compliance due date reminder filing fee",
    "beneficial ownership information report BOI filing update correction deadline FinCEN",
    "USPTO trademark filing maintenance renewal assignment fee process",
    "USPTO patent filing maintenance fee assignment process",
    "amendment registered agent change address change reinstatement dissolution fees",
    "certified copy certificate of status good standing fees",
]

LOCAL_FILING_TERMS = [
    "county fictitious business name DBA assumed name fee",
    "county business license general license business tax certificate fee",
    "city business license business tax certificate home occupation permit fee",
    "business license renewal amendment closure fee",
    "business license renewal reminder due date late fee",
]


def build_discovery_queries(batch: ResearchBatch) -> list[str]:
    domains = " OR ".join(f"site:{domain}" for domain in batch.official_domains)
    terms = LOCAL_FILING_TERMS if "local" in batch.scope else STATE_FILING_TERMS
    return [f"{domains} {batch.title} {term}".strip() for term in terms]


def source_requirements_prompt(batch: ResearchBatch) -> str:
    filings = ", ".join(batch.required_filings)
    return (
        "Use official government sources first. Return only source URLs that describe "
        f"{filings} for {batch.title}. Each source must be mapped to fees, process steps, "
        "expected turnaround, automation path, status checks, and produced documents."
    )
