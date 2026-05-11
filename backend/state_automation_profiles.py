"""State automation profile helpers.

The regulatory matrix answers whether a state portal can be automated, needs a
trusted-access checkpoint, or is already proven. This module adapts that matrix
for order routing and filing adapters without coupling the app server to the
research package internals.
"""

from __future__ import annotations

import json
import argparse
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
MATRIX_PATH = BASE_DIR / "data" / "regulatory" / "state_automation_matrix.json"
ADAPTER_MANIFEST_PATH = BASE_DIR / "data" / "regulatory" / "state_adapter_manifest.json"
CERTIFICATION_GATES_PATH = BASE_DIR / "data" / "regulatory" / "state_certification_gates.json"

PLAYWRIGHT_LANE = "browser_profile_automation"
TRUSTED_ACCESS_LANE = "operator_assisted_browser_provider"
OPERATOR_LANE = "operator_assisted"
INCOMPLETE_GATE_STATUSES = {
    "pending",
    "needs_repair",
    "required",
    "needs_state_script",
    "applicant_data_required",
    "login_required",
    "trusted_access_required",
    "requires_registered_agent_intermediary",
    "blocked",
    "failed",
}

CANONICAL_STATE_PORTALS: dict[str, str] = {
    "AL": "https://www.alabamainteractive.org/sos/",
    "AK": "https://www.commerce.alaska.gov/web/cbpl/Corporations/CreateFileNewEntity",
    "AZ": "https://ecorp.azcc.gov/",
    "AR": "https://www.ark.org/sos/corpfilings/index.php",
    "CA": "https://bizfileonline.sos.ca.gov/",
    "CO": "https://www.sos.state.co.us/pubs/business/fileAForm.html",
    "CT": "https://business.ct.gov/",
    "DC": "https://corponline.dlcp.dc.gov/Home.aspx/Landing",
    "DE": "https://corp.delaware.gov/document-upload-service-information/",
    "FL": "https://dos.fl.gov/sunbiz/start-business/efile/",
    "GA": "https://ecorp.sos.ga.gov/",
    "HI": "https://hbe.ehawaii.gov/BizEx/home.eb",
    "IA": "https://filings.sos.iowa.gov/",
    "ID": "https://sosbiz.idaho.gov/",
    "IL": "https://apps.ilsos.gov/corporatellc/",
    "IN": "https://inbiz.in.gov/BOS/Home/Index",
    "KS": "https://appengine.egov.com/apps/ks/kbos/startupwizard",
    "KY": "https://web.sos.ky.gov/fasttrack/default.aspx",
    "LA": "https://geauxbiz.sos.la.gov/",
    "MA": "https://corp.sec.state.ma.us/corpweb/loginsystem/ListNewFilings.aspx?FilingMethod=I",
    "MD": "https://businessexpress.maryland.gov/",
    "ME": "https://www.maine.gov/sos/cec/corp/",
    "MI": "https://mibusinessregistry.lara.state.mi.us/",
    "MN": "https://mblsportal.sos.state.mn.us/Business",
    "MO": "https://bsd.sos.mo.gov/",
    "MS": "https://www.ms.gov/sos/onestopshop",
    "MT": "https://biz.sosmt.gov/auth",
    "NC": "https://www.sosnc.gov/online_filing/filing/creation",
    "ND": "https://firststop.sos.nd.gov/",
    "NE": "https://www.nebraska.gov/apps-sos-edocs/",
    "NH": "https://quickstart.sos.nh.gov/online/Account/LoginPage?LoginType=CreateNewBusiness",
    "NJ": "https://www.njportal.com/DOR/BusinessFormation/CompanyInformation/BusinessName",
    "NM": "https://enterprise.sos.nm.gov/",
    "NV": "https://www.nvsilverflume.gov/checklist",
    "NY": "https://businessexpress.ny.gov/",
    "OH": "https://www.ohiobusinesscentral.gov/",
    "OK": "https://www.sos.ok.gov/corp/filing.aspx",
    "OR": "https://secure.sos.state.or.us/cbrmanager/index.action#stay",
    "PA": "https://file.dos.pa.gov/",
    "RI": "https://business.sos.ri.gov/",
    "SC": "https://businessfilings.sc.gov/",
    "SD": "https://sosenterprise.sd.gov/BusinessServices/",
    "TN": "https://tnbear.tn.gov/Ecommerce/FilingSearch.aspx",
    "TX": "https://direct.sos.state.tx.us/acct/acct-login.asp",
    "UT": "https://businessregistration.utah.gov/",
    "VA": "https://cis.scc.virginia.gov/",
    "VT": "https://bizfilings.vermont.gov/online/",
    "WA": "https://ccfs.sos.wa.gov/",
    "WI": "https://www.wdfi.org/apps/CorpSearch/Search.aspx",
    "WV": "https://apps.sos.wv.gov/business/corporations/",
    "WY": "https://wyobiz.wyo.gov/Business/FilingSearch.aspx",
}


def load_state_automation_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"summary": {}, "profiles": []}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"summary": {}, "profiles": []}


def state_automation_profile(state: str, path: Path = MATRIX_PATH) -> dict[str, Any]:
    state = (state or "").upper()
    for profile in load_state_automation_matrix(path).get("profiles", []):
        if str(profile.get("state_code", "")).upper() == state:
            return profile
    return {}


def dedupe_urls(urls: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for url in urls:
        value = str(url or "").rstrip("/")
        if not value.startswith(("http://", "https://")) or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
    return cleaned


def execution_lane_from_profile(profile: dict[str, Any]) -> str:
    readiness = profile.get("production_readiness")
    lane = profile.get("automation_lane")
    if readiness in {"production_ready_operator_supervised", "certified_portal_entry_observed"}:
        return PLAYWRIGHT_LANE
    if readiness == "operator_assisted_required":
        return TRUSTED_ACCESS_LANE
    if lane == "playwright_persistent_browser":
        return PLAYWRIGHT_LANE
    if "operator" in str(lane):
        return TRUSTED_ACCESS_LANE
    return OPERATOR_LANE


def automation_difficulty_from_profile(profile: dict[str, Any]) -> str:
    readiness = profile.get("production_readiness")
    if readiness == "production_ready_operator_supervised":
        return "medium"
    if readiness == "certified_portal_entry_observed":
        return "medium"
    if readiness == "operator_assisted_required":
        return "hard"
    return "unknown"


def customer_status_from_profile(profile: dict[str, Any], gates: list[dict[str, Any]] | None = None) -> str:
    gate_statuses = {str(gate.get("status", "")) for gate in gates or []}
    if "trusted_access_required" in gate_statuses:
        return "trusted_access_operator_checkpoint_required"
    if "applicant_data_required" in gate_statuses:
        return "customer_input_required"
    readiness = profile.get("production_readiness")
    if readiness == "production_ready_operator_supervised":
        return "production_ready_operator_supervised"
    if readiness == "certified_portal_entry_observed":
        return "payment_screen_certification_required"
    if readiness == "operator_assisted_required":
        return "trusted_access_operator_checkpoint_required"
    return "operator_review_required"


def blockers_from_profile(profile: dict[str, Any], gates: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    readiness = profile.get("production_readiness")
    gate_statuses = {str(gate.get("status", "")) for gate in gates or []}
    blockers: list[dict[str, str]] = []
    if readiness == "operator_assisted_required":
        blockers.append({
            "code": "trusted_access_operator_checkpoint",
            "message": profile.get("blocker_summary")
            or "Official portal requires a trusted operator checkpoint before automation can resume.",
            "source": "state_automation_matrix",
        })
    if "trusted_access_required" in gate_statuses and not any(
        blocker.get("code") == "trusted_access_operator_checkpoint" for blocker in blockers
    ):
        blockers.append({
            "code": "trusted_access_operator_checkpoint",
            "message": "Review-screen certification found a portal security checkpoint; run from the verified Trusted Access operator profile.",
            "source": "state_certification_gates",
        })
    if "applicant_data_required" in gate_statuses:
        blockers.append({
            "code": "customer_input_required",
            "message": "Review-screen certification found required applicant data that must be collected before automation can continue.",
            "source": "state_certification_gates",
        })
    if blockers:
        return blockers
    if readiness not in {"production_ready_operator_supervised", "certified_portal_entry_observed"}:
        return [
            {
                "code": "automation_profile_not_certified",
                "message": "State automation profile is not certified for production routing.",
                "source": "state_automation_matrix",
            }
        ]
    return []


def load_certification_gate_overrides(path: Path = CERTIFICATION_GATES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "states": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"version": 1, "states": {}}


def gate_overrides_for_state(state: str) -> dict[str, Any]:
    states = load_certification_gate_overrides().get("states") or {}
    return states.get((state or "").upper(), {})


def apply_gate_overrides(state: str, gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overrides = gate_overrides_for_state(state)
    if not overrides:
        return gates
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gate in gates:
        code = str(gate.get("code", ""))
        seen.add(code)
        override = overrides.get(code) or {}
        merged.append({**gate, **override})
    for code, override in overrides.items():
        if code not in seen:
            merged.append({"code": code, **override})
    return merged


def certification_gates(profile: dict[str, Any]) -> list[dict[str, Any]]:
    readiness = profile.get("production_readiness")
    gates = [
        {
            "code": "portal_entry",
            "label": "Official portal entry observed",
            "status": "complete" if readiness in {
                "production_ready_operator_supervised",
                "certified_portal_entry_observed",
                "operator_assisted_required",
            } else "pending",
        },
        {
            "code": "customer_input_schema",
            "label": "Required customer data mapped",
            "status": "complete" if sum((profile.get("record_counts") or {}).values()) else "needs_repair",
        },
        {
            "code": "review_screen",
            "label": "Review screen certification",
            "status": "complete" if readiness == "production_ready_operator_supervised" else "pending",
        },
        {
            "code": "payment_screen",
            "label": "Payment screen certification",
            "status": "complete" if readiness == "production_ready_operator_supervised" else "pending",
        },
        {
            "code": "status_polling",
            "label": "Status polling method",
            "status": "complete" if profile.get("status_check_method") not in {"", "unknown"} else "pending",
        },
        {
            "code": "document_collection",
            "label": "Document collection method",
            "status": "complete" if profile.get("document_retrieval_method") not in {"", "unknown"} else "pending",
        },
    ]
    if readiness == "operator_assisted_required":
        gates.append({
            "code": "trusted_access_checkpoint",
            "label": "Trusted Access checkpoint",
            "status": "required",
        })
    return apply_gate_overrides(str(profile.get("state_code", "")), gates)


def adapter_manifest_for_state(state: str) -> dict[str, Any]:
    profile = state_automation_profile(state)
    if not profile:
        return {
            "state": (state or "").upper(),
            "automation_lane": OPERATOR_LANE,
            "customer_status": "operator_review_required",
            "production_readiness": "missing_profile",
            "certification_gates": [],
            "blockers": [{"code": "missing_state_automation_profile", "message": "No state automation profile exists."}],
        }
    gates = certification_gates(profile)
    gate_statuses = {str(gate.get("status", "")) for gate in gates}
    lane = execution_lane_from_profile(profile)
    if "trusted_access_required" in gate_statuses:
        lane = TRUSTED_ACCESS_LANE
    difficulty = automation_difficulty_from_profile(profile)
    if "trusted_access_required" in gate_statuses:
        difficulty = "hard"
    portal_urls = dedupe_urls([
        CANONICAL_STATE_PORTALS.get(str(profile.get("state_code", "")).upper(), ""),
        *(profile.get("portal_urls") or []),
    ])
    return {
        "state": profile.get("state_code", ""),
        "state_name": profile.get("state_name", ""),
        "automation_lane": lane,
        "automation_difficulty": difficulty,
        "customer_status": customer_status_from_profile(profile, gates),
        "production_readiness": profile.get("production_readiness", ""),
        "matrix_automation_lane": profile.get("automation_lane", ""),
        "portal_urls": portal_urls,
        "evidence_urls": dedupe_urls([*portal_urls[:1], *(profile.get("evidence_urls") or [])]),
        "certification_gates": gates,
        "blockers": blockers_from_profile(profile, gates),
        "record_counts": profile.get("record_counts") or {},
        "status_check_method": profile.get("status_check_method", "unknown"),
        "document_retrieval_method": profile.get("document_retrieval_method", "unknown"),
        "next_action": profile.get("next_action", ""),
    }


def all_state_adapter_manifests() -> list[dict[str, Any]]:
    profiles = load_state_automation_matrix().get("profiles", [])
    return [adapter_manifest_for_state(profile.get("state_code", "")) for profile in profiles]


def manifest_summary(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    lanes = Counter(item.get("automation_lane", "unknown") for item in manifests)
    readiness = Counter(item.get("production_readiness", "unknown") for item in manifests)
    gate_statuses: Counter[str] = Counter()
    for item in manifests:
        for gate in item.get("certification_gates", []):
            gate_statuses[f"{gate.get('code')}:{gate.get('status')}"] += 1
    return {
        "total_states": len(manifests),
        "by_automation_lane": dict(sorted(lanes.items())),
        "by_production_readiness": dict(sorted(readiness.items())),
        "by_gate_status": dict(sorted(gate_statuses.items())),
    }


def next_certification_gate(item: dict[str, Any]) -> dict[str, Any] | None:
    for gate in item.get("certification_gates", []):
        if gate.get("status") in INCOMPLETE_GATE_STATUSES:
            return gate
    return None


def certification_worklist(manifests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    manifests = manifests or all_state_adapter_manifests()
    items: list[dict[str, Any]] = []
    for item in manifests:
        gate = next_certification_gate(item)
        if not gate:
            continue
        items.append({
            "state": item.get("state"),
            "state_name": item.get("state_name"),
            "automation_lane": item.get("automation_lane"),
            "production_readiness": item.get("production_readiness"),
            "next_gate": gate,
            "portal_urls": item.get("portal_urls", []),
            "blockers": item.get("blockers", []),
        })
    by_gate = Counter(str(item["next_gate"].get("code", "unknown")) for item in items)
    by_lane = Counter(str(item.get("automation_lane", "unknown")) for item in items)
    return {
        "summary": {
            "states_remaining": len(items),
            "by_next_gate": dict(sorted(by_gate.items())),
            "by_automation_lane": dict(sorted(by_lane.items())),
        },
        "items": items,
    }


def write_state_adapter_manifest(path: Path = ADAPTER_MANIFEST_PATH) -> dict[str, Any]:
    manifests = all_state_adapter_manifests()
    payload = {
        "version": 1,
        "source": str(MATRIX_PATH.relative_to(BASE_DIR)),
        "purpose": "Execution-ready state adapter lanes and certification gates for SOSFiler filing automation.",
        "summary": manifest_summary(manifests),
        "states": manifests,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or inspect SOSFiler state adapter manifest.")
    parser.add_argument("command", choices=["status", "write", "worklist"], nargs="?", default="status")
    args = parser.parse_args()
    if args.command == "write":
        payload = write_state_adapter_manifest()
        print(json.dumps(payload["summary"], indent=2, sort_keys=True))
        return 0
    if args.command == "worklist":
        payload = certification_worklist()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(json.dumps(manifest_summary(all_state_adapter_manifests()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
