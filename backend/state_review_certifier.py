"""Safe review-screen certification runner for state filing portals.

This runner is the next gate after "portal entry observed". It is designed to
advance a state only when we have evidence that a notional filing can reach the
review/payment-prep stage without submitting anything.
"""

from __future__ import annotations

import argparse
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from state_automation_profiles import (
        CERTIFICATION_GATES_PATH,
        all_state_adapter_manifests,
        certification_worklist,
        load_certification_gate_overrides,
        write_state_adapter_manifest,
    )
except ModuleNotFoundError:  # pragma: no cover - package execution path
    from .state_automation_profiles import (
        CERTIFICATION_GATES_PATH,
        all_state_adapter_manifests,
        certification_worklist,
        load_certification_gate_overrides,
        write_state_adapter_manifest,
    )


BASE_DIR = Path(__file__).resolve().parent.parent
RUNS_PATH = BASE_DIR / "data" / "regulatory" / "review_certification_runs.json"
SCREENSHOT_DIR = BASE_DIR / "data" / "regulatory" / "review_certification_screenshots"

DANGEROUS_SUBMIT_TERMS = (
    "submit filing",
    "file now",
    "complete filing",
    "place order",
    "pay now",
    "authorize payment",
)

REVIEW_TERMS = (
    "review",
    "summary",
    "confirm",
    "checkout",
    "cart",
    "payment",
    "fee",
)

LOGIN_TERMS = (
    "login",
    "log in",
    "sign in",
    "user id",
    "password",
    "create account",
)

CHALLENGE_TERMS = (
    "captcha",
    "cloudflare",
    "cf-chl",
    "cf-turnstile",
    "g-recaptcha",
    "recaptcha",
    "turnstile",
    "imperva",
    "incapsula",
    "error 15",
    "just a moment",
    "security verification",
    "access denied",
    "request was blocked",
    "botmanager",
    "radware",
)

SCRIPTED_STATES: dict[str, str] = {
    "TX": "tx_sosdirect_v1",
    "AL": "al_sos_online_services_v1",
    "AK": "ak_cbpl_create_entity_v1",
    "AR": "ar_sos_corpfilings_v1",
    "AZ": "az_ecorp_v1",
    "CA": "ca_bizfile_v1",
    "CO": "co_business_filing_v1",
    "CT": "ct_business_services_v1",
    "DE": "de_ecorp_document_upload_v1",
    "FL": "fl_sunbiz_efile_v1",
    "GA": "ga_ecorp_v1",
    "HI": "hi_bizex_v1",
    "IA": "ia_fast_track_filing_v1",
    "ID": "id_sosbiz_v1",
    "IL": "il_corporatellc_v1",
    "IN": "in_inbiz_v1",
    "KS": "ks_eforms_v1",
    "KY": "ky_fasttrack_v1",
    "LA": "la_geauxbiz_v1",
    "MA": "ma_corpweb_v1",
    "MD": "md_business_express_v1",
    "ME": "me_aro_v1",
    "MI": "mi_mibusiness_registry_v1",
    "MN": "mn_mbls_v1",
    "MO": "mo_bsd_v1",
    "MS": "ms_corp_portal_v1",
    "MT": "mt_biz_sosmt_v1",
    "NC": "nc_sosnc_online_filing_v1",
    "ND": "nd_firststop_v1",
    "NE": "ne_corp_filing_v1",
    "NH": "nh_quickstart_v1",
    "NJ": "nj_business_formation_v1",
    "NM": "nm_enterprise_v1",
    "NV": "nv_silverflume_v1",
    "NY": "ny_business_express_v1",
    "OH": "oh_obc_v1",
    "OK": "ok_sos_wizard_v1",
    "OR": "or_cbr_manager_v1",
    "PA": "pa_file_dos_v1",
    "RI": "ri_business_sos_v1",
    "SC": "sc_business_filings_v1",
    "SD": "sd_sosenterprise_v1",
    "TN": "tn_tnbear_v1",
    "UT": "ut_business_registration_v1",
    "VA": "va_cis_scc_v1",
    "VT": "vt_bizfilings_v1",
    "WA": "wa_ccfs_v1",
    "WV": "wv_onestop_v1",
    "WI": "wi_wdfi_v1",
    "WY": "wy_wyobiz_v1",
    "DC": "dc_corponline_v1",
}

LOW_VALUE_URL_PATTERNS = (
    ".pdf",
    "/uploads/",
    "/forms/",
    "forms-fees",
    "/documents/",
    "/content/dam/",
    "fincen.gov/boi",
)
HIGH_VALUE_URL_TERMS = (
    "filing",
    "corp",
    "business",
    "register",
    "login",
    "portal",
    "checklist",
    "create",
    "formation",
    "bizfile",
    "ecorp",
    "corpfilings",
    "sosbiz",
    "silverflume",
    "fasttrack",
    "inbiz",
    "corponline",
    "efile",
    "direct.sos",
    "secretaryofstate",
    "cis.scc",
    "ccfs",
    "firststop",
    "geauxbiz",
    "ohiobusinesscentral",
    "businessfilings",
    "cbrmanager",
    "file.dos",
    "onestopshop",
    "bsd.sos",
    "biz.sosmt",
    "wdfi",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_state(value: str) -> str:
    value = (value or "").upper().strip()
    if not re.fullmatch(r"[A-Z]{2}|DC", value):
        raise ValueError(f"Invalid state code: {value}")
    return value


def redact_text(value: str) -> str:
    value = re.sub(r"\b\d{3}-?\d{2}-?\d{4}\b", "[REDACTED_SSN]", value or "")
    value = re.sub(r"\b\d{2}-?\d{7}\b", "[REDACTED_EIN]", value)
    value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[REDACTED_IP]", value)
    value = re.sub(r"\b[0-9a-fA-F:]{12,}\b", "[REDACTED_NETWORK_ID]", value)
    value = re.sub(r"\b[A-Za-z0-9+/]{80,}={0,2}\b", "[REDACTED_ENCODED_VALUE]", value)
    value = re.sub(r"(incident[_ -]?id=)[^&\\s\"']+", r"\1[REDACTED_INCIDENT]", value, flags=re.I)
    value = re.sub(r"(Incapsula incident ID: )[^<\\s]+", r"\1[REDACTED_INCIDENT]", value, flags=re.I)
    return value


def redact_url(value: str) -> str:
    if not value:
        return ""
    sensitive_keys = {
        "amauthcookie",
        "goto",
        "relaystate",
        "reqid",
        "request",
        "secondvisiturl",
        "session",
        "sid",
        "state",
        "token",
    }
    try:
        parts = urlsplit(value)
        query: list[tuple[str, str]] = []
        for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in sensitive_keys or len(item_value) > 80:
                query.append((key, "[REDACTED]"))
            else:
                query.append((key, item_value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    except Exception:
        return redact_text(value)


def notional_filing(state: str) -> dict[str, Any]:
    return {
        "business_name": f"SOSFiler {state} Review Test LLC",
        "entity_type": "LLC",
        "state": state,
        "purpose": "Any lawful purpose",
        "principal_address": "1 Main St",
        "principal_city": "Capital City",
        "principal_state": state,
        "principal_zip": "00000",
        "registered_agent_name": "SOSFiler Review Agent",
        "management": "member_managed",
        "organizer": "SOSFiler Automation Certification",
    }


def best_portal_url(urls: list[str]) -> str:
    if not urls:
        return ""
    candidates: list[tuple[int, str]] = []
    for index, url in enumerate(urls):
        value = str(url)
        lowered = value.lower()
        if not lowered.startswith(("http://", "https://")):
            continue
        score = 100 - index
        if any(pattern in lowered for pattern in LOW_VALUE_URL_PATTERNS):
            score -= 500
        high_value = any(term in lowered for term in HIGH_VALUE_URL_TERMS)
        if high_value:
            score += 200
        if re.fullmatch(r"https?://[^/]+/?", lowered) and not high_value:
            score -= 80
        candidates.append((score, value))
    if not candidates:
        return str(urls[0])
    return sorted(candidates, reverse=True)[0][1]


def load_runs(path: Path = RUNS_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "runs": []}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"version": 1, "runs": []}


def append_run(run: dict[str, Any], path: Path = RUNS_PATH) -> None:
    payload = load_runs(path)
    payload.setdefault("runs", []).append(run)
    payload["updated_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_gate_result(state: str, gate: str, result: dict[str, Any], path: Path = CERTIFICATION_GATES_PATH) -> None:
    payload = load_certification_gate_overrides(path)
    payload.setdefault("states", {}).setdefault(state, {})[gate] = result
    payload["updated_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def classify_review_signals(
    *,
    state: str,
    body: str,
    title: str = "",
    url: str = "",
    http_status: int | None = None,
    scripted: bool = False,
) -> dict[str, Any]:
    haystack = " ".join([body or "", title or "", url or ""]).lower()
    challenge = http_status in {401, 403, 429} or any(term in haystack for term in CHALLENGE_TERMS)
    login_required = any(term in haystack for term in LOGIN_TERMS)
    review_visible = any(term in haystack for term in REVIEW_TERMS)
    dangerous_submit_visible = any(term in haystack for term in DANGEROUS_SUBMIT_TERMS)
    if challenge:
        status = "trusted_access_required"
        summary = "Portal challenge or access control observed before review screen."
    elif not scripted:
        status = "needs_state_script"
        summary = "Portal was reachable, but no state-specific form script exists to safely reach the review screen."
    elif login_required:
        status = "login_required"
        summary = "Login/account step observed before review screen certification."
    elif review_visible and not dangerous_submit_visible:
        status = "complete"
        summary = "Review/payment-prep screen observed without a final-submit action."
    elif dangerous_submit_visible:
        status = "blocked"
        summary = "Final submit/payment action was visible; certifier stopped before any irreversible action."
    else:
        status = "blocked"
        summary = "Review screen was not observed."
    return {
        "status": status,
        "summary": summary,
        "signals": {
            "challenge": challenge,
            "login_required": login_required,
            "review_visible": review_visible,
            "dangerous_submit_visible": dangerous_submit_visible,
            "http_status": http_status,
            "final_url": redact_url(url),
            "title": title,
            "body_preview": redact_text(re.sub(r"\s+", " ", body or "").strip()[:1200]),
        },
    }


def page_text_snapshot(page) -> tuple[str, str, str]:
    title = ""
    body = ""
    content = ""
    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        body = page.locator("body").inner_text(timeout=8000)
    except Exception:
        body = ""
    try:
        content = page.content()
    except Exception:
        content = ""
    return title, "\n".join(part for part in [body, content] if part), content


def visible_form_controls(page, limit: int = 120) -> list[dict[str, str]]:
    try:
        controls = page.locator("input,select,textarea,button").evaluate_all(
            """(els, limit) => els.slice(0, limit).map((e) => ({
                tag: e.tagName || "",
                name: e.name || "",
                id: e.id || "",
                type: e.type || "",
                value: e.value || "",
                text: e.innerText || "",
                label: e.labels && e.labels.length
                    ? Array.from(e.labels).map((l) => l.innerText || "").join(" | ")
                    : ""
            }))""",
            limit,
        )
        sensitive_name_tokens = (
            "password", "passwd", "pwd",
            "ssn", "ein", "tin",
            "creditcard", "cardnumber", "ccnum", "cvv", "cvc",
            "license", "dln", "driverid",
            "account", "routing",
        )
        for control in controls:
            ctype = str(control.get("type", "")).lower()
            name_blob = " ".join(str(v).lower() for v in (control.get("name", ""), control.get("id", "")))
            looks_like_captcha = any(token in name_blob for token in ("captcha", "turnstile", "cf-chl"))
            looks_sensitive = any(token in name_blob for token in sensitive_name_tokens)
            value = control.get("value")
            if ctype == "hidden" and value:
                control["value"] = "[REDACTED_HIDDEN_VALUE]"
                control["text"] = ""
            elif looks_like_captcha and value:
                control["value"] = "[REDACTED_CAPTCHA_TOKEN]"
                control["text"] = ""
            elif ctype == "password" and value:
                control["value"] = "[REDACTED_PASSWORD]"
                control["text"] = ""
            elif ctype in {"email", "tel"} and value:
                control["value"] = "[REDACTED_PII]"
                control["text"] = ""
            elif looks_sensitive and value:
                control["value"] = "[REDACTED_SENSITIVE_FIELD]"
                control["text"] = ""
            if isinstance(control.get("value"), str) and len(control["value"]) > 200:
                control["value"] = "[REDACTED_LONG_VALUE]"
        return controls
    except Exception:
        return []


def script_ca_bizfile(page, url: str, state: str) -> dict[str, Any]:
    response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3000)
    title, body, _content = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    if result["status"] == "blocked" and not result["signals"]["review_visible"]:
        result["status"] = "login_required"
        result["summary"] = "California BizFile loaded, but review-screen automation requires a logged-in BizFile session and state-specific form selectors."
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "If Imperva/Access Denied is observed, rerun from the verified Trusted Access operator browser profile.",
    ]
    return result


def script_co_business_filing(page, url: str, state: str) -> dict[str, Any]:
    llc_url = "https://www.coloradosos.gov/business/filing/llc/intro?transTyp=ARTORG_LLC&entityType=DLLC&entityId="
    response = page.goto(llc_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    title, body, _content = page_text_snapshot(page)
    intro = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    if intro["status"] == "trusted_access_required":
        intro["script_notes"] = [
            "No filing data was entered.",
            "Colorado LLC intro was blocked before the registered-agent step.",
        ]
        return intro

    page.locator("#saveNextId").click(force=True, timeout=15_000)
    page.wait_for_load_state("networkidle", timeout=45_000)
    page.locator("#individualRadioId").check(force=True, timeout=15_000)
    page.locator("#saveNextId").click(force=True, timeout=15_000)
    page.wait_for_load_state("networkidle", timeout=45_000)
    title, body, _content = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=200,
        scripted=True,
    )
    controls = visible_form_controls(page)
    agent_verification_visible = (
        "colorado driver's license or id card number" in body.lower()
        and "passcode" in body.lower()
    )
    if agent_verification_visible:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "Colorado LLC flow reaches registered-agent verification; customer intake must collect a Colorado "
            "driver's license/ID number or registered-agent passcode before automation can continue."
        )
        result["signals"]["review_visible"] = False
        result["signals"]["agent_verification_visible"] = True
        result["signals"]["required_controls"] = controls
        result["applicant_data_requirements"] = [
            {
                "field": "registered_agent_verification_method",
                "accepted_values": ["colorado_driver_license_or_id", "registered_agent_passcode"],
                "required": True,
            },
            {
                "field": "registered_agent_colorado_driver_license_or_id_number",
                "required_when": "registered_agent_verification_method=colorado_driver_license_or_id",
            },
            {
                "field": "registered_agent_passcode",
                "required_when": "registered_agent_verification_method=registered_agent_passcode",
            },
        ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Colorado registered-agent verification before entering identifying data.",
    ]
    return result


def script_ct_business_services(page, url: str, state: str) -> dict[str, Any]:
    formation_url = "https://service.ct.gov/business/s/brsflow"
    response = page.goto(formation_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(5000)
    title, body, _content = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    recaptcha_present = any("recaptcha" in " ".join(str(value).lower() for value in control.values()) for control in controls)
    login_fields_visible = any(control.get("id") == "loginEmail" for control in controls) and any(
        control.get("id") == "loginPassword" for control in controls
    )
    if recaptcha_present:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Connecticut business filing flow redirects to CT.GOV login with reCAPTCHA; run from the verified "
            "Trusted Access operator browser profile and authenticated CT.GOV account before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["recaptcha_present"] = True
    elif login_fields_visible:
        result["status"] = "login_required"
        result["summary"] = "Connecticut business filing flow requires CT.GOV account login before form automation can continue."
    result["signals"]["login_fields_visible"] = login_fields_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ctgov_account_email", "required": True},
        {"field": "ctgov_account_password_or_session", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at CT.GOV login before entering credentials.",
    ]
    return result


def script_ok_sos_wizard(page, url: str, state: str) -> dict[str, Any]:
    llc_url = "https://www.sos.ok.gov/corp/filing/Wizard.aspx?f=369"
    response = page.goto(llc_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3000)
    title, body, _content = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    contact_fields_visible = any(control.get("id") == "ctl00_DefaultContent_txtName" for control in controls)
    turnstile_present = any("turnstile" in " ".join(str(value).lower() for value in control.values()) for control in controls)
    if turnstile_present:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Oklahoma filing wizard reaches the contact-information step, but Cloudflare Turnstile is present; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["turnstile_present"] = True
    elif contact_fields_visible:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "Oklahoma filing wizard requires filer contact name and email before the entity-specific form starts."
        )
    result["signals"]["contact_fields_visible"] = contact_fields_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "filer_contact_name", "required": True},
        {"field": "filer_contact_email", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Oklahoma contact/challenge step before creating a filing work item.",
    ]
    return result


def script_al_sos_online_services(page, url: str, state: str) -> dict[str, Any]:
    intro_url = "https://www.alabamainteractive.org/sos/introduction_input.action"
    contact_url = "https://www.alabamainteractive.org/sos/contactInformation_input.action"
    intro_response = page.goto(intro_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    _intro_title, intro_body, _ = page_text_snapshot(page)
    intro_status = intro_response.status if intro_response else None

    response = page.goto(contact_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    title, body, _content = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    contact_fields_visible = any(control.get("id") == "contactName" for control in controls) and any(
        control.get("id") == "emailAddress" for control in controls
    )
    challenge_widget_present = any(
        any(token in " ".join(str(value).lower() for value in control.values())
            for token in ("turnstile", "g-recaptcha", "recaptcha-response"))
        for control in controls
    )
    body_lower = body.lower()
    access_blocked = (
        "incapsula" in body_lower
        or "access denied" in body_lower
        or "just a moment" in body_lower
        or "request was blocked" in body_lower
    )
    if challenge_widget_present or access_blocked or intro_status in {401, 403, 429}:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Alabama SOS Online Services portal showed an active CAPTCHA or access-control challenge; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif contact_fields_visible:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "Alabama SOS Online Services requires filer contact name, phone, email, and street address before "
            "the filing-type-specific form starts."
        )
        result["signals"]["challenge"] = False
    result["signals"]["contact_fields_visible"] = contact_fields_visible
    result["signals"]["challenge_widget_present"] = challenge_widget_present
    result["signals"]["required_controls"] = controls
    result["signals"]["intro_fee_disclosure_visible"] = "total fee through alabama.gov" in intro_body.lower()
    result["applicant_data_requirements"] = [
        {"field": "filer_contact_name", "required": True},
        {"field": "filer_primary_phone", "required": True},
        {"field": "filer_email_address", "required": True},
        {"field": "filer_street_address", "required": True},
        {"field": "filer_city", "required": True},
        {"field": "filer_state", "required": True},
        {"field": "filer_zip_code", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Alabama SOS contact-information step before entering filer data or selecting a filing type.",
    ]
    return result


def _safe_goto(page, url: str, timeout: int = 45_000, settle_ms: int = 2500) -> dict[str, Any]:
    """Read-only navigation that never raises. Returns title/body/status/error fields."""
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(settle_ms)
        title, body, content = page_text_snapshot(page)
        return {
            "title": title,
            "body": body,
            "content": content,
            "http_status": response.status if response else None,
            "final_url": page.url,
            "tls_error": False,
        }
    except Exception:
        return {
            "title": "",
            "body": "",
            "content": "",
            "http_status": None,
            "final_url": url,
            "tls_error": True,
        }


def _values_contain(controls: list[dict[str, Any]], tokens: tuple[str, ...]) -> bool:
    for control in controls:
        blob = " ".join(str(value).lower() for value in control.values())
        if any(token in blob for token in tokens):
            return True
    return False


_BLOCK_PAGE_PHRASES = (
    "incapsula incident",
    "errors.edgesuite.net",
    "this request was blocked by our security service",
    "performing security verification",
    "verifying you are a human",
    "attention required",
)
_BLOCK_TITLE_PHRASES = (
    "access denied",
    "just a moment",
    "attention required",
    "blocked",
)


def _body_blocked(body: str, title: str = "", http_status: int | None = None) -> bool:
    """Return True only when an observable block page is visible (not when a script tag references a vendor)."""
    body_lower = (body or "").lower()
    title_lower = (title or "").lower()
    if any(phrase in body_lower for phrase in _BLOCK_PAGE_PHRASES):
        return True
    if any(phrase in title_lower for phrase in _BLOCK_TITLE_PHRASES):
        return True
    if http_status in {401, 403, 429} and (
        "access denied" in body_lower or "request was blocked" in body_lower
    ):
        return True
    return False


def script_ar_sos_corpfilings(page, url: str, state: str) -> dict[str, Any]:
    intro_url = "https://www.ark.org/sos/corpfilings/index.php"
    response = page.goto(intro_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2000)
    intro_status = response.status if response else None
    try:
        page.evaluate("showOptions(13)")
        page.wait_for_timeout(1500)
    except Exception:
        pass
    advanced = False
    try:
        advanced = bool(page.evaluate(
            """() => {
                const danger = ["submit filing","file now","complete filing","place order","pay now","authorize payment","save form","savefilingfee"];
                for (const inp of document.querySelectorAll("input[name='form_id']")) {
                    if (inp.value !== '13') continue;
                    const f = inp.closest('form');
                    if (!f) continue;
                    const btn = f.querySelector("input[name^='do:StartForm']");
                    if (!btn) return false;
                    const blob = ((btn.value || '') + ' ' + (btn.name || '') + ' ' + (btn.innerText || '')).toLowerCase();
                    if (danger.some(t => blob.includes(t))) return false;
                    btn.click();
                    return true;
                }
                return false;
            }"""
        ))
    except Exception:
        advanced = False
    if advanced:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2500)
        except Exception:
            pass
    title, body, _content = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    names = {control.get("name") for control in controls}
    ll01_fields_visible = (
        "entity_name" in names
        and "contact_address_1" in names
        and "tax_contact_email" in names
        and "filing_signature" in names
    )
    challenge_widget = _values_contain(controls, ("turnstile", "g-recaptcha", "recaptcha-response"))
    if challenge_widget or _body_blocked(body) or intro_status in {401, 403, 429}:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Arkansas SOS corpfilings portal showed an access-control challenge; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif ll01_fields_visible:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "Arkansas SOS corpfilings LL-01 Domestic LLC form requires entity name, principal/registered-agent/"
            "tax-contact addresses, signatures, and agreement acknowledgement before the do:SaveForm submit."
        )
    result["signals"]["ll01_fields_visible"] = ll01_fields_visible
    result["signals"]["challenge_widget_present"] = challenge_widget
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "filing_act_selection", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "principal_name_individual_or_entity", "required": True},
        {"field": "principal_address_1", "required": True},
        {"field": "principal_city", "required": True},
        {"field": "principal_state", "required": True},
        {"field": "principal_zip_code", "required": True},
        {"field": "principal_country", "required": True},
        {"field": "registered_agent_contact_name", "required": True},
        {"field": "registered_agent_address_1", "required": True},
        {"field": "registered_agent_city", "required": True},
        {"field": "registered_agent_state", "required": True},
        {"field": "registered_agent_zip_code", "required": True},
        {"field": "registered_agent_phone_number", "required": True},
        {"field": "registered_agent_email", "required": True},
        {"field": "tax_contact_name", "required": True},
        {"field": "tax_contact_signature", "required": True},
        {"field": "filing_signature", "required": True},
        {"field": "agreement_acknowledgement", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Only the navigational 'Start Form' (do:StartForm) button is clicked, after a defensive guard rejects "
        "any button whose label/name matches DANGEROUS_SUBMIT_TERMS. do:SaveForm is never invoked.",
    ]
    return result


def script_ak_cbpl_create_entity(page, url: str, state: str) -> dict[str, Any]:
    target = "https://www.commerce.alaska.gov/web/cbpl/Corporations/CreateFileNewEntity"
    response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3000)
    title, body, _ = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    body_lower = body.lower()
    challenge_widget = _values_contain(controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"))
    myalaska_signin = "myalaska" in body_lower and "sign-in" in body_lower.replace(" ", "-")
    guidance_only = "create or file for a new entity" in body_lower and not any(
        control.get("type") in {"text", "email", "tel", "password"} for control in controls
    )
    if challenge_widget or _body_blocked(body) or (response and response.status in {401, 403, 429, 503}):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Alaska CBPL portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif guidance_only and myalaska_signin:
        result["status"] = "login_required"
        result["summary"] = (
            "Alaska CBPL CreateFileNewEntity is a guidance page; the entity-formation wizard requires "
            "myAlaska SSO login before any filer data can be entered."
        )
    result["signals"]["myalaska_signin_visible"] = myalaska_signin
    result["signals"]["guidance_page_only"] = guidance_only
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "myalaska_account_login", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "proposed_entity_name", "required": True},
        {"field": "naics_code", "required": True},
        {"field": "filer_contact_name", "required": True},
        {"field": "filer_contact_email", "required": True},
        {"field": "filer_contact_phone", "required": True},
        {"field": "registered_agent_name", "required": True},
        {"field": "registered_agent_physical_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "mailing_address", "required": True},
        {"field": "member_or_manager_info", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stops at Alaska CBPL guidance page / myAlaska SSO login wall before the entity wizard.",
    ]
    return result


def script_az_ecorp(page, url: str, state: str) -> dict[str, Any]:
    target = "https://ecorp.azcc.gov"
    response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3000)
    title, body, _ = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    body_lower = body.lower()
    recaptcha_disclosed = any(phrase in body_lower for phrase in (
        "protected by recaptcha",
        "i'm not a robot",
        "verify you are human",
        "google recaptcha",
    ))
    login_visible = any(
        control.get("type") == "password" or control.get("id", "").lower() in {"userid", "username", "email"}
        for control in controls
    )
    challenge_widget = _values_contain(controls, ("turnstile", "g-recaptcha", "recaptcha-response", "imperva", "incapsula"))
    if challenge_widget or _body_blocked(body) or recaptcha_disclosed:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Arizona eCorp portal requires login and discloses reCAPTCHA verification on filing flows; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif login_visible:
        result["status"] = "login_required"
        result["summary"] = "Arizona eCorp portal requires operator account login before automation can continue."
    result["signals"]["recaptcha_disclosed"] = recaptcha_disclosed
    result["signals"]["login_visible"] = login_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "az_ecorp_account_email", "required": True},
        {"field": "az_ecorp_account_password_or_session", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Arizona eCorp login/challenge gate.",
    ]
    return result


def script_de_ecorp_document_upload(page, url: str, state: str) -> dict[str, Any]:
    target = "https://corp.delaware.gov/document-upload-service-information/"
    response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    title, body, _ = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    body_lower = body.lower()
    submission_only_disclosed = (
        "submission only" in body_lower
        and "does not provide for direct online filing" in body_lower
    )
    my_ecorp_account_required = "my ecorp account" in body_lower
    anti_automation_terms = False
    captcha_present_on_tax_portal = False
    try:
        page.goto(
            "https://icis.corp.delaware.gov/ecorp/logintax.aspx",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        page.wait_for_timeout(2000)
        _, ecorp_body, _ = page_text_snapshot(page)
        ecorp_lower = ecorp_body.lower()
        anti_automation_terms = "use of automated tools" in ecorp_lower
        captcha_present_on_tax_portal = "type code from the image" in ecorp_lower or "captcha" in ecorp_lower
    except Exception:
        captcha_present_on_tax_portal = True
    result["status"] = "requires_registered_agent_intermediary"
    result["summary"] = (
        "Delaware has no public self-service formation portal. Route through a commercial Delaware "
        "registered agent (Harvard, CSC, NRAI, InCorp). The eCorp Document Upload Service is submission-only "
        "behind a My eCorp account, and the eCorp tax portal carries image CAPTCHA plus explicit "
        "anti-automation terms of use."
    )
    result["signals"]["submission_only_disclosed"] = submission_only_disclosed
    result["signals"]["my_ecorp_account_required"] = my_ecorp_account_required
    result["signals"]["anti_automation_terms"] = anti_automation_terms
    result["signals"]["captcha_present_on_tax_portal"] = captcha_present_on_tax_portal
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "registered_agent_provider", "required": True,
         "accepted_values": ["harvard_business_services", "csc", "nrai", "incorp",
                              "corporation_service_co", "self_supplied_delaware_ra"]},
        {"field": "registered_agent_delaware_street_address", "required": True},
        {"field": "entity_type", "required": True,
         "accepted_values": ["LLC", "C_CORP", "LP", "LLP", "NONPROFIT", "STATUTORY_TRUST"]},
        {"field": "entity_name_with_required_suffix", "required": True},
        {"field": "authorized_shares_and_par_value",
         "required_when": "entity_type=C_CORP"},
        {"field": "organizer_or_incorporator_signature", "required": True},
        {"field": "filer_contact_name", "required": True},
        {"field": "filer_email_address", "required": True},
        {"field": "filer_phone", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No login attempted; eCorp portal probed read-only.",
        "Recommend registered-agent API integration (Harvard or CSC) over portal scripting.",
        "Direct portal path would require My eCorp credentials and CAPTCHA-solver, run from Trusted Access.",
    ]
    return result


def script_fl_sunbiz_efile(page, url: str, state: str) -> dict[str, Any]:
    intro_url = "https://dos.fl.gov/sunbiz/start-business/efile/fl-llc/"
    efile_url = "https://efile.sunbiz.org/llc_file.html"
    try:
        intro_resp = page.goto(intro_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2000)
        _intro_title, _intro_body, intro_content = page_text_snapshot(page)
        intro_status = intro_resp.status if intro_resp else None
    except Exception:
        intro_status = None
        intro_content = ""
    response = page.goto(efile_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(4000)
    title, body, content = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    blob = (content or "").lower() + " " + " ".join(json.dumps(control) for control in controls).lower()
    real_turnstile = any(
        token in blob
        for token in ("cf-turnstile-response", "cf-chl-widget", "__cf_chl_", "challenge-platform", "just a moment")
    )
    if real_turnstile or (response and response.status in {401, 403, 429}):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Florida Sunbiz e-file portal presents an active Cloudflare Turnstile challenge; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["turnstile_present"] = True
        result["signals"]["cloudflare_ray_id_visible"] = "ray id" in body.lower()
    result["signals"]["intro_status"] = intro_status
    result["signals"]["intro_links_to_efile_host"] = "efile.sunbiz.org/llc_file.html" in intro_content.lower()
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "operator_browser_profile", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Cloudflare Turnstile challenge on efile.sunbiz.org before any LLC form step.",
    ]
    return result


def script_ga_ecorp(page, url: str, state: str) -> dict[str, Any]:
    target = "https://ecorp.sos.ga.gov/"
    response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    title, body, _ = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    challenge_widget = _values_contain(controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"))
    body_lower = body.lower()
    login_visible = any(
        control.get("id", "").lower() in {"txtuserid", "txtpassword"}
        for control in controls
    ) or any(control.get("type") == "password" for control in controls)
    if challenge_widget or _body_blocked(body) or (response and response.status in {401, 403, 429}):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Georgia eCorp portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif login_visible or "log in" in body_lower:
        result["status"] = "login_required"
        result["summary"] = (
            "Georgia eCorp portal requires a Corporations Division online account login before the "
            "entity-formation wizard opens."
        )
    result["signals"]["login_visible"] = login_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ga_ecorp_user_id", "required": True},
        {"field": "ga_ecorp_password_or_session", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Georgia eCorp login gate before entering credentials.",
    ]
    return result


def script_hi_bizex(page, url: str, state: str) -> dict[str, Any]:
    start_url = "https://hbe.ehawaii.gov/BizEx/start.eb"
    response = page.goto(start_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    title, body, _ = page_text_snapshot(page)
    final_url = page.url
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=final_url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    body_lower = body.lower()
    on_sso_gate = (
        "login-s.eb" in final_url
        or "go to sso login" in body_lower
        or "log in to access protected services" in body_lower
        or "myhawaii" in body_lower
    )
    challenge_widget = _values_contain(controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"))
    if challenge_widget or _body_blocked(body):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Hawaii BizEx SSO gate showed an active CAPTCHA/access challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif on_sso_gate:
        result["status"] = "login_required"
        result["summary"] = (
            "Hawaii BizEx requires myHawaii Single Sign-On (SSO) login before the LLC/Corp formation "
            "flow opens; provision a credentialed operator myHawaii account."
        )
        result["signals"]["sso_login_required"] = True
    result["signals"]["final_url"] = redact_url(final_url)
    result["signals"]["required_controls"] = controls
    result["signals"]["portal_transition_notice"] = "temporarily being offered" in body_lower
    result["applicant_data_requirements"] = [
        {"field": "myhawaii_account_username", "required": True},
        {"field": "myhawaii_account_password_or_session", "required": True},
        {"field": "filer_contact_email", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "registered_agent_name", "required": True},
        {"field": "registered_agent_hi_street_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_or_incorporator_name_and_address", "required": True},
        {"field": "manager_or_member_managed_indicator", "required": True},
        {"field": "separate_liability_for_debts_indicator", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No SSO credentials were submitted.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at the myHawaii SSO login gate before any data-entry step.",
    ]
    return result


def script_ia_fast_track_filing(page, url: str, state: str) -> dict[str, Any]:
    target = "https://filings.sos.iowa.gov/Account/Login"
    response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3000)
    title, body, _ = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    status_code = response.status if response else None
    body_lower = body.lower()
    akamai_block = (
        status_code in {401, 403, 429}
        and (
            "access denied" in body_lower
            or "errors.edgesuite.net" in body_lower
            or "reference #" in body_lower
        )
    )
    password_present = any(str(control.get("type", "")).lower() == "password" for control in controls)
    if akamai_block or _body_blocked(body):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Iowa Fast Track Filing portal is fronted by Akamai edge bot detection returning HTTP 403 "
            "Access Denied to headless sessions; run from the verified Trusted Access operator browser "
            "profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["edge_block"] = True
        result["signals"]["edge_provider"] = "akamai_edgesuite"
    elif password_present:
        result["status"] = "login_required"
        result["summary"] = "Iowa Fast Track Filing portal reached login wall; account credentials required."
    result["signals"]["required_controls"] = controls
    result["signals"]["http_status"] = status_code
    result["applicant_data_requirements"] = [
        {"field": "ia_fast_track_account_email", "required": True},
        {"field": "ia_fast_track_account_password_or_session", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Iowa Akamai edge block or login wall before entering credentials.",
    ]
    return result


def script_id_sosbiz(page, url: str, state: str) -> dict[str, Any]:
    target = "https://sosbiz.idaho.gov/forms/business"
    response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3000)
    title, body, _ = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    body_lower = body.lower()
    challenge_widget = _values_contain(controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"))
    login_required_visible = (
        "click on the login button" in body_lower
        or ("login" in body_lower and "create an account" in body_lower)
    )
    if challenge_widget or _body_blocked(body) or (response and response.status in {401, 403, 429}):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Idaho SOSBiz portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif login_required_visible:
        result["status"] = "login_required"
        result["summary"] = (
            "Idaho SOSBiz requires account creation or login before any LLC/corp formation form is reachable."
        )
    result["signals"]["login_required_visible"] = login_required_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "id_sosbiz_account_email", "required": True},
        {"field": "id_sosbiz_account_password_or_session", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No account was created.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Idaho SOSBiz login gate before entering credentials.",
    ]
    return result


def script_in_inbiz(page, url: str, state: str) -> dict[str, Any]:
    home_url = "https://inbiz.in.gov/BOS/Home/Index"
    response = page.goto(home_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3000)
    title, body, _ = page_text_snapshot(page)
    result = classify_review_signals(
        state=state,
        body=body,
        title=title,
        url=page.url,
        http_status=response.status if response else None,
        scripted=True,
    )
    controls = visible_form_controls(page)
    html = page.content().lower()
    body_lower = body.lower()
    sso_required = "log in" in body_lower and ("access indiana" in body_lower or "in.gov" in html)
    try:
        page.goto("https://inbiz.in.gov/BOS/Account/Login", wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2000)
        redirected_to_home = page.url.rstrip("/").endswith("/BOS/Home/Index")
    except Exception:
        redirected_to_home = False
    recaptcha_present = (
        "g-recaptcha-response" in html
        or "class=\"g-recaptcha\"" in html
        or "www.google.com/recaptcha" in html
    )
    if recaptcha_present or _body_blocked(body) or sso_required or redirected_to_home:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Indiana INBiz portal requires an Access Indiana (in.gov SSO) operator login and presents a "
            "Google reCAPTCHA on the public surface; run from the verified Trusted Access operator browser "
            "profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["recaptcha_present"] = recaptcha_present
        result["signals"]["sso_required"] = sso_required
        result["signals"]["login_redirects_to_home"] = redirected_to_home
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "access_indiana_account_email", "required": True},
        {"field": "access_indiana_account_password", "required": True},
        {"field": "access_indiana_mfa_token", "required": True},
        {"field": "filer_contact_name", "required": True},
        {"field": "filer_contact_email", "required": True},
        {"field": "filer_primary_phone", "required": True},
        {"field": "filer_street_address", "required": True},
        {"field": "filer_city", "required": True},
        {"field": "filer_state", "required": True},
        {"field": "filer_zip_code", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No Access Indiana login attempted.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at SSO/recaptcha gateway before the INBiz Start-a-Business wizard.",
    ]
    return result


def script_il_corporatellc(page, url: str, state: str) -> dict[str, Any]:
    target = "https://apps.ilsos.gov/corporatellc/"
    try:
        response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2500)
        title, body, _ = page_text_snapshot(page)
        http_status = response.status if response else None
        h2_block = False
    except Exception:
        title, body = "", ""
        http_status = None
        h2_block = True
    result = classify_review_signals(
        state=state, body=body, title=title, url=page.url if not h2_block else target,
        http_status=http_status, scripted=True,
    )
    controls = visible_form_controls(page) if not h2_block else []
    body_lower = body.lower()
    akamai_block = (
        http_status in {401, 403, 429}
        and ("reference id" in body_lower or "webmaster@ilsos.gov" in body_lower)
    )
    if akamai_block or h2_block or _body_blocked(body):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Illinois SOS apps.ilsos.gov is gated by Akamai Bot Manager (AkamaiGHost 403 with 'Reference ID' "
            "page) and www.ilsos.gov rejects headless HTTP/2; route IL filings through the verified Trusted "
            "Access operator browser provider."
        )
        result["signals"]["challenge"] = True
        result["signals"]["akamai_bot_manager"] = akamai_block
        result["signals"]["h2_fingerprint_block"] = h2_block
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "operator_browser_profile", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name", "required": True},
        {"field": "registered_agent_il_street_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_name_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Illinois Akamai edge block before the LLC formation entry was reachable.",
    ]
    return result


def script_ks_eforms(page, url: str, state: str) -> dict[str, Any]:
    target = "https://www.sos.ks.gov/eforms/user_login.aspx?frm=BS"
    snap = _safe_goto(page, target, timeout=45_000, settle_ms=2500)
    body = snap["body"]
    result = classify_review_signals(
        state=state, body=body, title=snap["title"], url=snap["final_url"],
        http_status=snap["http_status"], scripted=True,
    )
    controls = visible_form_controls(page) if not snap["tls_error"] else []
    ids = {control.get("id") for control in controls}
    login_form_visible = (
        "MainContent_txtUserAccount" in ids
        and "MainContent_txtPassword" in ids
        and "MainContent_btnSignIn" in ids
    )
    has_viewstate = any(control.get("name") == "__VIEWSTATE" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha", "amzn-captcha", "awswaf"),
    )
    if challenge_widget or _body_blocked(body) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Kansas SOS eForms portal showed an access-control challenge or refused headless TLS; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif login_form_visible and has_viewstate:
        result["status"] = "login_required"
        result["summary"] = (
            "Kansas SOS eForms (launched January 2024) requires a pre-registered individual user account; "
            "the legacy KBOS startupwizard at appengine.egov.com is decommissioned. No anonymous LLC "
            "formation wizard is exposed; Business Entity Search endpoint is fronted by an Amazon WAF challenge."
        )
    result["signals"]["login_form_visible"] = login_form_visible
    result["signals"]["asp_net_viewstate_present"] = has_viewstate
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ks_eforms_account_email", "required": True},
        {"field": "ks_eforms_account_password_or_session", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "resident_agent_name_and_ks_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "tax_closing_month", "required": True},
        {"field": "naics_code", "required": True},
        {"field": "organizer_name_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Kansas eForms login wall before entering credentials.",
    ]
    return result


def script_ky_fasttrack(page, url: str, state: str) -> dict[str, Any]:
    landing = "https://web.sos.ky.gov/fasttrack/default.aspx"
    snap = _safe_goto(page, landing, timeout=45_000, settle_ms=2500)
    if not snap["tls_error"]:
        sessioned_base = snap["final_url"].rsplit("/", 1)[0] + "/"
        new_business_snap = _safe_goto(page, sessioned_base + "NewBusiness.aspx", timeout=30_000, settle_ms=2000)
        if not new_business_snap["tls_error"]:
            snap = new_business_snap
    body = snap["body"]
    result = classify_review_signals(
        state=state, body=body, title=snap["title"], url=snap["final_url"],
        http_status=snap["http_status"], scripted=True,
    )
    controls = visible_form_controls(page) if not snap["tls_error"] else []
    ids = {control.get("id") for control in controls}
    jurisdiction_picker_visible = (
        "ctl00_MainContent_dombutt" in ids or "ctl00_MainContent_foregbutt" in ids
    )
    has_viewstate = any(control.get("name") == "__VIEWSTATE" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _body_blocked(body) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Kentucky FastTrack portal showed an access-control challenge or refused headless TLS; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif jurisdiction_picker_visible and has_viewstate:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "Kentucky FastTrack is a publicly reachable ASP.NET WebForms portal with no login or CAPTCHA. "
            "Formation wizard begins at the domestic/foreign jurisdiction radio; applicant data (entity name, "
            "registered agent in KY, principal office, organizer signature) is required before payment."
        )
    result["signals"]["jurisdiction_picker_visible"] = jurisdiction_picker_visible
    result["signals"]["asp_net_viewstate_present"] = has_viewstate
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "domestic_or_foreign", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "registered_agent_name", "required": True},
        {"field": "registered_agent_ky_address", "required": True},
        {"field": "organizer_or_incorporator_signature", "required": True},
        {"field": "filer_contact_email", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at the Kentucky FastTrack jurisdiction picker before any data entry.",
    ]
    return result


def script_la_geauxbiz(page, url: str, state: str) -> dict[str, Any]:
    target = "https://geauxbiz.sos.la.gov"
    snap = _safe_goto(page, target, timeout=45_000, settle_ms=3000)
    body = snap["body"]
    result = classify_review_signals(
        state=state, body=body, title=snap["title"], url=snap["final_url"],
        http_status=snap["http_status"], scripted=True,
    )
    controls = visible_form_controls(page) if not snap["tls_error"] else []
    body_lower = body.lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    sso_visible = "ssologin" in snap["final_url"].lower() or "single sign" in body_lower
    if challenge_widget or _body_blocked(body) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Louisiana geauxBIZ portal showed an access-control challenge or refused headless TLS; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["sso_visible"] = sso_visible
    elif sso_visible:
        result["status"] = "login_required"
        result["summary"] = (
            "Louisiana geauxBIZ portal redirects to SSO login; provision a credentialed operator account "
            "before automation can advance to the formation wizard."
        )
        result["signals"]["sso_visible"] = sso_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "geauxbiz_account_email", "required": True},
        {"field": "geauxbiz_account_password_or_session", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_la_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Louisiana geauxBIZ SSO/reCAPTCHA gate before entering credentials.",
    ]
    return result


def script_ma_corpweb(page, url: str, state: str) -> dict[str, Any]:
    target = "https://corp.sec.state.ma.us/corpweb/loginsystem/ListNewFilings.aspx?FilingMethod=I"
    snap = _safe_goto(page, target, timeout=45_000, settle_ms=4000)
    body = snap["body"]
    content_lower = (snap["content"] or "").lower()
    result = classify_review_signals(
        state=state, body=body, title=snap["title"], url=snap["final_url"],
        http_status=snap["http_status"], scripted=True,
    )
    controls = visible_form_controls(page) if not snap["tls_error"] else []
    incapsula_present = (
        "incapsula" in content_lower
        or "_incapsula_resource" in content_lower
        or "incap_ses_" in content_lower
    )
    if incapsula_present or _body_blocked(body) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Massachusetts Corpweb is fronted by Imperva/Incapsula; the listing URL serves a JS-challenge "
            "interstitial and the external-login URL 403s for headless sessions. Run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["incapsula_present"] = incapsula_present
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ma_corpweb_customer_id", "required": True},
        {"field": "ma_corpweb_password_or_session", "required": True},
        {"field": "entity_name_with_designator", "required": True},
        {"field": "principal_office_street_address", "required": True},
        {"field": "ma_resident_agent_name_and_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Massachusetts Imperva/Incapsula interstitial before any portal HTML rendered.",
    ]
    return result


def script_md_business_express(page, url: str, state: str) -> dict[str, Any]:
    target = "https://egov.maryland.gov/businessexpress"
    snap = _safe_goto(page, target, timeout=45_000, settle_ms=2500)
    body = snap["body"]
    result = classify_review_signals(
        state=state, body=body, title=snap["title"], url=snap["final_url"],
        http_status=snap["http_status"], scripted=True,
    )
    controls = visible_form_controls(page) if not snap["tls_error"] else []
    has_username = any(control.get("name") == "UserName" or control.get("id") == "UserName" for control in controls)
    has_password = any(control.get("type") == "password" for control in controls)
    has_antiforgery = any(control.get("name") == "__RequestVerificationToken" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _body_blocked(body) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Maryland Business Express portal showed an access-control challenge or refused headless TLS; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_username and has_password and has_antiforgery:
        result["status"] = "login_required"
        result["summary"] = (
            "Maryland Business Express (Tyler Technologies ASP.NET MVC) requires an operator-provisioned "
            "Business Express account before any LLC/corp filing wizard, annual report, or trade-name flow "
            "is reachable. No CAPTCHA or bot-management challenge observed on the anonymous landing."
        )
    result["signals"]["login_form_visible"] = has_username and has_password
    result["signals"]["anti_forgery_token_present"] = has_antiforgery
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "md_business_express_username", "required": True},
        {"field": "md_business_express_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "resident_agent_name_and_md_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Maryland Business Express login wall before entering credentials.",
    ]
    return result


def script_me_aro(page, url: str, state: str) -> dict[str, Any]:
    target = "https://apps1.web.maine.gov/cgi-bin/online/aro/index.pl"
    snap = _safe_goto(page, target, timeout=45_000, settle_ms=2500)
    body = snap["body"]
    result = classify_review_signals(
        state=state, body=body, title=snap["title"], url=snap["final_url"],
        http_status=snap["http_status"], scripted=True,
    )
    controls = visible_form_controls(page) if not snap["tls_error"] else []
    has_csrf = any(control.get("name") == "informepagetoken" for control in controls)
    aro_visible = "annual report" in body.lower() and "single" in body.lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _body_blocked(body) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Maine ARO portal showed an access-control challenge or refused headless TLS; run from the "
            "verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_csrf and aro_visible:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "Maine Annual Report Online (apps1.web.maine.gov/cgi-bin/online/aro) is publicly reachable with "
            "an informepagetoken CSRF; non-subscriber 'File a Single Annual Report' flow requires charter "
            "number, officer/director list, principal office, and registered clerk/agent. NOTE: Maine SOS "
            "does not expose online formation; Articles of Incorporation/Organization are paper-only (MBCA-6/"
            "MLLC-6 mailed to 101 State House Station, Augusta ME 04333)."
        )
    result["signals"]["informepagetoken_present"] = has_csrf
    result["signals"]["aro_landing_visible"] = aro_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "entity_charter_number_or_name", "required": True},
        {"field": "filer_contact_name", "required": True},
        {"field": "filer_contact_email", "required": True},
        {"field": "officer_director_or_member_manager_list", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "registered_clerk_or_agent_name_and_address", "required": True},
        {"field": "formation_filing_mode", "required": True,
         "accepted_values": ["paper_only_MBCA6", "paper_only_MLLC6"],
         "note": "Maine has no online formation portal; formations route to paper packet workflow."},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Maine ARO non-subscriber entry; formation flow is paper-only.",
    ]
    return result


def script_mi_mibusiness_registry(page, url: str, state: str) -> dict[str, Any]:
    target = "https://mibusinessregistry.lara.state.mi.us"
    try:
        response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(4000)
        title, body, content = page_text_snapshot(page)
        http_status = response.status if response else None
    except Exception:
        title, body, content = "", "", ""
        http_status = None
    result = classify_review_signals(
        state=state, body=body, title=title, url=page.url,
        http_status=http_status, scripted=True,
    )
    controls = visible_form_controls(page)
    content_lower = (content or "").lower()
    body_lower = body.lower()
    turnstile_present = (
        "cf-turnstile" in content_lower
        or "challenges.cloudflare.com/turnstile" in content_lower
        or "__cf_chl_rt_tk" in content_lower
        or "verifying you are a human" in body_lower
    )
    if turnstile_present or _body_blocked(body) or http_status in {401, 403, 429}:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Michigan LARA migrated business filings to MiBusiness Registry, fronted by Cloudflare Turnstile; "
            "the legacy corpweb endpoint refuses headless TLS handshakes. Run from the verified Trusted "
            "Access operator browser profile (with Turnstile-capable provider) before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["turnstile_present"] = turnstile_present
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "milogin_account_email", "required": True},
        {"field": "milogin_account_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_mi_street_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_or_incorporator_name_and_address", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Michigan Cloudflare Turnstile challenge before any portal HTML rendered.",
    ]
    return result


def script_mn_mbls(page, url: str, state: str) -> dict[str, Any]:
    target = "https://mblsportal.sos.state.mn.us/Business"
    h2_block = False
    try:
        response = page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2500)
        title, body, _ = page_text_snapshot(page)
        http_status = response.status if response else None
    except Exception:
        title, body = "", ""
        http_status = None
        h2_block = True
    result = classify_review_signals(
        state=state, body=body, title=title,
        url=page.url if not h2_block else target,
        http_status=http_status, scripted=True,
    )
    if h2_block:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Minnesota MBLS portal refused headless TLS handshake (ERR_CONNECTION_RESET); "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["h2_fingerprint_block"] = True
        result["signals"]["required_controls"] = []
        result["applicant_data_requirements"] = [
            {"field": "mbls_account_email", "required": True},
            {"field": "mbls_account_password_or_session", "required": True},
        ]
        result["script_notes"] = [
            "No filing data was entered.",
            "No final submission or payment action is permitted by this script.",
            "Script stopped at MN MBLS TLS fingerprint block; trusted access required.",
        ]
        return result
    controls = visible_form_controls(page)
    body_lower = body.lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    login_required_visible = (
        "log in" in body_lower or "sign in" in body_lower or "username" in body_lower
    )
    if challenge_widget or _body_blocked(body) or (response and response.status in {401, 403, 429}):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Minnesota MBLS portal showed an access-control challenge; run from the verified Trusted "
            "Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif login_required_visible:
        result["status"] = "login_required"
        result["summary"] = (
            "Minnesota MBLS portal exposes a public name-availability search, but new-entity formation "
            "wizards sit behind an authenticated MBLS account. No CAPTCHA observed on the anonymous "
            "landing."
        )
    result["signals"]["login_required_visible"] = login_required_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "mbls_account_email", "required": True},
        {"field": "mbls_account_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_office_name_and_mn_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Minnesota MBLS login wall before entering credentials.",
    ]
    return result


def script_mo_bsd(page, url: str, state: str) -> dict[str, Any]:
    target = "https://bsd.sos.mo.gov/"
    snap = _safe_goto(page, target, timeout=45_000, settle_ms=2500)
    body = snap["body"]
    result = classify_review_signals(
        state=state, body=body, title=snap["title"], url=snap["final_url"],
        http_status=snap["http_status"], scripted=True,
    )
    controls = visible_form_controls(page) if not snap["tls_error"] else []
    has_login_id = any(
        "txtuser" in str(control.get("id", "")).lower() for control in controls
    )
    has_password = any(control.get("type") == "password" for control in controls)
    has_viewstate = any(control.get("name") == "__VIEWSTATE" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _body_blocked(body) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Missouri SOS bsd.sos.mo.gov portal showed an access-control challenge or refused headless TLS; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_login_id and has_password and has_viewstate:
        result["status"] = "login_required"
        result["summary"] = (
            "Missouri SOS bsd.sos.mo.gov (ASP.NET WebForms with DNN/Telerik, Cloudflare CDN passthrough) "
            "requires an operator-provisioned filer account before any LLC/corp formation wizard or annual "
            "registration is reachable. Cloudflare is in CDN-only mode with no Turnstile or JS challenge."
        )
    result["signals"]["login_form_visible"] = has_login_id and has_password
    result["signals"]["asp_net_viewstate_present"] = has_viewstate
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "mo_sos_portal_login_id", "required": True},
        {"field": "mo_sos_portal_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_mo_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_name_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Missouri bsd.sos.mo.gov login wall before entering credentials.",
    ]
    return result


def _classify_with_safe_goto(
    page,
    state: str,
    target: str,
    *,
    timeout: int = 45_000,
    settle_ms: int = 2500,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    snap = _safe_goto(page, target, timeout=timeout, settle_ms=settle_ms)
    body = snap["body"]
    result = classify_review_signals(
        state=state, body=body, title=snap["title"], url=snap["final_url"],
        http_status=snap["http_status"], scripted=True,
    )
    controls = visible_form_controls(page) if not snap["tls_error"] else []
    return snap, result, controls


def _edge_block_marks_trusted_access(snap: dict[str, Any], body: str, controls: list[dict[str, Any]]) -> bool:
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha", "amzn-captcha"),
    )
    return (
        challenge_widget
        or _body_blocked(body, snap.get("title", ""), snap.get("http_status"))
        or snap["http_status"] in {401, 403, 429}
        or snap["tls_error"]
    )


def script_ms_corp_portal(page, url: str, state: str) -> dict[str, Any]:
    target = "https://corp.sos.ms.gov/corp/portal/c/portal.aspx"
    snap, result, controls = _classify_with_safe_goto(page, state, target)
    if _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Mississippi SOS corp portal is gated by Akamai edge bot management (HTTP 403 with "
            "errors.edgesuite.net reference IDs) on headless sessions; run from the verified Trusted "
            "Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["edge_provider"] = "akamai_edgesuite"
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ms_corp_portal_account_email", "required": True},
        {"field": "ms_corp_portal_password_or_session", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_ms_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Mississippi Akamai edge block before the corp portal HTML rendered.",
    ]
    return result


def script_mt_biz_sosmt(page, url: str, state: str) -> dict[str, Any]:
    target = "https://biz.sosmt.gov"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _body_blocked(snap["body"]) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Montana biz.sosmt.gov auth route is gated by Cloudflare Turnstile; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif "login" in body_lower or "log in" in body_lower:
        result["status"] = "login_required"
        result["summary"] = (
            "Montana biz.sosmt.gov landing is a clean React SPA; all filing actions require account login, "
            "and /auth is gated by Cloudflare Turnstile. Provision a credentialed operator account out-of-band."
        )
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "mt_sos_account_username", "required": True},
        {"field": "mt_sos_account_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_mt_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Montana biz.sosmt.gov landing before any auth action.",
    ]
    return result


def script_nc_sosnc_online_filing(page, url: str, state: str) -> dict[str, Any]:
    target = "https://www.sosnc.gov/online_filing/filing/creation"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    content_lower = (snap["content"] or "").lower()
    body_lower = snap["body"].lower()
    cf_interstitial = (
        "just a moment" in body_lower
        or "cf-chl" in content_lower
        or "__cf_bm" in content_lower
        or "challenge-platform" in content_lower
    )
    if cf_interstitial or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "North Carolina SOS online filing creation URL serves a Cloudflare JS interstitial to headless "
            "sessions; run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["cloudflare_interstitial"] = cf_interstitial
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "nc_sosnc_account_email", "required": True},
        {"field": "nc_sosnc_password_or_session", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_nc_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at North Carolina Cloudflare interstitial before the SOSNC filing form rendered.",
    ]
    return result


def script_nd_firststop(page, url: str, state: str) -> dict[str, Any]:
    target = "https://firststop.sos.nd.gov/forms/new/523"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    wizard_visible = "step 1 of 4" in body_lower or "instructions" in body_lower
    login_visible = "login" in body_lower or "ndlogin" in body_lower
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _body_blocked(snap["body"]) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "North Dakota FirstStop portal showed an access-control challenge or refused headless TLS; "
            "run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif wizard_visible and login_visible:
        result["status"] = "login_required"
        result["summary"] = (
            "North Dakota FirstStop start-business wizard is a publicly reachable 4-step React SPA "
            "instructional flow, but the entity-formation form is gated behind NDLogin SSO. Provision an "
            "operator NDLogin account out-of-band before automation can advance to data entry."
        )
    result["signals"]["wizard_visible"] = wizard_visible
    result["signals"]["login_visible"] = login_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ndlogin_account_email", "required": True},
        {"field": "ndlogin_account_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_nd_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at North Dakota FirstStop wizard step 1 before NDLogin redirect.",
    ]
    return result


def script_ne_corp_filing(page, url: str, state: str) -> dict[str, Any]:
    target = "https://www.nebraska.gov/apps-sos-edocs"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=2500)
    body_lower = snap["body"].lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    login_visible = "log in" in body_lower or "sign in" in body_lower
    if challenge_widget or _body_blocked(snap["body"]) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Nebraska SOS eDocs/corp filing portal showed an access-control challenge; run from the "
            "verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif login_visible:
        result["status"] = "login_required"
        result["summary"] = (
            "Nebraska has no self-service formation wizard; apps-sos-edocs is a document-upload portal "
            "and corp_filing/index.cgi handles biennial reports only. Both gates require a subscriber "
            "or document-upload account. Formation routes to a document-upload-assist workflow."
        )
    result["signals"]["login_visible"] = login_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ne_subscriber_account_email", "required": True},
        {"field": "ne_subscriber_password_or_session", "required": True},
        {"field": "filing_workflow", "required": True,
         "accepted_values": ["document_upload_assist"],
         "note": "Nebraska routes formation via document upload; no online wizard exists."},
        {"field": "prepared_articles_pdf", "required": True},
        {"field": "filer_contact_name", "required": True},
        {"field": "filer_contact_email", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No documents were uploaded.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Nebraska eDocs subscriber gate before any upload action.",
    ]
    return result


def script_nh_quickstart(page, url: str, state: str) -> dict[str, Any]:
    target = "https://quickstart.sos.nh.gov/online/Account/LoginPage?LoginType=CreateNewBusiness"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    akamai_block = (
        snap["http_status"] in {401, 403, 429}
        and ("access denied" in body_lower or "errors.edgesuite.net" in body_lower or "reference #" in body_lower)
    )
    if akamai_block or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "New Hampshire QuickStart portal is fronted by Akamai Bot Manager returning HTTP 403 'Access "
            "Denied' to headless sessions on every QuickStart path; run from the verified Trusted Access "
            "operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["akamai_block"] = akamai_block
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "nh_quickstart_account_email", "required": True},
        {"field": "nh_quickstart_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_nh_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at New Hampshire Akamai edge block before any QuickStart HTML rendered.",
    ]
    return result


def script_nj_business_formation(page, url: str, state: str) -> dict[str, Any]:
    target = "https://www.njportal.com/DOR/BusinessFormation/CompanyInformation/BusinessName"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=2500)
    has_business_type = any(control.get("name") == "BusinessType" for control in controls)
    has_business_name = any(control.get("name") == "BusinessName" or control.get("id") == "BusinessName" for control in controls)
    has_antiforgery = any(control.get("name") == "__RequestVerificationToken" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _body_blocked(snap["body"]) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "New Jersey Business Formation portal showed an access-control challenge or refused headless "
            "TLS; run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_business_type and has_business_name and has_antiforgery:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "New Jersey Business Formation portal exposes an anonymous multi-step ASP.NET MVC wizard with "
            "no CAPTCHA. Step 1 (Business Name + BusinessType) is reachable without login; applicant data "
            "(entity name, registered agent, NJ principal office, organizer signature) is required before "
            "reaching the $125 Payment step."
        )
    result["signals"]["business_type_visible"] = has_business_type
    result["signals"]["antiforgery_token_present"] = has_antiforgery
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "entity_type", "required": True,
         "accepted_values": ["LLC", "DP", "PA", "LP", "LLP", "NP", "FR", "FLC", "LF", "FLP", "NF", "NV"]},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_nj_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "purpose_statement", "required": True},
        {"field": "organizer_signature", "required": True},
        {"field": "filer_contact_email", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at New Jersey Business Formation step 1 before any data entry.",
    ]
    return result


def script_nm_enterprise(page, url: str, state: str) -> dict[str, Any]:
    target = "https://enterprise.sos.nm.gov/forms/business"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    forms_catalog_visible = "articles of organization" in body_lower and "log in" in body_lower
    if challenge_widget or _body_blocked(snap["body"]) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "New Mexico Enterprise SOS portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif forms_catalog_visible:
        result["status"] = "login_required"
        result["summary"] = (
            "New Mexico Enterprise SOS is a React SPA listing all entity-formation forms (Articles of "
            "Organization, etc.); each form requires a self-created account before the wizard opens. No "
            "CAPTCHA observed on public surfaces; a WAF AppSetting indicates an upstream WAF that may "
            "activate at higher volume."
        )
    result["signals"]["forms_catalog_visible"] = forms_catalog_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "nm_enterprise_account_email", "required": True},
        {"field": "nm_enterprise_password_or_session", "required": True,
         "note": "Prior-system credentials did not transfer; new account creation required."},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_nm_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at New Mexico Enterprise SOS forms catalog before login redirect.",
    ]
    return result


def script_nv_silverflume(page, url: str, state: str) -> dict[str, Any]:
    target = "https://www.nvsilverflume.gov/home"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3500)
    content_lower = (snap["content"] or "").lower()
    body_lower = snap["body"].lower()
    incapsula_or_hcaptcha = (
        "incapsula" in content_lower
        or "_incapsula_resource" in content_lower
        or "hcaptcha" in content_lower
        or "h-captcha" in content_lower
    )
    if incapsula_or_hcaptcha or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Nevada SilverFlume portal is fronted by Imperva/Incapsula plus an hCaptcha challenge "
            "(sitekey dd6e16a7-972e-47d2-93d0-96642fb6d8de); run from the verified Trusted Access "
            "operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["incapsula_or_hcaptcha"] = incapsula_or_hcaptcha
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "nv_silverflume_account_email", "required": True},
        {"field": "nv_silverflume_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_nv_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Nevada SilverFlume Imperva/hCaptcha gate before any portal HTML rendered.",
    ]
    return result


def script_ny_business_express(page, url: str, state: str) -> dict[str, Any]:
    target = "https://www.businessexpress.ny.gov/app/loginregister?p_next_page=/app/dashboard/recentActivity"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    sso_handoff_visible = "login.ny.gov" in body_lower or "ny.gov id" in body_lower or "my.ny.gov" in body_lower
    if challenge_widget or _body_blocked(snap["body"]) or snap["http_status"] in {401, 403, 429} or snap["tls_error"]:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "New York Business Express portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif sso_handoff_visible:
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "New York Business Express is a checklist/wizard front-end; entity formation requires "
            "NY.GOV ID via login.ny.gov (Okta SAML SSO) plus my.ny.gov SelfRegV3 account creation, then "
            "hand-off to NY Department of State Division of Corporations for the actual LLC/Corp filing. "
            "Operator-assisted lane until SSO + cross-portal automation is built."
        )
        result["signals"]["sso_handoff_visible"] = sso_handoff_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ny_gov_id_account_email", "required": True},
        {"field": "ny_gov_id_password_or_session", "required": True},
        {"field": "my_ny_gov_business_profile", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_ny_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at New York Business Express login redirect before NY.GOV ID SSO.",
    ]
    return result


def script_oh_obc(page, url: str, state: str) -> dict[str, Any]:
    target = "https://www.ohiobusinesscentral.gov"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    title_lower = (snap.get("title") or "").lower()
    challenge_widget = _values_contain(
        controls, ("cf-turnstile", "turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    turnstile_interstitial = "just a moment" in title_lower or "performing security verification" in body_lower
    viewstate_present = any(control.get("name") == "__VIEWSTATE" for control in controls)
    profile_gate_visible = "create profile" in body_lower or "log in" in body_lower
    if challenge_widget or turnstile_interstitial or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Ohio Business Central was fronted by a Cloudflare Turnstile challenge; run from the verified "
            "Trusted Access operator browser profile and always enter via www.ohiobusinesscentral.gov "
            "(direct hits to bsportal.ohiosos.gov trigger the interstitial)."
        )
        result["signals"]["challenge"] = True
    elif viewstate_present and profile_gate_visible:
        result["status"] = "login_required"
        result["summary"] = (
            "Ohio Business Central (ASP.NET WebForms) requires a self-created OBC profile before any "
            "filing wizard opens. Cloudflare Turnstile activates only on direct bsportal.ohiosos.gov hits."
        )
    result["signals"]["viewstate_present"] = viewstate_present
    result["signals"]["profile_gate_visible"] = profile_gate_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "oh_obc_profile_email", "required": True},
        {"field": "oh_obc_profile_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "statutory_agent_name_and_oh_address", "required": True,
         "note": "Ohio terminology is 'statutory agent', not 'registered agent'."},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_or_incorporator_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Ohio Business Central profile login gate.",
    ]
    return result


def script_or_cbr_manager(page, url: str, state: str) -> dict[str, Any]:
    target = "https://secure.sos.state.or.us/cbrmanager/index.action"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3500)
    body_lower = snap["body"].lower()
    content_lower = (snap["content"] or "").lower()
    f5_js_challenge = (
        "please enable javascript" in body_lower
        or "your support id is" in body_lower
        or "support id:" in body_lower
        or "the requested url was rejected" in body_lower
        or "bobcmn" in content_lower
        or "big-ip" in content_lower
        or "/tsbin/" in content_lower
        or "ts_callback" in content_lower
        or "f5_cspm" in content_lower
        or (snap["title"] == "" and snap["http_status"] == 200 and not snap["body"].strip())
    )
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or f5_js_challenge or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Oregon CBR Manager (Apache Struts2) is gated by an F5 BIG-IP ASM JavaScript challenge; "
            "headless plain-HTTP clients cannot pass. Run from the verified Trusted Access operator "
            "browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["f5_js_challenge"] = f5_js_challenge
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "obr_account_email", "required": True},
        {"field": "obr_account_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_or_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Oregon CBR Manager F5 JavaScript challenge before any portal HTML rendered.",
    ]
    return result


def script_pa_file_dos(page, url: str, state: str) -> dict[str, Any]:
    target = "https://file.dos.pa.gov/"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=4000)
    body_lower = snap["body"].lower()
    title_lower = (snap.get("title") or "").lower()
    turnstile_present = (
        "cf-turnstile-response" in (snap["content"] or "").lower()
        or "challenges.cloudflare.com/turnstile" in (snap["content"] or "").lower()
        or "just a moment" in title_lower
    )
    if turnstile_present or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Pennsylvania file.dos.pa.gov is fronted by an active Cloudflare Turnstile interstitial "
            "(HTTP 403 with cf-turnstile-response widget and Ray ID); run from the verified Trusted "
            "Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["turnstile_present"] = turnstile_present
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "operator_browser_profile", "required": True},
        {"field": "pa_filer_keystone_login", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "pa_registered_office_or_crop", "required": True,
         "note": "PA disallows PO box for registered office; commercial registered office provider acceptable."},
        {"field": "organizer_signature", "required": True},
        {"field": "docketing_statement_DSCB_15_134A", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Pennsylvania Cloudflare Turnstile interstitial before any DOS HTML rendered.",
    ]
    return result


def script_ri_business_sos(page, url: str, state: str) -> dict[str, Any]:
    target = "https://business.sos.ri.gov/corp/LoginSystem/login_form.asp"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=2500)
    has_cid = any(control.get("name") == "CID" for control in controls)
    has_pin = any(control.get("name") == "PIN" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Rhode Island Business SOS portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_cid and has_pin:
        result["status"] = "login_required"
        result["summary"] = (
            "Rhode Island Business SOS portal (classic IIS/ASP) requires pre-issued Customer ID (CID) + "
            "PIN credentials from the Division of Business Services (corp_pin@sos.ri.gov). No self-service "
            "account creation. No CAPTCHA on the login form; Cloudflare email-protection is the only "
            "passive CDN feature detected."
        )
    result["signals"]["cid_pin_login_visible"] = has_cid and has_pin
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ri_sos_cid", "required": True,
         "note": "6-char alphanumeric Customer ID issued out-of-band by RI SOS Business Services."},
        {"field": "ri_sos_pin", "required": True,
         "note": "4-digit numeric PIN paired with the CID."},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_ri_street_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Rhode Island login form before submitting CID/PIN.",
    ]
    return result


def script_sc_business_filings(page, url: str, state: str) -> dict[str, Any]:
    target = "https://businessfilings.sc.gov/BusinessFiling/Account/Login"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=2500)
    has_username = any(control.get("name") == "Username" or control.get("id") == "Username" for control in controls)
    has_password = any(control.get("type") == "password" for control in controls)
    has_antiforgery = any(control.get("name") == "__RequestVerificationToken" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "South Carolina Business Filings portal showed an access-control challenge; run from the "
            "verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_username and has_password and has_antiforgery:
        result["status"] = "login_required"
        result["summary"] = (
            "South Carolina Business Filings portal (ASP.NET MVC) requires a paid SC.gov / NIC Interactive "
            "subscriber account; phone-gated onboarding at 866.340.7105 x100. Register and Entity Search "
            "endpoints carry Google reCAPTCHA v2 (sitekey 6Leb4xEUAAAAABb-cJNQHgSXe100c1ch58rsqKJh)."
        )
    result["signals"]["login_form_visible"] = has_username and has_password
    result["signals"]["anti_forgery_token_present"] = has_antiforgery
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "sc_gov_subscriber_username", "required": True},
        {"field": "sc_gov_subscriber_password_or_session", "required": True},
        {"field": "sc_gov_subscriber_account_id", "required": True,
         "note": "NIC billing account; onboarding via 866.340.7105 x100."},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_sc_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at South Carolina Business Filings login wall before submitting credentials.",
    ]
    return result


def script_sd_sosenterprise(page, url: str, state: str) -> dict[str, Any]:
    target = "https://sosenterprise.sd.gov/BusinessServices/Business/RegistrationInstr.aspx"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    has_viewstate = any(control.get("name") == "__VIEWSTATE" for control in controls)
    on_registration_url = "registrationinstr.aspx" in snap["final_url"].lower()
    registration_link_visible = any(
        str(control.get("id", "")).lower() == "regstartnow"
        or "start a new business" in str(control.get("text", "")).lower()
        or "register a new business" in str(control.get("text", "")).lower()
        for control in controls
    )
    registration_visible = (
        on_registration_url
        or registration_link_visible
        or "start a new business" in body_lower
        or "register a new business" in body_lower
        or "business registration" in body_lower
    )
    recaptcha_loaded = "recaptcha/api.js" in (snap["content"] or "").lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "South Dakota SOS Enterprise portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_viewstate and registration_visible:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "South Dakota SOS Enterprise portal exposes a public 'Start a New Business' ASP.NET WebForms "
            "wizard without login; entity name, registered agent SD address, organizer, and payment "
            "method are required. Google reCAPTCHA api.js is loaded site-wide and likely activates "
            "deeper in the wizard (name-check or confirmation step)."
        )
    result["signals"]["viewstate_present"] = has_viewstate
    result["signals"]["registration_visible"] = registration_visible
    result["signals"]["recaptcha_loaded"] = recaptcha_loaded
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "registered_agent_name_and_sd_street_address", "required": True},
        {"field": "organizer_name_and_address", "required": True},
        {"field": "management_structure", "required": True,
         "accepted_values": ["member_managed", "manager_managed"]},
        {"field": "duration", "required": True},
        {"field": "filer_contact_email", "required": True},
        {"field": "filing_fee_payment_method", "required": True,
         "accepted_values": ["visa", "mastercard", "discover", "amex", "print_and_mail_paper"]},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at South Dakota new-business wizard entry; no anonymous data entry attempted.",
    ]
    return result


def script_tn_tnbear(page, url: str, state: str) -> dict[str, Any]:
    target = "https://tncab.tnsos.gov/"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=2500)
    has_username = any(str(control.get("name", "")).lower() in {"username"} for control in controls)
    has_password = any(control.get("type") == "password" for control in controls)
    has_antiforgery = any(control.get("name") == "__RequestVerificationToken" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Tennessee TNCaB portal showed an access-control challenge; run from the verified Trusted "
            "Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_username and has_password and has_antiforgery:
        result["status"] = "login_required"
        result["summary"] = (
            "Tennessee migrated business filings from tnbear.tn.gov (legacy, now read-only/search) to "
            "TNCaB at tncab.tnsos.gov. TNCaB requires email-based account login (verification email with "
            "24h expiry for new accounts). Drafts persist 14 days; rejected filings resubmittable 14 days. "
            "No CAPTCHA on login form."
        )
    result["signals"]["login_form_visible"] = has_username and has_password
    result["signals"]["anti_forgery_token_present"] = has_antiforgery
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "tncab_account_email", "required": True},
        {"field": "tncab_account_password_or_session", "required": True},
        {"field": "tn_entity_control_number_or_name", "required": True},
        {"field": "naics_code", "required": True},
        {"field": "signer_name_and_title", "required": True},
        {"field": "attestation_checkbox_acknowledgement", "required": True},
        {"field": "payment_method", "required": True,
         "accepted_values": ["pay_now_credit_card", "mail_in_check_voucher"]},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission, payment, or voucher generation is permitted by this script.",
        "Script stopped at TNCaB login form before any credential submission.",
    ]
    return result


def script_ut_business_registration(page, url: str, state: str) -> dict[str, Any]:
    target = "https://businessregistration.utah.gov"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    utahid_visible = "utahid" in body_lower or "id.utah.gov" in (snap["content"] or "").lower()
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Utah Business Registration portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif utahid_visible or "log in" in body_lower:
        result["status"] = "login_required"
        result["summary"] = (
            "Utah Business Registration (commerce.utah.gov/businessregistration) federates login through "
            "UtahID OIDC SSO at id.utah.gov; anonymous utilities (entity search, name availability, "
            "fictitious name) are reachable without login. All filing actions require a UtahID account."
        )
    result["signals"]["utahid_visible"] = utahid_visible
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "utahid_account_email", "required": True},
        {"field": "utahid_account_password_or_session", "required": True},
        {"field": "entity_type", "required": True,
         "accepted_values": ["LLC", "C_CORP", "LP", "LLP", "NONPROFIT", "PROFESSIONAL_LLC", "BENEFIT_LLC", "SERIES_LLC", "DAO"]},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_ut_address", "required": True},
        {"field": "registered_agent_consent_signature", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Utah Business Registration UtahID login gate.",
    ]
    return result


def script_va_cis_scc(page, url: str, state: str) -> dict[str, Any]:
    target = "https://cis.scc.virginia.gov/Account/Login"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    has_username = any(str(control.get("id", "")).lower() == "txtusername" for control in controls)
    has_password = any(control.get("type") == "password" for control in controls)
    has_antiforgery = any(control.get("name") == "__RequestVerificationToken" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Virginia CIS SCC portal showed an access-control challenge; run from the verified Trusted "
            "Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_username and has_password and has_antiforgery:
        result["status"] = "login_required"
        result["summary"] = (
            "Virginia CIS SCC portal (clean ASP.NET MVC, plain IIS) requires a CIS account created at "
            "scc.virginia.gov/businesses/cis-help/cis-create-an-account. No CAPTCHA on the login submit; "
            "reCAPTCHA v3 (action='submit') only gates anonymous Entity/UCC Search. Cookie consent gate "
            "is a one-shot POST /Cookie/StoreCookieConsent."
        )
    result["signals"]["login_form_visible"] = has_username and has_password
    result["signals"]["anti_forgery_token_present"] = has_antiforgery
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "cis_username", "required": True},
        {"field": "cis_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_va_office_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_or_incorporator_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Virginia CIS login form before submitting credentials.",
    ]
    return result


def script_vt_bizfilings(page, url: str, state: str) -> dict[str, Any]:
    target = "https://bizfilings.vermont.gov/online"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    has_email_field = any(
        str(control.get("name", "")).lower() == "userid"
        or str(control.get("id", "")).lower() == "user-label"
        for control in controls
    )
    content_lower = (snap["content"] or "").lower()
    incapsula_interstitial = (
        "_incapsula_resource" in content_lower and (
            "incapsula incident" in body_lower
            or snap["http_status"] in {401, 403, 429}
        )
    )
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or incapsula_interstitial or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Vermont bizfilings portal showed an access-control challenge; run from the verified Trusted "
            "Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["incapsula_interstitial"] = incapsula_interstitial
    elif has_email_field or "login" in body_lower or "register here" in body_lower:
        result["status"] = "login_required"
        result["summary"] = (
            "Vermont bizfilings (Angular Material SPA) is reachable through a passive Imperva WAF "
            "telemetry script (no active challenge). All filing actions are gated behind an email-first "
            "login (userId field → Continue → password/OTP). Account creation via 'Register Here'."
        )
    result["signals"]["email_field_visible"] = has_email_field
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "vt_bizfilings_account_email", "required": True},
        {"field": "vt_bizfilings_account_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_vt_street_address", "required": True},
        {"field": "registered_agent_email", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Vermont bizfilings email-first login gate.",
    ]
    return result


def script_wa_ccfs(page, url: str, state: str) -> dict[str, Any]:
    target = "https://ccfs.sos.wa.gov"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=3000)
    body_lower = snap["body"].lower()
    has_username = any(
        str(control.get("id", "")).lower() == "txtusername"
        or str(control.get("name", "")).lower() == "username"
        for control in controls
    )
    has_password = any(control.get("type") == "password" for control in controls)
    has_turnstile = any(
        "cf-turnstile-response" in str(control.get("name", "")).lower()
        or "cf-chl-widget" in str(control.get("id", "")).lower()
        for control in controls
    )
    challenge_widget = has_turnstile or _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Washington CCFS portal (AngularJS SPA) shows an active Cloudflare Turnstile widget bound to "
            "the Customer Login submit. Express Annual Report and public searches are exposed without "
            "login, but all non-express filings require both a CCFS user account and a passing Turnstile "
            "token. Run from the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
        result["signals"]["turnstile_widget_present"] = has_turnstile
    elif has_username and has_password:
        result["status"] = "login_required"
        result["summary"] = (
            "Washington CCFS Customer Login reachable without an active challenge; non-express filings "
            "require a pre-registered CCFS user account. Express Annual Report is the only anonymous "
            "filing path."
        )
    result["signals"]["login_form_visible"] = has_username and has_password
    result["signals"]["express_annual_report_visible"] = "express annual report" in body_lower
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "ccfs_username", "required": True},
        {"field": "ccfs_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "ubi_number_if_existing", "required": False},
        {"field": "registered_agent_name_and_wa_street_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "governors_or_member_managers", "required": True},
        {"field": "executor_or_organizer_signature", "required": True},
        {"field": "nature_of_business_description", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Washington CCFS Customer Login panel.",
    ]
    return result


def script_wv_onestop(page, url: str, state: str) -> dict[str, Any]:
    target = "https://onestop.wv.gov"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=2500)
    has_login = any(
        "tblogin" in str(control.get("id", "")).lower() for control in controls
    )
    has_password = any(control.get("type") == "password" for control in controls)
    has_viewstate = any(control.get("name") == "__VIEWSTATE" for control in controls)
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "West Virginia OneStop / Business4WV portal showed an access-control challenge; run from "
            "the verified Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_login and has_password and has_viewstate:
        result["status"] = "login_required"
        result["summary"] = (
            "West Virginia OneStop / Business4WV (ASP.NET WebForms) requires a self-service-created "
            "operator account. No CAPTCHA on the login form; standard ASP.NET anti-forgery only."
        )
    result["signals"]["login_form_visible"] = has_login and has_password
    result["signals"]["viewstate_present"] = has_viewstate
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "wv_onestop_user_id", "required": True},
        {"field": "wv_onestop_password_or_session", "required": True},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_wv_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at West Virginia OneStop login form.",
    ]
    return result


def script_wi_wdfi(page, url: str, state: str) -> dict[str, Any]:
    target = "https://apps.dfi.wi.gov/apps/CorpFormation/"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=2500)
    body_lower = snap["body"].lower()
    has_viewstate = any(control.get("name") == "__VIEWSTATE" for control in controls)
    entity_link_visible = any(
        "type=12" in str(control.get("name", "")).lower()
        or "type=12" in str(control.get("id", "")).lower()
        or "type=12" in str(control.get("text", "")).lower()
        or "articles of organization" in str(control.get("text", "")).lower()
        for control in controls
    )
    entity_chooser_visible = (
        entity_link_visible
        or "choose an entity type" in body_lower
        or ("limited liability company" in body_lower and "chapter 183" in body_lower)
        or "articles of organization" in body_lower
    )
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Wisconsin DFI CorpFormation portal showed an access-control challenge; run from the verified "
            "Trusted Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_viewstate and entity_chooser_visible:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "Wisconsin DFI CorpFormation (apps.dfi.wi.gov/apps/CorpFormation) exposes an anonymous "
            "ASP.NET WebForms entity-type chooser. Domestic LLC (Ch. 183, Form 502, $130) reachable "
            "without login; entity name, registered agent + email, principal office, organizer(s), and "
            "drafter required before the credit-card payment step."
        )
    result["signals"]["entity_chooser_visible"] = entity_chooser_visible
    result["signals"]["viewstate_present"] = has_viewstate
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "entity_type", "required": True,
         "accepted_values": ["DOMESTIC_LLC_CH183", "BUSINESS_CORP_CH180", "CLOSE_CORP_CH180", "NONSTOCK_CORP_CH181"]},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name", "required": True},
        {"field": "registered_agent_email", "required": True},
        {"field": "registered_office_wi_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_names_and_addresses", "required": True},
        {"field": "drafter_name", "required": True},
        {"field": "organizer_signatures", "required": True},
        {"field": "filer_contact_email_and_phone", "required": True},
        {"field": "payment_method", "required": True,
         "accepted_values": ["visa", "mastercard", "discover", "amex"]},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Wisconsin entity-type chooser before any data entry.",
    ]
    return result


def script_wy_wyobiz(page, url: str, state: str) -> dict[str, Any]:
    target = "https://wyobiz.wyo.gov/Business/RegistrationInstr.aspx"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=2500)
    body_lower = snap["body"].lower()
    has_viewstate = any(control.get("name") == "__VIEWSTATE" for control in controls)
    on_registration_url = "registrationinstr.aspx" in snap["final_url"].lower()
    formation_visible = (
        "instructions to form or register" in body_lower
        or ("articles" in body_lower and ("llc" in body_lower or "corporation" in body_lower))
    )
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "Wyoming wyobiz portal showed an access-control challenge; run from the verified Trusted "
            "Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif has_viewstate and formation_visible:
        result["status"] = "applicant_data_required"
        result["summary"] = (
            "Wyoming wyobiz portal exposes an anonymous ASP.NET WebForms formation wizard for domestic "
            "LLC ($100), Profit Corp ($100), Nonprofit ($50), and LP ($100). Entity name (not starting "
            "with 'A' — those route to paper), registered agent, principal office, organizers, and "
            "credit card (2.4% surcharge) required."
        )
    result["signals"]["on_registration_url"] = on_registration_url
    result["signals"]["formation_visible"] = formation_visible
    result["signals"]["viewstate_present"] = has_viewstate
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "entity_type", "required": True,
         "accepted_values": ["DOMESTIC_LLC", "PROFIT_CORP", "NONPROFIT_CORP", "LIMITED_PARTNERSHIP"]},
        {"field": "entity_name", "required": True,
         "note": "Names starting with 'A' require paper filing (manual review)."},
        {"field": "registered_agent_name_and_wy_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_names_and_addresses", "required": True},
        {"field": "purpose_statement", "required": True},
        {"field": "duration", "required": True},
        {"field": "filer_contact_email", "required": True},
        {"field": "payment_method", "required": True,
         "note": "2.4% credit card surcharge (minimum $1)."},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Wyoming registration instructions before the wizard postback.",
    ]
    return result


def script_dc_corponline(page, url: str, state: str) -> dict[str, Any]:
    target = "https://corponline.dlcp.dc.gov/Home.aspx/Landing"
    snap, result, controls = _classify_with_safe_goto(page, state, target, settle_ms=4000)
    final_url = snap["final_url"].lower()
    body_lower = snap["body"].lower()
    okta_redirect = "accessdc.dcra.dc.gov" in final_url or "oauth2/v1/authorize" in final_url
    has_identifier = any(
        str(control.get("name", "")).lower() == "identifier" for control in controls
    )
    challenge_widget = _values_contain(
        controls, ("turnstile", "g-recaptcha", "recaptcha-response", "h-captcha"),
    )
    if challenge_widget or _edge_block_marks_trusted_access(snap, snap["body"], controls):
        result["status"] = "trusted_access_required"
        result["summary"] = (
            "DC CorpOnline portal showed an access-control challenge; run from the verified Trusted "
            "Access operator browser profile before automation resumes."
        )
        result["signals"]["challenge"] = True
    elif okta_redirect or has_identifier or "access dc" in body_lower:
        result["status"] = "login_required"
        result["summary"] = (
            "DC CorpOnline (Angular SPA fronted by Cloudflare) immediately federates to Access DC via "
            "Okta OAuth2/OIDC + PKCE (client_id 0oaj2b95ksVHhlHyi4h7). No anonymous landing reachable; "
            "Okta sign-in form requires an Access DC account (MFA likely enforced)."
        )
    result["signals"]["okta_redirect"] = okta_redirect
    result["signals"]["okta_identifier_field_visible"] = has_identifier
    result["signals"]["required_controls"] = controls
    result["applicant_data_requirements"] = [
        {"field": "access_dc_account_email", "required": True},
        {"field": "access_dc_account_password_or_session", "required": True},
        {"field": "access_dc_mfa_token", "required": True,
         "note": "Okta MFA policy likely enforced."},
        {"field": "entity_type", "required": True},
        {"field": "entity_name", "required": True},
        {"field": "registered_agent_name_and_dc_address", "required": True},
        {"field": "principal_office_address", "required": True},
        {"field": "organizer_signature", "required": True},
    ]
    result["script_notes"] = [
        "No filing data was entered.",
        "No final submission or payment action is permitted by this script.",
        "Script stopped at Access DC Okta identifier (email) screen.",
    ]
    return result


STATE_SCRIPT_RUNNERS = {
    "AL": script_al_sos_online_services,
    "AK": script_ak_cbpl_create_entity,
    "AR": script_ar_sos_corpfilings,
    "AZ": script_az_ecorp,
    "CA": script_ca_bizfile,
    "CO": script_co_business_filing,
    "CT": script_ct_business_services,
    "DE": script_de_ecorp_document_upload,
    "FL": script_fl_sunbiz_efile,
    "GA": script_ga_ecorp,
    "HI": script_hi_bizex,
    "IA": script_ia_fast_track_filing,
    "ID": script_id_sosbiz,
    "IL": script_il_corporatellc,
    "IN": script_in_inbiz,
    "KS": script_ks_eforms,
    "KY": script_ky_fasttrack,
    "LA": script_la_geauxbiz,
    "MA": script_ma_corpweb,
    "MD": script_md_business_express,
    "ME": script_me_aro,
    "MI": script_mi_mibusiness_registry,
    "MN": script_mn_mbls,
    "MO": script_mo_bsd,
    "MS": script_ms_corp_portal,
    "MT": script_mt_biz_sosmt,
    "NC": script_nc_sosnc_online_filing,
    "ND": script_nd_firststop,
    "NE": script_ne_corp_filing,
    "NH": script_nh_quickstart,
    "NJ": script_nj_business_formation,
    "NM": script_nm_enterprise,
    "NV": script_nv_silverflume,
    "NY": script_ny_business_express,
    "OH": script_oh_obc,
    "OK": script_ok_sos_wizard,
    "OR": script_or_cbr_manager,
    "PA": script_pa_file_dos,
    "RI": script_ri_business_sos,
    "SC": script_sc_business_filings,
    "SD": script_sd_sosenterprise,
    "TN": script_tn_tnbear,
    "UT": script_ut_business_registration,
    "VA": script_va_cis_scc,
    "VT": script_vt_bizfilings,
    "WA": script_wa_ccfs,
    "WV": script_wv_onestop,
    "WI": script_wi_wdfi,
    "WY": script_wy_wyobiz,
    "DC": script_dc_corponline,
}


@dataclass
class ReviewCertification:
    state: str
    script_id: str
    status: str
    summary: str
    gate_result: dict[str, Any]
    portal_url: str = ""
    screenshot_path: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def target_states(states: list[str] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    manifests = all_state_adapter_manifests()
    wanted = {safe_state(state) for state in states or []}
    work_items = certification_worklist(manifests)["items"]
    targets = [
        item for item in work_items
        if item.get("next_gate", {}).get("code") in {"review_screen", "trusted_access_checkpoint", "customer_input_schema"}
    ]
    if wanted:
        targets = [item for item in targets if item.get("state") in wanted]
    if limit is not None:
        targets = targets[: max(0, int(limit))]
    return targets


def dry_run_certify(item: dict[str, Any]) -> ReviewCertification:
    state = safe_state(item["state"])
    script_id = SCRIPTED_STATES.get(state, "generic_discovery_only")
    if item.get("next_gate", {}).get("code") == "customer_input_schema":
        gate_result = {
            "status": "needs_repair",
            "label": "Required customer data mapped",
            "summary": "Regulatory records are missing or incomplete; repair extraction before review-screen automation.",
            "updated_at": utc_now(),
        }
    elif item.get("automation_lane") == "operator_assisted_browser_provider":
        gate_result = {
            "status": "trusted_access_required",
            "label": item.get("next_gate", {}).get("label", "Trusted Access checkpoint"),
            "summary": "Run from the verified Trusted Access operator browser profile before form automation resumes.",
            "updated_at": utc_now(),
        }
    else:
        gate_result = {
            "status": "needs_state_script" if script_id == "generic_discovery_only" else "pending_browser_certification",
            "label": item.get("next_gate", {}).get("label", "Review screen certification"),
            "summary": (
                "State-specific Playwright form script required to reach review screen without submitting."
                if script_id == "generic_discovery_only"
                else "State-specific script exists; run browser certification from the correct operator profile."
            ),
            "updated_at": utc_now(),
        }
    return ReviewCertification(
        state=state,
        script_id=script_id,
        status=gate_result["status"],
        summary=gate_result["summary"],
        gate_result=gate_result,
        portal_url=best_portal_url(item.get("portal_urls") or []),
    )


def browser_certify(item: dict[str, Any], *, headless: bool = True) -> ReviewCertification:
    state = safe_state(item["state"])
    script_id = SCRIPTED_STATES.get(state, "generic_discovery_only")
    url = best_portal_url(item.get("portal_urls") or [])
    if not url:
        result = classify_review_signals(state=state, body="", scripted=False)
        return ReviewCertification(state, script_id, result["status"], result["summary"], result)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("Python Playwright is required for browser review certification") from exc

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    screenshot = SCREENSHOT_DIR / f"{state.lower()}-review-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        runner = STATE_SCRIPT_RUNNERS.get(state)
        if runner:
            result = runner(page, url, state)
        else:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3000)
            title, body, _content = page_text_snapshot(page)
            result = classify_review_signals(
                state=state,
                body=body,
                title=title,
                url=page.url,
                http_status=response.status if response else None,
                scripted=script_id != "generic_discovery_only",
            )
        page.screenshot(path=str(screenshot), full_page=True)
        context.close()
        browser.close()
    result["label"] = item.get("next_gate", {}).get("label", "Review screen certification")
    result["updated_at"] = utc_now()
    return ReviewCertification(
        state=state,
        script_id=script_id,
        status=result["status"],
        summary=result["summary"],
        gate_result=result,
        portal_url=url,
        screenshot_path=str(screenshot.relative_to(BASE_DIR)),
    )


def run_certifications(
    *,
    states: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = True,
    write: bool = False,
    headless: bool = True,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in target_states(states, limit):
        cert = dry_run_certify(item) if dry_run else browser_certify(item, headless=headless)
        payload = cert.to_dict()
        results.append(payload)
        if write:
            gate_code = item.get("next_gate", {}).get("code") or "review_screen"
            write_gate_result(cert.state, gate_code, cert.gate_result)
            append_run(payload)
    if write:
        write_state_adapter_manifest()
    return {
        "dry_run": dry_run,
        "write": write,
        "started": len(results),
        "summary": {
            "by_status": {
                status: sum(1 for item in results if item["status"] == status)
                for status in sorted({item["status"] for item in results})
            }
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify state filing portals to review-screen gate.")
    parser.add_argument("command", choices=["status", "run"], nargs="?", default="status")
    parser.add_argument("--state", action="append", dest="states")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--browser", action="store_true", help="Use Playwright browser instead of dry-run planning.")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(certification_worklist(), indent=2, sort_keys=True))
        return 0
    result = run_certifications(
        states=args.states,
        limit=args.limit,
        dry_run=not args.browser,
        write=args.write,
        headless=not args.headed,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
