#!/usr/bin/env python3
"""Playwright E2E smoke tests for the SOSFiler operator cockpit.

Required environment:
  PLAYWRIGHT_BASE_URL=https://ops.sosfiler.com
  PLAYWRIGHT_ADMIN_TOKEN=<admin token>

These tests intentionally exercise the public operator UI and create a
unique test enhancement ticket.
"""

import os
import unittest
import uuid
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def load_playwright_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if key not in {"PLAYWRIGHT_BASE_URL", "PLAYWRIGHT_ADMIN_TOKEN", "PLAYWRIGHT_CHROME_EXECUTABLE"}:
            continue
        os.environ[key] = value.strip().strip("\"'")


class OperatorPlaywrightE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_playwright_env()
        cls.base_url = os.environ.get("PLAYWRIGHT_BASE_URL", "").rstrip("/")
        cls.admin_token = os.environ.get("PLAYWRIGHT_ADMIN_TOKEN", "")
        if not cls.base_url or not cls.admin_token:
            raise unittest.SkipTest("PLAYWRIGHT_BASE_URL and PLAYWRIGHT_ADMIN_TOKEN are required")
        cls.playwright = sync_playwright().start()
        launch_options = {
            "headless": os.environ.get("PLAYWRIGHT_HEADLESS", "1") != "0",
            "args": ["--no-sandbox"],
        }
        executable = os.environ.get("PLAYWRIGHT_CHROME_EXECUTABLE")
        if executable:
            launch_options["executable_path"] = executable
        try:
            cls.browser = cls.playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f"Playwright browser could not launch: {str(exc).splitlines()[0]}")

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser:
            browser.close()
        playwright = getattr(cls, "playwright", None)
        if playwright:
            playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1100})
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def open_operator(self):
        page = self.page
        page.goto(f"{self.base_url}/operator.html", wait_until="domcontentloaded")
        expect(page.locator("#adminToken")).to_be_visible(timeout=10_000)
        page.locator("#adminToken").fill(self.admin_token)
        page.locator("#saveTokenBtn").click()
        expect(page.locator("#systemHealthSummary")).to_contain_text("Overall", timeout=20_000)
        expect(page.locator("#systemHealthSummary")).to_contain_text("Database Write Read", timeout=20_000)
        expect(page.locator("#engineeringCount")).to_contain_text("jobs", timeout=15_000)
        return page

    def wait_operator_idle(self, page):
        page.wait_for_function(
            "() => window.__sosfilerOperatorState && !window.__sosfilerOperatorState.busy",
            timeout=15_000,
        )

    def test_operator_ticket_to_guarded_execution_flow(self):
        page = self.open_operator()
        marker = uuid.uuid4().hex[:8].upper()
        prompt = (
            f"Playwright E2E {marker}: please add an operator-visible checklist "
            "for annual report filing readiness."
        )

        page.locator("#ticketStatusFilter").select_option("")
        page.locator("#testTicketPrompt").fill(prompt)
        page.locator("#createTestTicketBtn").click()
        ticket_list = page.locator("#ticketList")
        expect(ticket_list).to_contain_text(prompt, timeout=20_000)

        ticket_button = page.locator("#ticketList button.job").filter(has_text=prompt)
        expect(ticket_button).to_have_count(1)
        ticket_button.click()
        detail = page.locator("#detail")
        expect(detail).to_contain_text("Approve Enhancement", timeout=10_000)
        page.locator("#ticketOperator").fill("playwright")
        page.locator("#ticketApprovalNote").fill(f"E2E approval {marker}")
        page.get_by_role("button", name="Approve Enhancement").click()

        engineering_list = page.locator("#engineeringList")
        expect(engineering_list).to_contain_text(prompt, timeout=20_000)
        engineering_button = page.locator("#engineeringList button.job").filter(has_text=prompt)
        expect(engineering_button).to_have_count(1)
        engineering_button.click()
        expect(detail).to_contain_text("Implementation Plan", timeout=10_000)

        page.get_by_role("button", name="Create Work Plan").click()
        expect(detail).to_contain_text("work_plan.md", timeout=15_000)
        self.wait_operator_idle(page)
        page.get_by_role("button", name="Prepare Execution").click()
        expect(detail).to_contain_text("Guarded Execution", timeout=15_000)
        expect(detail).to_contain_text("execution_package.md", timeout=15_000)
        expect(detail).to_contain_text("In Progress", timeout=15_000)
        self.wait_operator_idle(page)
        page.get_by_role("button", name="Run Required Tests").click()
        expect(detail).to_contain_text("Required Test Run", timeout=120_000)
        expect(detail).to_contain_text("Passed", timeout=120_000)
        expect(detail).to_contain_text("Ready To Deploy", timeout=120_000)
        self.wait_operator_idle(page)
        page.locator("#engineeringDeploymentUrl").fill(f"{self.base_url}/operator.html")
        page.get_by_role("button", name="Run Deploy Check").click()
        expect(detail).to_contain_text("Deploy Check", timeout=30_000)
        expect(detail).to_contain_text("2/2 production deploy check(s) passed", timeout=30_000)
        expect(detail).to_contain_text("Deployed", timeout=30_000)

    def test_annual_report_readiness_visible_when_jobs_exist(self):
        page = self.open_operator()
        response = page.request.get(
            f"{self.base_url}/api/admin/filing-jobs",
            headers={"x-admin-token": self.admin_token},
        )
        self.assertTrue(response.ok, response.text())
        jobs = response.json().get("jobs", [])
        annual_jobs = [job for job in jobs if job.get("action_type") == "annual_report"]
        if not annual_jobs:
            marker = uuid.uuid4().hex[:8].upper()
            fixture_response = page.request.post(
                f"{self.base_url}/api/admin/qa/annual-report-job",
                headers={"x-admin-token": self.admin_token},
                data={
                    "state": "CA",
                    "entity_type": "LLC",
                    "business_name": f"Playwright Annual Report {marker} LLC",
                    "email": "playwright-e2e@sosfiler.com",
                },
            )
            self.assertTrue(fixture_response.ok, fixture_response.text())
            annual_jobs = [fixture_response.json()["job"]]

        job_id = annual_jobs[0]["id"]
        detail_response = page.request.get(
            f"{self.base_url}/api/admin/filing-jobs/{job_id}",
            headers={"x-admin-token": self.admin_token},
        )
        self.assertTrue(detail_response.ok, detail_response.text())
        checklist = detail_response.json()["job"].get("readiness_checklist") or []
        self.assertTrue(checklist)

        page.locator("#refreshBtn").click()
        expect(page.locator("#jobList")).to_contain_text(job_id, timeout=15_000)
        page.locator("#jobList button.job").filter(has_text=job_id).click()
        expect(page.locator("#detail")).to_contain_text("Annual Report Readiness", timeout=10_000)


if __name__ == "__main__":
    unittest.main()
