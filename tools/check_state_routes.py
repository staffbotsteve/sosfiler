#!/usr/bin/env python3
"""Check formation route coverage for every state in SOSFiler fee data."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"
BACKEND_DIR = APP_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from state_routing import build_state_route, merge_filing_actions  # noqa: E402


def load_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        if fallback is not None:
            return fallback
        raise


def load_inputs() -> tuple[dict, dict, dict]:
    state_fees = load_json(DATA_DIR / "state_fees.json")
    portal_research = load_json(DATA_DIR / "portal_maps" / "state_portals_research.json")
    generated_actions = load_json(DATA_DIR / "filing_actions.generated.json", {})
    manual_actions = load_json(DATA_DIR / "filing_actions.json", {})
    filing_actions = merge_filing_actions(generated_actions, manual_actions)
    return state_fees, portal_research, filing_actions


def check_routes(entity_type: str, action_type: str) -> dict:
    state_fees, portal_research, filing_actions = load_inputs()
    fee_entity = "LLC" if entity_type.upper() == "LLC" else "Corp"
    states = sorted(state_fees.get(fee_entity, {}).keys())
    routes = [
        build_state_route(
            state=state,
            entity_type=entity_type,
            action_type=action_type,
            filing_actions=filing_actions,
            portal_research=portal_research,
            state_fees=state_fees,
        )
        for state in states
    ]

    issues: list[dict] = []
    for route in routes:
        state = route["state"]
        if route["automation_difficulty"] not in {"easy", "medium", "hard"}:
            issues.append({"state": state, "code": "missing_portal_difficulty"})
        if not route["portal_url"]:
            issues.append({"state": state, "code": "missing_portal_url"})
        if route["state_fee_cents"] <= 0:
            issues.append({"state": state, "code": "missing_state_fee"})
        if route["automation_lane"] == "research_required":
            issues.append({"state": state, "code": "research_required"})
        if route["automation_difficulty"] == "hard":
            blocker_codes = {blocker.get("code") for blocker in route.get("blockers") or []}
            if not ({"hard_automation_state", "trusted_access_operator_checkpoint"} & blocker_codes):
                issues.append({"state": state, "code": "missing_hard_state_blocker"})

    by_lane = Counter(route["automation_lane"] for route in routes)
    by_difficulty = Counter(route["automation_difficulty"] for route in routes)
    deep_states = sorted(route["state"] for route in routes if route["has_deep_action"])
    fallback_states = sorted(route["state"] for route in routes if not route["has_deep_action"])
    return {
        "entity_type": entity_type,
        "action_type": action_type,
        "states_checked": len(routes),
        "deep_action_states": len(deep_states),
        "fallback_states": len(fallback_states),
        "by_lane": dict(sorted(by_lane.items())),
        "by_difficulty": dict(sorted(by_difficulty.items())),
        "states_with_deep_action": deep_states,
        "states_using_fee_portal_fallback": fallback_states,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check state route coverage.")
    parser.add_argument("--entity-type", default="LLC")
    parser.add_argument("--action-type", default="formation")
    parser.add_argument("--fail-on-issues", action="store_true")
    args = parser.parse_args()

    summary = check_routes(args.entity_type, args.action_type)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.fail_on_issues and summary["issues"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
