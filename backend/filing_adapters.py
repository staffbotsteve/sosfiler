"""Adapter registry for evidence-gated filing automation lanes."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Any

from execution_platform import FilingActionResult, FilingAdapter, OperatorAssistedAdapter
from state_automation_profiles import adapter_manifest_for_state


AUTOMATION_LANES = {
    "official_api",
    "partner_api",
    "browser_automation",
    "browser_automation_candidate",
    "browser_profile_automation",
    "operator_assisted_browser_provider",
    "operator_assisted",
}

PAYMENT_READY_STATUSES = {
    "payment_authorized",
    "payment_captured",
    "paid",
    "ready_to_file",
    "automation_started",
    "operator_required",
    "submitted",
    "submitted_to_state",
    "pending_government_review",
    "approved",
    "state_approved",
    "documents_collected",
    "complete",
}

TERMINAL_OR_SUBMITTED_STATUSES = {
    "submitted",
    "submitted_to_state",
    "pending_government_review",
    "approved",
    "state_approved",
    "documents_collected",
    "complete",
}


def job_route(job: dict[str, Any]) -> dict[str, Any]:
    route = job.get("route_metadata") or {}
    return route if isinstance(route, dict) else {}


def blockers(job: dict[str, Any]) -> list[dict[str, Any]]:
    value = job.get("portal_blockers") or []
    return value if isinstance(value, list) else []


def _present(value: Any) -> bool:
    return bool(str(value or "").strip())


def _has_artifact(job: dict[str, Any], *artifact_types: str) -> bool:
    wanted = set(artifact_types)
    return bool(wanted & set(job.get("artifact_types") or []))


def _has_document(job: dict[str, Any], *document_types: str) -> bool:
    wanted = set(document_types)
    return bool(wanted & set(job.get("document_types") or []))


def _route_steps(route: dict[str, Any]) -> list[dict[str, Any]]:
    steps = route.get("portal_field_sequence") or []
    return steps if isinstance(steps, list) else []


def build_adapter_contract(filing_job: dict[str, Any]) -> dict[str, Any]:
    """Expose the state/action route as a no-touch executable contract."""
    route = job_route(filing_job)
    state_manifest = route.get("state_automation_profile") or adapter_manifest_for_state(filing_job.get("state", ""))
    evidence = filing_job.get("required_evidence") or {}
    lane = filing_job.get("automation_lane") or route.get("automation_lane") or "operator_assisted"
    blockers_payload = blockers(filing_job)
    steps = _route_steps(route)
    required_inputs: list[str] = []
    evidence_outputs: list[str] = []
    for step in steps:
        for value in step.get("required_inputs") or step.get("fields") or []:
            if value and value not in required_inputs:
                required_inputs.append(str(value))
        for value in step.get("evidence_output") or []:
            if value and value not in evidence_outputs:
                evidence_outputs.append(str(value))
    for value in route.get("required_customer_inputs") or []:
        if value and value not in required_inputs:
            required_inputs.append(str(value))
    for value in route.get("evidence_outputs") or []:
        if value and value not in evidence_outputs:
            evidence_outputs.append(str(value))

    return {
        "contract_version": "state_action_v1",
        "state_adapter": filing_job.get("adapter_key") or route.get("adapter_key") or "",
        "lane": lane,
        "action_type": filing_job.get("action_type") or route.get("action_type") or "formation",
        "portal": {
            "name": filing_job.get("portal_name") or route.get("portal_name") or "",
            "url": filing_job.get("portal_url") or route.get("portal_url") or "",
            "method": filing_job.get("filing_method") or route.get("filing_method") or "web_portal",
        },
        "preflight_requirements": [
            "payment_ready",
            "not_already_submitted",
            "government_fee_quote",
            "business_identity",
            "evidence_contract",
        ],
        "required_customer_inputs": required_inputs,
        "portal_steps": steps,
        "submit_strategy": "dry_run_only" if lane != "official_api" else "official_api_disabled_until_credentials",
        "status_strategy": "partner_or_portal_status_check_with_operator_fallback",
        "document_strategy": "collect_official_evidence_before_customer_visible_state",
        "evidence_plan": {
            "submitted": evidence.get("submitted") or ["Official submission receipt or confirmation"],
            "approved": evidence.get("approved") or ["Official approval, certificate, or accepted filing record"],
            "outputs": evidence_outputs,
        },
        "state_automation_profile": state_manifest,
        "certification_gates": state_manifest.get("certification_gates", []),
        "operator_fallback": bool(blockers_payload) or lane in {"operator_assisted", "operator_assisted_browser_provider"},
        "blocker_codes": [blocker.get("code", "") for blocker in blockers_payload],
        "source_urls": route.get("source_urls", []),
        "last_verified": route.get("last_verified", ""),
    }


def add_check(checks: list[dict[str, Any]], code: str, label: str, passed: bool, severity: str, message: str) -> None:
    checks.append({
        "code": code,
        "label": label,
        "passed": passed,
        "severity": severity,
        "message": message,
    })


def validate_filing_preflight(filing_job: dict[str, Any]) -> dict[str, Any]:
    """Validate that a filing job is ready for automation or operator submission."""
    formation = filing_job.get("formation_data") or {}
    checks: list[dict[str, Any]] = []
    order_status = filing_job.get("order_status") or filing_job.get("status") or ""
    job_status = filing_job.get("status") or ""
    action_type = filing_job.get("action_type") or "formation"

    add_check(
        checks,
        "payment_ready",
        "Payment authorization",
        order_status in PAYMENT_READY_STATUSES,
        "blocking",
        "Payment must be paid or authorized before state submission.",
    )
    add_check(
        checks,
        "not_already_submitted",
        "Duplicate filing guard",
        job_status not in TERMINAL_OR_SUBMITTED_STATUSES,
        "blocking",
        "Job is already submitted or completed; do not start another submission without operator review.",
    )
    add_check(
        checks,
        "government_fee_quote",
        "Government fee quote",
        int(filing_job.get("total_government_cents") or filing_job.get("state_fee_cents") or 0) > 0,
        "blocking",
        "Government fee quote must be present before filing.",
    )
    if action_type == "annual_report":
        evidence = filing_job.get("required_evidence") or {}
        add_check(
            checks,
            "business_identity",
            "Business identity",
            all(_present(filing_job.get(key)) for key in ("business_name", "state", "entity_type")),
            "blocking",
            "Business name, state, and entity type are required.",
        )
        add_check(
            checks,
            "portal_route",
            "Official portal route",
            _present(filing_job.get("portal_url")),
            "blocking",
            "Annual report portal URL must be mapped before dry-run filing can proceed.",
        )
        add_check(
            checks,
            "annual_report_packet",
            "Operator packet",
            _has_artifact(filing_job, "annual_report_packet") or _has_document(filing_job, "annual_report_packet"),
            "warning",
            "Prepare the annual report packet before live operator work.",
        )
        add_check(
            checks,
            "evidence_contract",
            "Evidence contract",
            bool(evidence.get("submitted")) and bool(evidence.get("approved")),
            "blocking",
            "Required evidence for submitted and approved states must be defined.",
        )
        blocking = [check for check in checks if not check["passed"] and check["severity"] == "blocking"]
        warnings = [check for check in checks if not check["passed"] and check["severity"] == "warning"]
        return {
            "passed": not blocking,
            "checks": checks,
            "blocking_issues": blocking,
            "warnings": warnings,
        }

    add_check(
        checks,
        "business_identity",
        "Business identity",
        all(_present(formation.get(key) or filing_job.get(key)) for key in ("business_name", "state", "entity_type")),
        "blocking",
        "Business name, state, and entity type are required.",
    )
    add_check(
        checks,
        "principal_address",
        "Principal address",
        all(_present(formation.get(key)) for key in ("principal_address", "principal_city", "principal_state", "principal_zip")),
        "blocking",
        "Principal business street address, city, state, and ZIP are required.",
    )

    ra_choice = formation.get("ra_choice") or ""
    if ra_choice == "self":
        ra_ok = all(_present(formation.get(key)) for key in ("ra_name", "ra_address", "ra_city", "ra_state", "ra_zip"))
        ra_ok = ra_ok and str(formation.get("ra_state", "")).upper() == str(filing_job.get("state", "")).upper()
        ra_message = "Self registered agent must include name and in-state physical address."
    else:
        ra_ok = _has_artifact(filing_job, "registered_agent_consent", "registered_agent_assignment")
        ra_message = "Partner/SOSFiler registered agent assignment or consent evidence must be attached."
    add_check(checks, "registered_agent_ready", "Registered agent", ra_ok, "blocking", ra_message)

    members = formation.get("members") or []
    member_ok = bool(members) and all(
        _present(member.get("name"))
        and _present(member.get("address"))
        and _present(member.get("city"))
        and _present(member.get("state"))
        and _present(member.get("zip_code"))
        for member in members
    )
    add_check(checks, "ownership_roster", "Owner/officer roster", member_ok, "blocking", "At least one owner/officer with address is required.")

    authorization_ok = _has_document(filing_job, "filing_authorization") or _has_artifact(filing_job, "filing_authorization")
    add_check(
        checks,
        "filing_authorization",
        "Customer authorization",
        authorization_ok,
        "blocking",
        "Customer filing authorization document must be generated or attached.",
    )

    evidence = filing_job.get("required_evidence") or {}
    evidence_ok = bool(evidence.get("submitted")) and bool(evidence.get("approved"))
    add_check(
        checks,
        "evidence_contract",
        "Evidence contract",
        evidence_ok,
        "blocking",
        "Required evidence for submitted and approved states must be defined.",
    )

    blocking = [check for check in checks if not check["passed"] and check["severity"] == "blocking"]
    warnings = [check for check in checks if not check["passed"] and check["severity"] == "warning"]
    return {
        "passed": not blocking,
        "checks": checks,
        "blocking_issues": blocking,
        "warnings": warnings,
    }


def portal_credentials_configured(lane: str) -> bool:
    if lane == "official_api":
        return bool(os.getenv("STATE_API_TOKEN") or os.getenv("SOS_API_TOKEN"))
    if lane == "partner_api":
        return bool(os.getenv("CORPNET_API_KEY"))
    if lane in {"browser_automation", "browser_automation_candidate", "browser_profile_automation", "operator_assisted_browser_provider"}:
        return bool(os.getenv("BROWSERBASE_API_KEY") or os.getenv("STEALTH_BROWSER_PROVIDER_KEY"))
    return False


class DryRunLaneAdapter(FilingAdapter):
    """Safe default adapter that records what would happen without live filing."""

    def __init__(self, lane: str):
        self.lane = lane if lane in AUTOMATION_LANES else "operator_assisted"

    def _base_metadata(self, filing_job: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        route = job_route(filing_job)
        preflight = validate_filing_preflight(filing_job)
        return {
            "dry_run": dry_run,
            "lane": self.lane,
            "adapter_key": filing_job.get("adapter_key", ""),
            "portal_name": filing_job.get("portal_name", ""),
            "portal_url": filing_job.get("portal_url", ""),
            "automation_difficulty": filing_job.get("automation_difficulty", "unknown"),
            "customer_status": filing_job.get("customer_status", ""),
            "requires_operator": self.requires_operator(filing_job),
            "credentials_configured": portal_credentials_configured(self.lane),
            "required_evidence": filing_job.get("required_evidence") or {},
            "blockers": blockers(filing_job),
            "source_urls": route.get("source_urls", []),
            "preflight": preflight,
            "adapter_contract": build_adapter_contract(filing_job),
        }

    async def preflight(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job)
        preflight = metadata["preflight"]
        if not preflight["passed"]:
            issue_labels = ", ".join(issue["label"] for issue in preflight["blocking_issues"])
            return FilingActionResult(
                status="operator_required",
                message=f"Preflight blocked. Resolve: {issue_labels}.",
                metadata=metadata,
            )
        status = "operator_required" if self.requires_operator(filing_job) else "automation_started"
        message = "Dry-run preflight completed; no government portal was touched."
        if self.requires_operator(filing_job):
            message += " Operator verification remains required before live submission."
        return FilingActionResult(status=status, message=message, metadata=metadata)

    async def submit(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job)
        metadata["submit_allowed"] = False
        if not metadata["preflight"]["passed"]:
            return FilingActionResult(
                status="operator_required",
                message="Dry-run submit blocked by preflight validation. Resolve blocking issues first.",
                metadata=metadata,
            )
        return FilingActionResult(
            status="operator_required",
            message="Dry-run submit recorded. Live submission is disabled until adapter credentials, idempotency, and evidence capture are approved.",
            metadata=metadata,
        )

    async def check_status(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job)
        return FilingActionResult(
            status="operator_required" if self.requires_operator(filing_job) else "automation_started",
            message="Dry-run status check recorded. Official status evidence must still come from the portal or operator.",
            metadata=metadata,
        )

    async def collect_documents(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job)
        return FilingActionResult(
            status="operator_required",
            message="Dry-run document collection recorded. Official documents cannot be marked collected without stored evidence.",
            metadata=metadata,
        )

    def requires_operator(self, filing_job: dict[str, Any]) -> bool:
        if self.lane in {"operator_assisted", "operator_assisted_browser_provider"}:
            return True
        if filing_job.get("automation_difficulty") == "hard":
            return True
        return bool(blockers(filing_job))


class BrowserPreflightProbeAdapter(DryRunLaneAdapter):
    """Pilot adapter for safe portal reachability checks. It never submits."""

    async def preflight(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job, dry_run=False)
        preflight = metadata["preflight"]
        if not preflight["passed"]:
            issue_labels = ", ".join(issue["label"] for issue in preflight["blocking_issues"])
            return FilingActionResult(
                status="operator_required",
                message=f"Browser preflight blocked. Resolve: {issue_labels}.",
                metadata=metadata,
            )
        portal_url = filing_job.get("portal_url") or metadata.get("portal_url") or ""
        if not portal_url.startswith(("http://", "https://")):
            return FilingActionResult(
                status="operator_required",
                message="Browser preflight blocked because no official portal URL is available.",
                metadata=metadata,
            )
        try:
            request = urllib.request.Request(
                portal_url,
                method="GET",
                headers={"User-Agent": "SOSFilerAutomationPreflight/1.0"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                status_code = int(getattr(response, "status", 0) or 0)
                final_url = getattr(response, "url", portal_url)
            metadata["portal_probe"] = {
                "ok": 200 <= status_code < 400,
                "status_code": status_code,
                "final_url": final_url,
            }
        except urllib.error.HTTPError as exc:
            metadata["portal_probe"] = {
                "ok": False,
                "status_code": exc.code,
                "final_url": portal_url,
                "error": str(exc.reason),
            }
        except Exception as exc:
            metadata["portal_probe"] = {
                "ok": False,
                "status_code": 0,
                "final_url": portal_url,
                "error": exc.__class__.__name__,
            }

        if metadata["portal_probe"]["ok"]:
            return FilingActionResult(
                status="automation_started",
                message="Browser preflight probe reached the official portal. Submission remains disabled.",
                metadata=metadata,
            )
        return FilingActionResult(
            status="operator_required",
            message="Browser preflight probe could not reach the official portal; operator review required.",
            metadata=metadata,
        )

    async def check_status(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job, dry_run=False)
        metadata["status_check_supported"] = False
        return FilingActionResult(
            status="operator_required",
            message="Pilot status check needs a state-specific status URL or confirmation number before automation can run.",
            metadata=metadata,
        )


class StateMatrixPortalAdapter(DryRunLaneAdapter):
    """Matrix-backed Playwright lane for states with observed filing portals."""

    def __init__(self):
        super().__init__("browser_profile_automation")

    def _base_metadata(self, filing_job: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        metadata = super()._base_metadata(filing_job, dry_run=dry_run)
        state_manifest = adapter_manifest_for_state(filing_job.get("state", ""))
        metadata["state_adapter"] = f"{str(filing_job.get('state', '')).lower()}_matrix_portal_v1"
        metadata["state_automation_profile"] = state_manifest
        metadata["certification_gates"] = state_manifest.get("certification_gates", [])
        metadata["payment_screen_certification_required"] = state_manifest.get("production_readiness") == "certified_portal_entry_observed"
        metadata["live_submission_ready"] = state_manifest.get("production_readiness") == "production_ready_operator_supervised"
        metadata["portal_urls"] = state_manifest.get("portal_urls", [])
        metadata["evidence_plan"] = [
            "review_screen_snapshot",
            "payment_screen_snapshot",
            "official_submission_receipt",
            "status_polling_snapshot",
            "filed_government_document",
        ]
        return metadata

    async def preflight(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job)
        preflight = metadata["preflight"]
        if not preflight["passed"]:
            issue_labels = ", ".join(issue["label"] for issue in preflight["blocking_issues"])
            return FilingActionResult(
                status="operator_required",
                message=f"State portal preflight blocked. Resolve: {issue_labels}.",
                metadata=metadata,
            )
        if metadata["live_submission_ready"]:
            return FilingActionResult(
                status="automation_started",
                message="State portal preflight passed. This state has a production-ready operator-supervised adapter lane.",
                metadata=metadata,
            )
        return FilingActionResult(
            status="automation_started",
            message="State portal preflight passed. Next gate is review/payment-screen certification before live submission.",
            metadata=metadata,
        )

    async def submit(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job, dry_run=False)
        metadata["submit_allowed"] = False
        return FilingActionResult(
            status="operator_required",
            message="Live submission is disabled until review screen, payment screen, receipt capture, and document retrieval are certified for this state.",
            metadata=metadata,
        )


class TrustedAccessCheckpointAdapter(StateMatrixPortalAdapter):
    """Protected-portal lane. Automation pauses at a trusted operator checkpoint."""

    def __init__(self):
        super().__init__()
        self.lane = "operator_assisted_browser_provider"

    def _base_metadata(self, filing_job: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        metadata = super()._base_metadata(filing_job, dry_run=dry_run)
        metadata["requires_operator"] = True
        metadata["trusted_access_checkpoint_required"] = True
        metadata["state_adapter"] = f"{str(filing_job.get('state', '')).lower()}_trusted_access_checkpoint_v1"
        metadata["evidence_plan"] = [
            "trusted_access_checkpoint_snapshot",
            "post_checkpoint_review_screen_snapshot",
            "payment_screen_snapshot",
            "official_submission_receipt",
            "filed_government_document",
        ]
        return metadata

    async def preflight(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job)
        preflight = metadata["preflight"]
        if not preflight["passed"]:
            issue_labels = ", ".join(issue["label"] for issue in preflight["blocking_issues"])
            return FilingActionResult(
                status="operator_required",
                message=f"Trusted-access preflight blocked. Resolve: {issue_labels}.",
                metadata=metadata,
            )
        return FilingActionResult(
            status="operator_required",
            message="Trusted Access checkpoint required. Automation should run from the verified operator machine, pause for portal verification, then resume.",
            metadata=metadata,
        )

    def requires_operator(self, filing_job: dict[str, Any]) -> bool:
        return True


class TexasSOSDirectAdapter(StateMatrixPortalAdapter):
    """Texas SOSDirect adapter contract based on the proven real filing flow."""

    def _base_metadata(self, filing_job: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        metadata = super()._base_metadata(filing_job, dry_run=dry_run)
        metadata["state_adapter"] = "tx_sosdirect_v1"
        metadata["live_submission_ready"] = True
        metadata["payment_screen_certification_required"] = False
        metadata["credentials_configured"] = bool(os.getenv("TX_SOSDIRECT_USER_ID") and os.getenv("TX_SOSDIRECT_PASSWORD"))
        metadata["field_map"] = {
            "business_name": "Entity name",
            "purpose": "Supplemental provisions / purpose",
            "registered_agent": "Article 2 registered agent and registered office",
            "management": "Article 3 governing authority",
            "organizer": "Organizer / submitter",
            "effective_date": "Effectiveness of filing",
        }
        metadata["sosdirect_flow"] = [
            "login",
            "select_business_organizations",
            "start_certificate_of_formation",
            "complete_form_205",
            "review",
            "payment",
            "receipt_capture",
            "briefcase_status_polling",
            "document_zip_download",
            "customer_portal_attachment",
        ]
        metadata["evidence_plan"] = [
            "sosdirect_review_screen_snapshot",
            "sosdirect_payment_or_receipt_snapshot",
            "sosdirect_session_code",
            "sosdirect_document_number",
            "texas_certificate_of_filing_pdf_or_zip",
        ]
        return metadata

    async def preflight(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job)
        preflight = metadata["preflight"]
        if not preflight["passed"]:
            issue_labels = ", ".join(issue["label"] for issue in preflight["blocking_issues"])
            return FilingActionResult(
                status="operator_required",
                message=f"Texas SOSDirect preflight blocked. Resolve: {issue_labels}.",
                metadata=metadata,
            )
        if not metadata["credentials_configured"]:
            return FilingActionResult(
                status="operator_required",
                message="Texas SOSDirect lane is proven, but production credentials are not configured in this environment.",
                metadata=metadata,
            )
        return FilingActionResult(
            status="automation_started",
            message="Texas SOSDirect preflight passed. Adapter is ready for operator-supervised automated filing.",
            metadata=metadata,
        )


class ColoradoFormationAdapter(DryRunLaneAdapter):
    """Colorado formation lane contract.

    This adapter is intentionally no-touch for submission until SOSFiler has
    portal credentials, idempotency review, and official receipt capture
    approved. It still gives operators a state-specific field/evidence map.
    """

    def __init__(self):
        super().__init__("browser_automation")

    def _base_metadata(self, filing_job: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        metadata = super()._base_metadata(filing_job, dry_run=dry_run)
        metadata["state_adapter"] = "colorado_formation_v1"
        metadata["live_submission_ready"] = bool(os.getenv("COLORADO_SOS_PROFILE_READY") == "true")
        metadata["field_map"] = {
            "business_name": "Entity name",
            "principal_address": "Principal office street address",
            "registered_agent": "Registered agent name and Colorado street address",
            "organizer": "Organizer name and mailing address",
            "management": "Member/manager managed selection",
        }
        metadata["evidence_plan"] = [
            "review_screen_snapshot",
            "payment_screen_snapshot",
            "filed_articles_pdf",
            "state_confirmation_receipt",
        ]
        return metadata

    async def preflight(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job)
        preflight = metadata["preflight"]
        if not preflight["passed"]:
            issue_labels = ", ".join(issue["label"] for issue in preflight["blocking_issues"])
            return FilingActionResult(
                status="operator_required",
                message=f"Colorado formation preflight blocked. Resolve: {issue_labels}.",
                metadata=metadata,
            )
        return FilingActionResult(
            status="automation_started",
            message="Colorado formation dry-run preflight passed. Live filing remains disabled until portal profile and evidence capture are approved.",
            metadata=metadata,
        )

    async def submit(self, filing_job: dict[str, Any]) -> FilingActionResult:
        metadata = self._base_metadata(filing_job, dry_run=False)
        metadata["submit_allowed"] = False
        return FilingActionResult(
            status="operator_required",
            message="Colorado live submission is not enabled yet. Operator must submit and attach official receipt evidence.",
            metadata=metadata,
        )


class AdapterRegistry:
    def for_job(self, filing_job: dict[str, Any], dry_run: bool = True) -> FilingAdapter:
        state = str(filing_job.get("state") or "").upper()
        manifest = adapter_manifest_for_state(state)
        if state == "TX" and (filing_job.get("action_type") or "formation") == "formation":
            return TexasSOSDirectAdapter()
        if manifest.get("automation_lane") == "operator_assisted_browser_provider":
            return TrustedAccessCheckpointAdapter()
        if filing_job.get("state") == "CO" and (filing_job.get("action_type") or "formation") == "formation":
            return ColoradoFormationAdapter()
        if manifest.get("production_readiness") in {"certified_portal_entry_observed", "production_ready_operator_supervised"}:
            return StateMatrixPortalAdapter()
        if not dry_run:
            lane = filing_job.get("automation_lane") or "operator_assisted"
            if lane in {"browser_automation", "browser_automation_candidate", "browser_profile_automation"}:
                return BrowserPreflightProbeAdapter(lane)
            return OperatorAssistedAdapter()
        lane = filing_job.get("automation_lane") or "operator_assisted"
        return DryRunLaneAdapter(lane)


async def run_adapter_operation(
    filing_job: dict[str, Any],
    operation: str,
    *,
    dry_run: bool = True,
    registry: AdapterRegistry | None = None,
) -> FilingActionResult:
    registry = registry or AdapterRegistry()
    adapter = registry.for_job(filing_job, dry_run=dry_run)
    if operation == "preflight":
        return await adapter.preflight(filing_job)
    if operation == "submit":
        return await adapter.submit(filing_job)
    if operation == "check_status":
        return await adapter.check_status(filing_job)
    if operation == "collect_documents":
        return await adapter.collect_documents(filing_job)
    return FilingActionResult(
        status="operator_required",
        message=f"Unknown adapter operation: {operation}",
        metadata={"dry_run": dry_run, "operation": operation},
    )
