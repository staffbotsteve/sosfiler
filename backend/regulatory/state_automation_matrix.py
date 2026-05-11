"""Build and optionally browser-certify state automation profiles.

The matrix answers a production question that filing research alone cannot:
for each state, can SOSFiler submit, pay, poll status, and retrieve documents
without inventing a bespoke process at order time?
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .jurisdiction_registry import load_registry
from .paths import regulatory_data_dir
from .schemas import FilingCategory, VerificationStatus, utc_now


DATA_DIR = regulatory_data_dir()
FILINGS_PATH = DATA_DIR / "filings.json"
MATRIX_PATH = DATA_DIR / "state_automation_matrix.json"
CERT_SCREENSHOT_DIR = DATA_DIR / "automation_certification_screenshots"

PORTAL_HINTS: dict[str, list[str]] = {
    "TX": ["https://direct.sos.state.tx.us/acct/acct-login.asp"],
    "CA": ["https://bizfileonline.sos.ca.gov/"],
    "FL": ["https://dos.fl.gov/sunbiz/start-business/efile/"],
    "DE": ["https://corp.delaware.gov/document-upload-service-information/"],
    "NV": [
        "https://www.nvsilverflume.gov/checklist",
        "https://www.nvsilverflume.gov/login",
        "https://www.nvsilverflume.gov/home",
        "https://nv.gov/business",
    ],
    "AZ": ["https://ecorp.azcc.gov/"],
    "NY": ["https://businessexpress.ny.gov/"],
    "WY": ["https://wyobiz.wyo.gov/Business/FilingSearch.aspx"],
    "GA": ["https://ecorp.sos.ga.gov/"],
    "IL": ["https://apps.ilsos.gov/corporatellc/"],
    "NC": [
        "https://www.sosnc.gov/home/Online_Services",
        "https://www.sosnc.gov/online_filing/filing/creation",
    ],
    "OK": [
        "https://oklahoma.gov/business/launch/register-your-business.html",
        "https://www.sos.ok.gov/corp/filing.aspx",
    ],
    "UT": [
        "https://commerce.utah.gov/corporations/",
        "https://businessregistration.utah.gov/",
    ],
    "AK": [
        "https://www.commerce.alaska.gov/web/cbpl/Home",
        "https://www.commerce.alaska.gov/web/cbpl/Corporations",
    ],
    "IA": [
        "https://filings.sos.iowa.gov",
        "https://filings.sos.iowa.gov/Account/Login",
        "https://help.sos.iowa.gov/about-fast-track-filing",
    ],
    "ID": [
        "https://sos.idaho.gov/business-services/",
        "https://sos.idaho.gov/sosbiz-help/",
        "https://sos.idaho.gov/business-forms/",
    ],
    "KY": [
        "https://web.sos.ky.gov/fasttrack/default.aspx",
        "https://www.sos.ky.gov/bus/business-filings/OnlineServices/pages/default.aspx",
    ],
    "MN": [
        "https://sos.mn.gov/business",
        "https://sos.minnesota.gov/business-liens/start-a-business/how-to-register-your-business/",
        "https://www.sos.minnesota.gov/business-liens/business-forms-fees/business-filing-and-certification-fee-schedule/",
    ],
    "OH": [
        "https://www.ohiobusinesscentral.gov",
        "https://www.ohiosos.gov/businesses/filing-forms--fee-schedule/",
    ],
    "OR": [
        "https://sos.oregon.gov/business",
        "https://sos.oregon.gov/business/Pages/register.aspx",
        "https://secure.sos.state.or.us/cbrmanager/index.action#stay",
    ],
}

KNOWN_HUMAN_CHALLENGES: dict[str, str] = {
    "NC": "cloudflare_on_online_filing_creation",
}

KNOWN_CHALLENGE_URLS: dict[str, list[str]] = {
    "NC": ["https://www.sosnc.gov/online_filing/filing/creation"],
}

KNOWN_PROVEN_STATES: dict[str, dict[str, str]] = {
    "TX": {
        "account_required": "yes",
        "master_account_possible": "yes",
        "human_challenge_status": "not_observed_in_sosdirect_session",
        "payment_method": "credit_card_or_prefunded_sosdirect_account",
        "status_check_method": "sosdirect_briefcase_email_acknowledgment_entity_search",
        "document_retrieval_method": "sosdirect_email_zip_link_or_briefcase",
        "automation_lane": "playwright_persistent_browser",
        "production_readiness": "production_ready_operator_supervised",
        "blocker_summary": "Real order flow has been proven; keep payment and portal challenge fallback operator-supervised.",
        "next_action": "Move Texas status listener and document retrieval into scheduled production worker.",
    }
}


@dataclass
class StateAutomationProfile:
    state_code: str
    jurisdiction_id: str
    state_name: str
    portal_urls: list[str] = field(default_factory=list)
    account_required: str = "unknown"
    master_account_possible: str = "unknown"
    human_challenge_status: str = "unknown"
    online_llc: bool = False
    online_corporation: bool = False
    online_nonprofit: bool = False
    online_foreign_qualification: bool = False
    online_amendments: bool = False
    online_annual_report: bool = False
    payment_method: str = "unknown"
    status_check_method: str = "unknown"
    document_retrieval_method: str = "unknown"
    automation_lane: str = "needs_certification"
    production_readiness: str = "not_ready"
    blocker_summary: str = ""
    next_action: str = "Run Playwright certification against the state portal and classify submission/status/document retrieval."
    evidence_urls: list[str] = field(default_factory=list)
    last_certified_at: str = ""
    record_counts: dict[str, int] = field(default_factory=dict)
    verification_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_records() -> list[dict[str, Any]]:
    if not FILINGS_PATH.exists():
        return []
    return json.loads(FILINGS_PATH.read_text()).get("records", [])


def _state_from_record(record: dict[str, Any]) -> str:
    return str(record.get("jurisdiction_id", "")).split("_")[0].upper()


def _clean_urls(urls: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not url or not str(url).startswith(("http://", "https://")):
            continue
        parsed = urlparse(str(url))
        if not parsed.netloc:
            continue
        value = str(url).rstrip("/")
        if value not in seen:
            cleaned.append(value)
            seen.add(value)
    return cleaned[:12]


def _portal_hints_for_state(state: str) -> list[str]:
    return [*PORTAL_HINTS.get(state, []), *KNOWN_CHALLENGE_URLS.get(state, [])]


def _has_entity(records: list[dict[str, Any]], state: str, *needles: str) -> bool:
    for record in records:
        if _state_from_record(record) != state:
            continue
        haystack = " ".join([
            str(record.get("filing_name", "")),
            str(record.get("form_name", "")),
            " ".join(str(item) for item in record.get("entity_types", []) or []),
        ]).lower()
        if all(needle.lower() in haystack for needle in needles):
            return True
    return False


def _has_category(records: list[dict[str, Any]], state: str, category: str) -> bool:
    return any(_state_from_record(record) == state and record.get("filing_category") == category for record in records)


def build_matrix() -> dict[str, Any]:
    registry = load_registry()
    records = _load_records()
    records_by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
    urls_by_state: dict[str, list[str]] = defaultdict(list)
    for record in records:
        state = _state_from_record(record)
        if not state:
            continue
        records_by_state[state].append(record)
        if record.get("portal_url"):
            urls_by_state[state].append(str(record["portal_url"]))

    profiles: list[StateAutomationProfile] = []
    for jurisdiction in sorted(registry, key=lambda item: item["state"]):
        state = jurisdiction["state"]
        state_records = records_by_state.get(state, [])
        category_counts = Counter(str(record.get("filing_category", "")) for record in state_records)
        verification_counts = Counter(str(record.get("verification_status", "")) for record in state_records)
        portal_urls = _clean_urls([*_portal_hints_for_state(state), *urls_by_state.get(state, [])])
        profile = StateAutomationProfile(
            state_code=state,
            jurisdiction_id=jurisdiction["jurisdiction_id"],
            state_name=jurisdiction["name"],
            portal_urls=portal_urls,
            evidence_urls=portal_urls[:5],
            record_counts=dict(category_counts),
            verification_counts=dict(verification_counts),
            online_llc=_has_entity(records, state, "llc") or _has_entity(records, state, "limited liability"),
            online_corporation=_has_entity(records, state, "corporation"),
            online_nonprofit=_has_entity(records, state, "nonprofit") or _has_entity(records, state, "non-profit"),
            online_foreign_qualification=_has_category(records, state, FilingCategory.FOREIGN_AUTHORITY.value),
            online_amendments=(
                _has_category(records, state, FilingCategory.AMENDMENT.value)
                or _has_category(records, state, FilingCategory.REGISTERED_AGENT_CHANGE.value)
                or _has_category(records, state, FilingCategory.ADDRESS_CHANGE.value)
            ),
            online_annual_report=(
                _has_category(records, state, FilingCategory.ANNUAL_OBLIGATION.value)
                or _has_category(records, state, FilingCategory.RENEWAL.value)
            ),
        )
        if state_records and portal_urls:
            profile.automation_lane = "playwright_needs_certification"
            profile.production_readiness = "research_ready_certification_needed"
            profile.next_action = "Run Playwright portal certification to confirm login, challenge, payment, status, and document retrieval."
        if not state_records:
            profile.blocker_summary = "No state filing records are currently extracted."
            profile.next_action = "Repair official source extraction before portal automation certification."
        if state in KNOWN_HUMAN_CHALLENGES:
            profile.human_challenge_status = KNOWN_HUMAN_CHALLENGES[state]
            profile.automation_lane = "playwright_persistent_browser_with_operator_challenge_fallback"
            profile.production_readiness = "operator_assisted_required"
            profile.blocker_summary = "Online filing endpoint showed a Cloudflare challenge during Playwright certification."
            profile.next_action = "Use persistent Chrome profile on trusted operator machine; resume automation after human verification."
        if state in KNOWN_PROVEN_STATES:
            for key, value in KNOWN_PROVEN_STATES[state].items():
                setattr(profile, key, value)
            profile.last_certified_at = utc_now()
        profiles.append(profile)

    summary = {
        "total_states": len(profiles),
        "production_ready_operator_supervised": sum(
            1 for p in profiles if p.production_readiness == "production_ready_operator_supervised"
        ),
        "operator_assisted_required": sum(1 for p in profiles if p.production_readiness == "operator_assisted_required"),
        "research_ready_certification_needed": sum(
            1 for p in profiles if p.production_readiness == "research_ready_certification_needed"
        ),
        "not_ready": sum(1 for p in profiles if p.production_readiness == "not_ready"),
    }
    return {
        "version": 1,
        "generated_at": utc_now(),
        "purpose": "National state automation certification matrix for submission, status polling, payment, and document retrieval readiness.",
        "summary": summary,
        "profiles": [profile.to_dict() for profile in profiles],
    }


def write_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    payload = build_matrix()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def print_summary(payload: dict[str, Any]) -> None:
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    for profile in payload["profiles"]:
        print(
            f"{profile['state_code']:>2} "
            f"{profile['production_readiness']:<36} "
            f"{profile['automation_lane']:<52} "
            f"records={sum(profile['record_counts'].values())}"
        )


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text()) if MATRIX_PATH.exists() else write_matrix()


def refresh_portal_hints() -> dict[str, Any]:
    payload = _load_matrix()
    for profile in payload["profiles"]:
        state = profile["state_code"]
        existing = list(profile.get("portal_urls", []))
        profile["portal_urls"] = _clean_urls([*_portal_hints_for_state(state), *existing])
        profile["evidence_urls"] = profile["portal_urls"][:5]
    payload["generated_at"] = utc_now()
    MATRIX_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _detect_browser_signals(title: str, body: str, final_url: str, status: int | None) -> dict[str, Any]:
    haystack = " ".join([title, body, final_url]).lower()
    return {
        "http_status": status,
        "final_url": final_url,
        "title": title,
        "cloudflare": any(term in haystack for term in ["just a moment", "cloudflare", "security verification"]),
        "captcha_or_challenge": (
            status in {401, 403, 429}
            or any(
                term in haystack
                for term in [
                    "captcha",
                    "verify you are not a bot",
                    "security verification",
                    "access denied",
                    "service unavailable",
                    "website maintenance",
                    "botmanager",
                    "radware",
                ]
            )
        ),
        "login_required": any(term in haystack for term in ["sign in", "log in", "login", "user id", "password"]),
        "filing_entry_visible": any(term in haystack for term in [
            "start a business",
            "register new business",
            "register your new business",
            "silverflume",
            "business portal",
            "business creation",
            "create",
            "file online",
            "formation",
            "articles",
        ]),
        "payment_language_visible": any(term in haystack for term in ["payment", "credit card", "convenience fee", "service fee"]),
        "status_language_visible": any(term in haystack for term in ["status", "order", "submissions", "briefcase", "my filings"]),
        "document_language_visible": any(term in haystack for term in ["download", "document", "certificate", "receipt", "copy"]),
        "body_preview": re.sub(r"\s+", " ", body).strip()[:1200],
    }


def _summarize_matrix(payload: dict[str, Any]) -> dict[str, int]:
    readiness = Counter(str(profile["production_readiness"]) for profile in payload["profiles"])
    summary = {"total_states": len(payload["profiles"])}
    for key in [
        "production_ready_operator_supervised",
        "operator_assisted_required",
        "research_ready_certification_needed",
        "not_ready",
        "certified_portal_entry_observed",
    ]:
        summary[key] = readiness.get(key, 0)
    return summary


def _readiness_from_signals(profile: dict[str, Any], signals: list[dict[str, Any]]) -> tuple[str, str, str, str, str]:
    if any(item.get("cloudflare") or item.get("captcha_or_challenge") for item in signals):
        return (
            "playwright_persistent_browser_with_operator_challenge_fallback",
            "operator_assisted_required",
            "browser challenge observed during certification",
            "Use persistent Chrome profile on trusted operator machine; resume after human verification.",
            "observed",
        )
    if any(item.get("filing_entry_visible") for item in signals):
        return (
            "playwright_persistent_browser",
            "certified_portal_entry_observed",
            "",
            "Certify full filing path to payment screen and document retrieval for representative filing types.",
            "not_observed",
        )
    if profile.get("portal_urls"):
        return (
            "playwright_needs_certification",
            "research_ready_certification_needed",
            "portal reachable but filing entry was not observed on tested URLs",
            "Add deeper filing URLs or run an operator browser walkthrough.",
            "not_observed",
        )
    return (
        "needs_certification",
        "not_ready",
        "no portal URL available",
        "Repair state research and portal URL discovery.",
        "unknown",
    )


def _known_challenge_unresolved(state_code: str, signals: list[dict[str, Any]]) -> bool:
    challenged_urls = KNOWN_CHALLENGE_URLS.get(state_code, [])
    if not challenged_urls:
        return False
    normalized = [url.rstrip("/") for url in challenged_urls]
    for signal in signals:
        tested_url = str(signal.get("url", "")).rstrip("/")
        final_url = str(signal.get("final_url", "")).rstrip("/")
        if tested_url not in normalized and final_url not in normalized:
            continue
        if signal.get("cloudflare") or signal.get("captcha_or_challenge") or signal.get("error"):
            return True
        if signal.get("http_status") and int(signal["http_status"]) >= 400:
            return True
        return False
    return True


def certify_state(state_code: str, headless: bool = True, max_urls: int = 3) -> dict[str, Any]:
    payload = _load_matrix()
    state_code = state_code.upper()
    profile = next((item for item in payload["profiles"] if item["state_code"] == state_code), None)
    if not profile:
        raise SystemExit(f"Unknown state code: {state_code}")
    urls = profile.get("portal_urls", [])[:max_urls]
    if not urls:
        profile["blocker_summary"] = "No portal URL available for browser certification."
        profile["production_readiness"] = "not_ready"
        profile["automation_lane"] = "needs_certification"
        MATRIX_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return profile

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise SystemExit(
            "Python Playwright is required. Use app/.venv312/bin/python -m backend.regulatory.state_automation_matrix certify --state XX"
        ) from exc

    CERT_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    signals: list[dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        for index, url in enumerate(urls, start=1):
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
                title = page.title()
                body = page.locator("body").inner_text(timeout=8000)
                screenshot = CERT_SCREENSHOT_DIR / f"{state_code.lower()}_{index}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                signal = _detect_browser_signals(title, body, page.url, response.status if response else None)
                signal["url"] = url
                signal["screenshot_path"] = str(screenshot.relative_to(DATA_DIR))
            except Exception as exc:
                signal = {
                    "url": url,
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
            signals.append(signal)
        context.close()
        browser.close()

    lane, readiness, blocker, next_action, challenge_status = _readiness_from_signals(profile, signals)
    if _known_challenge_unresolved(state_code, signals):
        lane = "playwright_persistent_browser_with_trusted_access_operator"
        readiness = "operator_assisted_required"
        blocker = "Known protected filing endpoint still requires Trusted Access operator verification before automation can resume."
        next_action = "Run this state from the Trusted Access operator machine with a persistent browser profile, then certify submission, status polling, and document retrieval."
        challenge_status = KNOWN_HUMAN_CHALLENGES.get(state_code, "operator_verification_required")
    if state_code in KNOWN_PROVEN_STATES and readiness != "operator_assisted_required":
        for key, value in KNOWN_PROVEN_STATES[state_code].items():
            if key in {"automation_lane", "production_readiness", "blocker_summary", "next_action", "human_challenge_status"}:
                continue
            profile[key] = value
        lane = KNOWN_PROVEN_STATES[state_code]["automation_lane"]
        readiness = KNOWN_PROVEN_STATES[state_code]["production_readiness"]
        blocker = KNOWN_PROVEN_STATES[state_code]["blocker_summary"]
        next_action = KNOWN_PROVEN_STATES[state_code]["next_action"]
        challenge_status = KNOWN_PROVEN_STATES[state_code]["human_challenge_status"]
    profile["automation_lane"] = lane
    profile["production_readiness"] = readiness
    profile["blocker_summary"] = blocker
    profile["next_action"] = next_action
    profile["human_challenge_status"] = challenge_status
    profile["last_certified_at"] = utc_now()
    profile["browser_certification"] = {
        "certified_at": profile["last_certified_at"],
        "headless": headless,
        "signals": signals,
    }
    payload["generated_at"] = utc_now()
    payload["summary"] = _summarize_matrix(payload)
    MATRIX_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return profile


def certify_all_states(
    *,
    headless: bool = True,
    max_urls: int = 3,
    limit: int | None = None,
    include_ready: bool = False,
    retry_attempted: bool = False,
    states: list[str] | None = None,
) -> dict[str, Any]:
    payload = _load_matrix()
    wanted = {state.upper() for state in states or []}
    targets: list[str] = []
    for profile in payload["profiles"]:
        state = profile["state_code"]
        if wanted and state not in wanted:
            continue
        if not profile.get("portal_urls"):
            continue
        if not retry_attempted and profile.get("browser_certification"):
            continue
        if not include_ready and profile.get("production_readiness") in {
            "production_ready_operator_supervised",
            "operator_assisted_required",
            "certified_portal_entry_observed",
        }:
            continue
        targets.append(state)
    if limit is not None:
        targets = targets[:limit]

    results: list[dict[str, Any]] = []
    for state in targets:
        try:
            profile = certify_state(state, headless=headless, max_urls=max_urls)
            results.append(
                {
                    "state_code": state,
                    "production_readiness": profile.get("production_readiness"),
                    "automation_lane": profile.get("automation_lane"),
                    "blocker_summary": profile.get("blocker_summary"),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "state_code": state,
                    "production_readiness": "certification_error",
                    "automation_lane": "needs_operator_review",
                    "blocker_summary": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
            )

    final_payload = _load_matrix()
    return {
        "started": len(targets),
        "summary": final_payload["summary"],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SOSFiler state automation matrix.")
    parser.add_argument("command", choices=["build", "status", "certify", "certify-all", "refresh-urls"], nargs="?", default="build")
    parser.add_argument("--state")
    parser.add_argument("--states", nargs="*")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--include-ready", action="store_true")
    parser.add_argument("--retry-attempted", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-urls", type=int, default=3)
    args = parser.parse_args()
    if args.command == "build":
        payload = write_matrix()
    elif args.command == "certify":
        if not args.state:
            raise SystemExit("--state is required for certify")
        print(json.dumps(certify_state(args.state, headless=not args.headed, max_urls=args.max_urls), indent=2, sort_keys=True))
        return 0
    elif args.command == "certify-all":
        print(
            json.dumps(
                certify_all_states(
                    headless=not args.headed,
                    max_urls=args.max_urls,
                    limit=args.limit,
                    include_ready=args.include_ready,
                    retry_attempted=args.retry_attempted,
                    states=args.states,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    elif args.command == "refresh-urls":
        payload = refresh_portal_hints()
    else:
        payload = json.loads(MATRIX_PATH.read_text()) if MATRIX_PATH.exists() else write_matrix()
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
