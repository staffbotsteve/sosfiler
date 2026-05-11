#!/usr/bin/env python3
"""Tests for approved-enhancement engineering queue."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class FakeHTTPResponse:
    def __init__(self, body, status=200):
        self.body = body.encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class EngineeringQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_docs_dir = server.DOCS_DIR
        self.old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ["ADMIN_TOKEN"] = "test-admin"
        server.DB_PATH = Path(self.tmp.name) / "engineering.db"
        server.DOCS_DIR = Path(self.tmp.name) / "generated_docs"
        server.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        server.init_db()
        self.client = TestClient(server.app)
        self.headers = {"x-admin-token": "test-admin"}

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        server.DOCS_DIR = self.old_docs_dir
        if self.old_mode is None:
            os.environ.pop("EXECUTION_PERSISTENCE_MODE", None)
        else:
            os.environ["EXECUTION_PERSISTENCE_MODE"] = self.old_mode
        if self.old_admin is None:
            os.environ.pop("ADMIN_TOKEN", None)
        else:
            os.environ["ADMIN_TOKEN"] = self.old_admin
        self.tmp.cleanup()

    def insert_ticket(self, ticket_id="TKT-ENG"):
        conn = server.get_db()
        conn.execute("""
            INSERT INTO support_tickets (
                id, ticket_type, status, priority, customer_email, question,
                confidence_reason, suggested_answer, slack_sent
            ) VALUES (?, 'enhancement', 'open', 'high', 'ops-test@sosfiler.com', ?, ?, ?, 1)
        """, (
            ticket_id,
            "Add a safer enhancement queue.",
            "Approved operator improvement request.",
            "Track engineering status with stop conditions.",
        ))
        conn.commit()
        conn.close()
        return ticket_id

    def approve_ticket(self):
        ticket_id = self.insert_ticket()
        res = self.client.post(
            f"/api/admin/slack/tickets/{ticket_id}/approve",
            headers=self.headers,
            json={"approved_by": "tester", "approval_note": "Queue it"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["automation_run_id"]

    def prepare_and_pass_tests(self):
        run_id = self.approve_ticket()
        self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/prepare-execution",
            headers=self.headers,
            json={"approved_by": "executor", "approval_note": "Prepare execution"},
        )
        with patch.object(server.subprocess, "run") as run_mock:
            run_mock.return_value = SimpleNamespace(returncode=0, stdout="OK\n", stderr="")
            res = self.client.post(
                f"/api/admin/engineering-jobs/{run_id}/run-tests",
                headers=self.headers,
                json={"actor": "tester", "test_command": "python3 -m py_compile backend/server.py"},
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["job"]["status"], "ready_to_deploy")
        return run_id

    def test_approved_ticket_appears_in_engineering_queue(self):
        run_id = self.approve_ticket()

        res = self.client.get("/api/admin/engineering-jobs", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        jobs = res.json()["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], run_id)
        self.assertEqual(jobs[0]["status"], "approved")
        self.assertEqual(jobs[0]["ticket_id"], "TKT-ENG")
        self.assertIn("failing_tests", jobs[0]["stop_conditions"])
        self.assertEqual(jobs[0]["ticket"]["question"], "Add a safer enhancement queue.")
        self.assertEqual(jobs[0]["engineering_plan"]["source_ticket_id"], "TKT-ENG")
        self.assertIn("acceptance_criteria", jobs[0]["engineering_plan"])
        self.assertIn("required_tests", jobs[0]["engineering_plan"])
        self.assertIn("standard_code_review", jobs[0]["engineering_plan"]["risk_flags"])

    def test_engineering_job_transitions_and_logs_status(self):
        run_id = self.approve_ticket()

        res = self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/transition",
            headers=self.headers,
            json={
                "target_status": "in_progress",
                "actor": "tester",
                "message": "Implementation started.",
                "test_command": ".venv312/bin/python -m unittest",
            },
        )

        self.assertEqual(res.status_code, 200, res.text)
        job = res.json()["job"]
        self.assertEqual(job["status"], "in_progress")
        self.assertEqual(job["redacted_log"][-1]["actor"], "tester")
        self.assertEqual(job["redacted_log"][-1]["to_status"], "in_progress")

    def test_invalid_engineering_transition_is_rejected(self):
        run_id = self.approve_ticket()

        res = self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/transition",
            headers=self.headers,
            json={"target_status": "deployed", "actor": "tester"},
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("Cannot transition", res.json()["detail"])

    def test_backfill_creates_jobs_for_previously_approved_tickets(self):
        ticket_id = self.insert_ticket("TKT-BACKFILL")
        conn = server.get_db()
        conn.execute("""
            UPDATE support_tickets
            SET status = 'approved', approved_by = 'legacy-operator', approved_at = datetime('now')
            WHERE id = ?
        """, (ticket_id,))
        conn.commit()
        conn.close()

        res = self.client.post("/api/admin/engineering-jobs/backfill-approved", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["created_count"], 1)
        jobs = self.client.get("/api/admin/engineering-jobs", headers=self.headers).json()["jobs"]
        self.assertEqual(jobs[0]["ticket_id"], "TKT-BACKFILL")

    def test_refresh_plan_appends_plan_event(self):
        run_id = self.approve_ticket()

        res = self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/plan",
            headers=self.headers,
            json={"approved_by": "planner", "approval_note": "Refresh plan"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        job = res.json()["job"]
        self.assertEqual(job["engineering_plan"]["source_ticket_id"], "TKT-ENG")
        self.assertEqual(job["redacted_log"][-1]["message"], "Engineering implementation plan refreshed.")
        self.assertEqual(job["redacted_log"][-1]["actor"], "planner")

    def test_create_work_plan_writes_markdown_artifact_and_logs_path(self):
        run_id = self.approve_ticket()

        res = self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/work-plan",
            headers=self.headers,
            json={"approved_by": "planner", "approval_note": "Create work plan"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        work_plan = payload["work_plan"]
        path = Path(work_plan["file_path"])
        self.assertEqual(work_plan["filename"], "work_plan.md")
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("Engineering Work Plan", content)
        self.assertIn("TKT-ENG", content)
        self.assertIn("Add a safer enhancement queue.", content)
        self.assertIn("Acceptance Criteria", content)
        self.assertIn("Required Tests", content)
        self.assertIn("Stop Conditions", content)
        self.assertIn("Risk Flags", content)

        job = payload["job"]
        self.assertEqual(job["work_plan"]["file_path"], str(path))
        self.assertEqual(job["redacted_log"][-1]["message"], "Engineering work plan artifact created.")
        listed = self.client.get("/api/admin/engineering-jobs", headers=self.headers).json()["jobs"][0]
        self.assertEqual(listed["work_plan"]["file_path"], str(path))

    def test_prepare_execution_creates_guarded_package_and_moves_in_progress(self):
        run_id = self.approve_ticket()

        res = self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/prepare-execution",
            headers=self.headers,
            json={"approved_by": "executor", "approval_note": "Prepare execution"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        job = payload["job"]
        package = payload["execution_package"]
        self.assertEqual(job["status"], "in_progress")
        self.assertTrue(package["can_start"])
        package_path = Path(package["file_path"])
        work_path = Path(job["work_plan"]["file_path"])
        self.assertEqual(package["filename"], "execution_package.md")
        self.assertTrue(package_path.exists())
        self.assertTrue(work_path.exists())
        content = package_path.read_text(encoding="utf-8")
        self.assertIn("Guarded Engineering Execution Package", content)
        self.assertIn("Can start implementation: yes", content)
        self.assertIn("Required Tests", content)
        self.assertIn("Stop Conditions", content)
        gate_keys = {gate["key"] for gate in job["execution_gates"]}
        self.assertIn("work_plan_artifact", gate_keys)
        self.assertIn("required_tests", gate_keys)
        self.assertEqual(job["execution_package"]["file_path"], str(package_path))
        self.assertEqual(job["redacted_log"][-1]["message"], "Guarded engineering execution package prepared.")
        self.assertEqual(job["redacted_log"][-1]["to_status"], "in_progress")

    def test_prepare_execution_does_not_move_ready_to_deploy_backwards(self):
        run_id = self.approve_ticket()
        self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/transition",
            headers=self.headers,
            json={"target_status": "in_progress", "actor": "tester"},
        )
        self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/transition",
            headers=self.headers,
            json={"target_status": "ready_to_deploy", "actor": "tester"},
        )

        res = self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/prepare-execution",
            headers=self.headers,
            json={"approved_by": "executor", "approval_note": "Prepare execution"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        job = res.json()["job"]
        self.assertEqual(job["status"], "ready_to_deploy")
        status_gate = next(gate for gate in job["execution_gates"] if gate["key"] == "status_allows_execution")
        self.assertEqual(status_gate["status"], "blocked")

    def test_run_required_tests_marks_ready_to_deploy_when_allowlisted_tests_pass(self):
        run_id = self.approve_ticket()
        self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/prepare-execution",
            headers=self.headers,
            json={"approved_by": "executor", "approval_note": "Prepare execution"},
        )

        with patch.object(server.subprocess, "run") as run_mock:
            run_mock.return_value = SimpleNamespace(returncode=0, stdout="OK\n", stderr="")
            res = self.client.post(
                f"/api/admin/engineering-jobs/{run_id}/run-tests",
                headers=self.headers,
                json={"actor": "tester", "test_command": "python3 -m py_compile backend/server.py"},
            )

        self.assertEqual(res.status_code, 200, res.text)
        job = res.json()["job"]
        self.assertEqual(job["status"], "ready_to_deploy")
        self.assertTrue(job["latest_test_run"]["passed"])
        self.assertIn("1/1 required test command(s) passed", job["latest_test_run"]["summary"])
        test_gate = next(gate for gate in job["execution_gates"] if gate["key"] == "required_tests_passed")
        self.assertEqual(test_gate["status"], "ready")
        self.assertEqual(job["redacted_log"][-1]["message"], "Required engineering tests passed.")

    def test_run_required_tests_rejects_non_allowlisted_command(self):
        run_id = self.approve_ticket()
        self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/prepare-execution",
            headers=self.headers,
            json={"approved_by": "executor", "approval_note": "Prepare execution"},
        )

        res = self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/run-tests",
            headers=self.headers,
            json={"actor": "tester", "test_command": "python3 -c print('unsafe')"},
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("not allowlisted", res.json()["detail"])

    def test_run_required_tests_marks_tests_failed_when_command_fails(self):
        run_id = self.approve_ticket()
        self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/prepare-execution",
            headers=self.headers,
            json={"approved_by": "executor", "approval_note": "Prepare execution"},
        )

        with patch.object(server.subprocess, "run") as run_mock:
            run_mock.return_value = SimpleNamespace(returncode=1, stdout="", stderr="boom")
            res = self.client.post(
                f"/api/admin/engineering-jobs/{run_id}/run-tests",
                headers=self.headers,
                json={"actor": "tester", "test_command": "python3 -m py_compile backend/server.py"},
            )

        self.assertEqual(res.status_code, 200, res.text)
        job = res.json()["job"]
        self.assertEqual(job["status"], "tests_failed")
        self.assertFalse(job["latest_test_run"]["passed"])
        self.assertIn("boom", job["latest_test_run"]["commands"][0]["stderr"])
        self.assertEqual(job["redacted_log"][-1]["message"], "Required engineering tests failed.")

    def test_deploy_check_marks_deployed_when_public_checks_pass(self):
        run_id = self.prepare_and_pass_tests()

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if url.endswith("/api/health"):
                return FakeHTTPResponse('{"status":"ok","service":"SOSFiler"}')
            if url.endswith("/operator.html"):
                return FakeHTTPResponse("Engineering Queue Run Required Tests Guarded Execution")
            raise AssertionError(url)

        with patch.object(server.urllib.request, "urlopen", side_effect=fake_urlopen):
            res = self.client.post(
                f"/api/admin/engineering-jobs/{run_id}/deploy-check",
                headers=self.headers,
                json={"actor": "tester", "deployment_url": "https://ops.sosfiler.com/operator.html"},
            )

        self.assertEqual(res.status_code, 200, res.text)
        job = res.json()["job"]
        self.assertEqual(job["status"], "deployed")
        self.assertTrue(job["latest_deploy_check"]["passed"])
        self.assertIn("2/2 production deploy check(s) passed", job["latest_deploy_check"]["summary"])
        self.assertEqual(job["redacted_log"][-1]["message"], "Production deploy check passed.")

    def test_deploy_check_replaces_localhost_plan_target_with_public_base(self):
        run_id = self.prepare_and_pass_tests()
        requested_urls = []

        def fake_urlopen(request, timeout=0):
            requested_urls.append(request.full_url)
            url = request.full_url
            if url.endswith("/api/health"):
                return FakeHTTPResponse('{"status":"ok","service":"SOSFiler"}')
            if url.endswith("/operator.html"):
                return FakeHTTPResponse("Engineering Queue Run Required Tests Guarded Execution")
            raise AssertionError(url)

        with patch.dict(os.environ, {"SOSFILER_PUBLIC_BASE_URL": "https://public.example.com"}):
            with patch.object(server.urllib.request, "urlopen", side_effect=fake_urlopen):
                res = self.client.post(
                    f"/api/admin/engineering-jobs/{run_id}/deploy-check",
                    headers=self.headers,
                    json={"actor": "tester", "deployment_url": "http://127.0.0.1:8017/operator.html"},
                )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(requested_urls, [
            "https://public.example.com/api/health",
            "https://public.example.com/operator.html",
        ])
        self.assertEqual(res.json()["job"]["status"], "deployed")

    def test_deploy_check_marks_blocked_when_public_check_fails(self):
        run_id = self.prepare_and_pass_tests()

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if url.endswith("/api/health"):
                return FakeHTTPResponse('{"status":"ok","service":"SOSFiler"}')
            if url.endswith("/operator.html"):
                return FakeHTTPResponse("Missing expected controls")
            raise AssertionError(url)

        with patch.object(server.urllib.request, "urlopen", side_effect=fake_urlopen):
            res = self.client.post(
                f"/api/admin/engineering-jobs/{run_id}/deploy-check",
                headers=self.headers,
                json={"actor": "tester", "deployment_url": "https://ops.sosfiler.com/operator.html"},
            )

        self.assertEqual(res.status_code, 200, res.text)
        job = res.json()["job"]
        self.assertEqual(job["status"], "blocked")
        self.assertFalse(job["latest_deploy_check"]["passed"])
        self.assertEqual(job["redacted_log"][-1]["message"], "Production deploy check failed.")

    def test_deploy_check_requires_ready_to_deploy(self):
        run_id = self.approve_ticket()

        res = self.client.post(
            f"/api/admin/engineering-jobs/{run_id}/deploy-check",
            headers=self.headers,
            json={"actor": "tester", "deployment_url": "https://ops.sosfiler.com/operator.html"},
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("ready_to_deploy", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
