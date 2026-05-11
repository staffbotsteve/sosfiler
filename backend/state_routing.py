"""State filing route selection from SOSFiler research data."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from state_automation_profiles import adapter_manifest_for_state


EASY_LANE = "browser_automation_candidate"
MEDIUM_LANE = "browser_profile_automation"
HARD_LANE = "operator_assisted_browser_provider"
MANUAL_LANE = "operator_assisted"
STALE_TECHNICAL_BLOCKERS = {
    "hard_automation_state",
    "portal_account_required",
}


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return fallback


def deep_get(mapping: dict, *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def merge_filing_actions(*action_maps: dict) -> dict:
    """Deep-merge filing action maps, with later maps overriding earlier ones."""
    merged: dict[str, Any] = {}

    def merge_into(target: dict, source: dict) -> dict:
        for key, value in (source or {}).items():
            if key == "_meta":
                target[key] = deepcopy(value)
                continue
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge_into(target[key], value)
            else:
                target[key] = deepcopy(value)
        return target

    for action_map in action_maps:
        merge_into(merged, action_map or {})
    return merged


def difficulty_for_state(portal_research: dict, state: str) -> str:
    state = state.upper()
    difficulty = (portal_research.get("summary") or {}).get("automation_difficulty") or {}
    for label, payload in difficulty.items():
        if state in set(payload.get("states") or []):
            return label
    return "unknown"


def lane_for_route(action: dict | None, portal: dict | None, difficulty: str) -> str:
    automation_level = (action or {}).get("automation_level", "")
    if automation_level == "api_ready":
        return "official_api"
    if automation_level == "partner_api_ready":
        return "partner_api"
    if automation_level == "browser_automation_ready":
        if difficulty == "hard":
            return HARD_LANE
        if difficulty == "medium":
            return MEDIUM_LANE
        return "browser_automation"
    if automation_level == "operator_assisted":
        return MANUAL_LANE
    if automation_level == "manual_review":
        return HARD_LANE if difficulty == "hard" else MANUAL_LANE

    if difficulty == "easy":
        return EASY_LANE
    if difficulty == "medium":
        return MEDIUM_LANE
    if difficulty == "hard":
        return HARD_LANE
    if portal:
        return MANUAL_LANE
    return "research_required"


def blockers_for_route(action: dict | None, portal: dict | None, difficulty: str) -> list[dict]:
    blockers: list[dict] = []
    for blocker in (action or {}).get("pre_submission_blockers") or []:
        blockers.append({
            "code": blocker.get("code", "pre_submission_blocker"),
            "message": blocker.get("message", ""),
            "source": "filing_action",
        })
    if not portal:
        blockers.append({
            "code": "missing_portal_research",
            "message": "No portal research profile is available for this state.",
            "source": "state_router",
        })
        return blockers

    captcha = portal.get("captcha_type") or "unknown"
    if captcha not in {"", "none"}:
        blockers.append({
            "code": f"captcha_{captcha}",
            "message": f"Portal has {captcha} challenge; browser provider or operator intervention may be required.",
            "source": "portal_research",
        })
    waf = portal.get("waf_type") or ""
    if waf and waf != "none":
        blockers.append({
            "code": f"waf_{waf}",
            "message": f"Portal is protected by {waf}; persistent browser profile or operator fallback may be required.",
            "source": "portal_research",
        })
    if portal.get("account_required"):
        blockers.append({
            "code": "portal_account_required",
            "message": "A filer account is required before submission.",
            "source": "portal_research",
        })
    if difficulty == "hard":
        blockers.append({
            "code": "hard_automation_state",
            "message": "Research classifies this portal as hard automation; evidence-backed operator fallback remains enabled.",
            "source": "portal_research",
        })
    return blockers


def filter_stale_technical_blockers(blockers: list[dict], production_readiness: str) -> list[dict]:
    if production_readiness not in {"production_ready_operator_supervised", "certified_portal_entry_observed"}:
        return blockers
    filtered: list[dict] = []
    for blocker in blockers:
        code = str(blocker.get("code", ""))
        if code in STALE_TECHNICAL_BLOCKERS or code.startswith(("captcha_", "waf_")):
            continue
        filtered.append(blocker)
    return filtered


def customer_status_for_lane(lane: str, difficulty: str) -> str:
    if lane in {"official_api", "partner_api", "browser_automation"}:
        return "automation_ready"
    if lane in {EASY_LANE, MEDIUM_LANE}:
        return "automation_candidate"
    if lane == HARD_LANE:
        return "operator_verified_with_browser_assist"
    if lane == "research_required":
        return "operator_review_required"
    return "operator_verified"


def build_state_route(
    *,
    state: str,
    entity_type: str,
    action_type: str,
    filing_actions: dict,
    portal_research: dict,
    state_fees: dict,
) -> dict[str, Any]:
    state = state.upper()
    entity_type = "LLC" if entity_type.upper() == "LLC" else entity_type
    action = deep_get(filing_actions, state, entity_type, action_type)
    portal = (portal_research.get("states") or {}).get(state)
    difficulty = difficulty_for_state(portal_research, state)
    lane = lane_for_route(action, portal, difficulty)
    entity_fee_key = "LLC" if entity_type == "LLC" else "Corp"
    fee_record = deep_get(state_fees, entity_fee_key, state) or {}
    fallback_state_fee_cents = int(round(float(fee_record.get("filing_fee", 0)) * 100))
    action_fee_cents = int((action or {}).get("state_fee_cents") or 0)
    state_fee_cents = fallback_state_fee_cents if (action or {}).get("generated_from_regulatory") and fallback_state_fee_cents else action_fee_cents
    if not state_fee_cents:
        state_fee_cents = fallback_state_fee_cents
    portal_name = (action or {}).get("portal_name") or (portal or {}).get("portal_name") or ""
    portal_url = (action or {}).get("portal_url") or (portal or {}).get("portal_url") or ""
    form_name = (action or {}).get("form_name") or "Formation filing"
    blockers = blockers_for_route(action, portal, difficulty)
    automation_manifest = adapter_manifest_for_state(state)
    if automation_manifest.get("production_readiness") != "missing_profile":
        lane = automation_manifest["automation_lane"]
        difficulty = automation_manifest["automation_difficulty"]
        blockers = filter_stale_technical_blockers(blockers, automation_manifest.get("production_readiness", ""))
        matrix_blockers = automation_manifest.get("blockers") or []
        blocker_codes = {blocker.get("code") for blocker in blockers}
        for blocker in matrix_blockers:
            if blocker.get("code") not in blocker_codes:
                blockers.append(blocker)
        if not portal_url and automation_manifest.get("portal_urls"):
            portal_url = automation_manifest["portal_urls"][0]
        if not portal_name and automation_manifest.get("state_name"):
            portal_name = f"{automation_manifest['state_name']} business filing portal"
    route = {
        "state": state,
        "entity_type": entity_type,
        "action_type": action_type,
        "has_deep_action": bool(action),
        "automation_level": (action or {}).get("automation_level") or lane,
        "automation_lane": lane,
        "automation_difficulty": difficulty,
        "customer_status": automation_manifest.get("customer_status") or customer_status_for_lane(lane, difficulty),
        "adapter_key": f"{state.lower()}_{action_type}_{lane}",
        "filing_method": (action or {}).get("filing_method") or (portal or {}).get("filing_method") or "web_portal",
        "office": (action or {}).get("office") or f"{state} Secretary of State",
        "form_name": form_name,
        "form_number": (action or {}).get("form_number") or "",
        "portal_name": portal_name,
        "portal_url": portal_url,
        "state_fee_cents": state_fee_cents,
        "deep_action_state_fee_cents": action_fee_cents,
        "processing_fee": (action or {}).get("processing_fee") or {"type": "none"},
        "expected_processing_time": (action or {}).get("expected_processing_time") or "Varies by state and portal workload.",
        "required_consents": (action or {}).get("required_consents") or [
            "Customer authorization for SOSFiler to prepare and submit filing documents"
        ],
        "required_evidence": {
            "submitted": (action or {}).get("required_evidence_for_submitted") or [
                "Official portal receipt, confirmation, or submission evidence"
            ],
            "approved": (action or {}).get("required_evidence_for_approved") or [
                "Official approval, file-stamped document, certificate, or government correspondence"
            ],
        },
        "blockers": blockers,
        "portal_field_sequence": (action or {}).get("portal_field_sequence") or [],
        "required_customer_inputs": (action or {}).get("required_customer_inputs") or [],
        "evidence_outputs": (action or {}).get("evidence_outputs") or [],
        "portal_profile": portal or {},
        "state_automation_profile": automation_manifest,
        "certification_gates": automation_manifest.get("certification_gates", []),
        "source_urls": (action or {}).get("source_urls") or ([portal_url] if portal_url else []),
        "last_verified": (action or {}).get("last_verified") or (portal_research.get("metadata") or {}).get("generated_at", ""),
    }
    return route
