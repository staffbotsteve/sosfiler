#!/usr/bin/env python3
"""PR7 tests: SQLite evidence-invariant trigger.

Plan v2.6 §4.2.4 step 3. The trigger is the last line of defense — any
direct SQL UPDATE that bypasses the application/repository layers must
fail when the predicate is not satisfied.

Each test bootstraps an isolated SQLite database via server.init_db()
so the live trigger code path is exercised, then runs raw UPDATE
statements and asserts they ABORT with the expected message.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


def _bootstrap_isolated_db():
    """Return (path, conn). Caller owns close + unlink."""
    import server  # type: ignore

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    original = server.DB_PATH
    server.DB_PATH = Path(tmp.name)
    try:
        server.init_db()
    finally:
        # Caller still uses server.DB_PATH temporarily; restore in tearDown.
        pass
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row
    return tmp.name, conn, original, server


class EvidenceInvariantTriggerTests(unittest.TestCase):
    def setUp(self):
        self.path, self.conn, self._original_db, self._server = _bootstrap_isolated_db()
        # Seed one order + one filing_job in ready_to_file.
        self.conn.execute(
            """
            INSERT INTO orders (
                id, user_id, email, token, status, entity_type, state,
                business_name, formation_data, state_fee_cents,
                gov_processing_fee_cents, platform_fee_cents, total_cents
            ) VALUES (
                'ORDER-T1', NULL, 'qa@sosfiler.test', 'tkn', 'paid',
                'LLC', 'WY', 'Trigger Test LLC', '{}', 10200, 0, 2900, 13100
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO filing_jobs (
                id, order_id, action_type, state, entity_type, status,
                automation_level, filing_method, state_fee_cents,
                processing_fee_cents, total_government_cents
            ) VALUES (
                'JOB-T1', 'ORDER-T1', 'formation', 'WY', 'LLC', 'ready_to_file',
                'manual_review', 'web_portal', 10200, 0, 10200
            )
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self._server.DB_PATH = self._original_db

    def _attempt_update(self, status: str) -> str:
        """Try UPDATE filing_jobs SET status = ?; return None on success, error str on fail."""
        try:
            self.conn.execute("UPDATE filing_jobs SET status = ? WHERE id = 'JOB-T1'", (status,))
            self.conn.commit()
            return ""
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            return str(exc)

    def _insert_artifact(self, artifact_type: str, sha256_hex: str = "abc123") -> None:
        self.conn.execute(
            """
            INSERT INTO filing_artifacts
                (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence, sha256_hex)
            VALUES (?, 'ORDER-T1', ?, ?, ?, 1, ?)
            """,
            ("JOB-T1", artifact_type, f"{artifact_type}.pdf", f"/tmp/{artifact_type}.pdf", sha256_hex),
        )

    def _set_filing_confirmation(self, value: str = "L25000012345") -> None:
        from execution_platform import build_filing_confirmation_payload

        payload = build_filing_confirmation_payload(value, "adapter")
        self.conn.execute("UPDATE orders SET filing_confirmation = ? WHERE id = 'ORDER-T1'", (payload,))
        self.conn.commit()

    # ── Non-evidence transitions pass freely ───────────────────────────

    def test_ready_to_file_transition_is_unaffected_by_trigger(self):
        err = self._attempt_update("automation_started")
        self.assertEqual(err, "")

    # ── Missing filing_confirmation blocks ─────────────────────────────

    def test_submitted_without_confirmation_blocks(self):
        self._insert_artifact("submitted_receipt")
        err = self._attempt_update("submitted")
        self.assertIn("missing or malformed filing_confirmation", err)

    def test_submitted_with_raw_string_confirmation_blocks(self):
        self._insert_artifact("submitted_receipt")
        self.conn.execute(
            "UPDATE orders SET filing_confirmation = ? WHERE id = 'ORDER-T1'",
            ("just-a-raw-string",),
        )
        self.conn.commit()
        err = self._attempt_update("submitted")
        self.assertIn("missing or malformed filing_confirmation", err)

    def test_submitted_with_empty_value_blocks(self):
        self._insert_artifact("submitted_receipt")
        self.conn.execute(
            "UPDATE orders SET filing_confirmation = ? WHERE id = 'ORDER-T1'",
            (json.dumps({"value": "", "source": "operator"}),),
        )
        self.conn.commit()
        err = self._attempt_update("submitted")
        self.assertIn("missing or malformed filing_confirmation", err)

    def test_submitted_with_non_string_value_blocks(self):
        self._insert_artifact("submitted_receipt")
        self.conn.execute(
            "UPDATE orders SET filing_confirmation = ? WHERE id = 'ORDER-T1'",
            (json.dumps({"value": 123, "source": "operator"}),),
        )
        self.conn.commit()
        err = self._attempt_update("submitted")
        self.assertIn("missing or malformed filing_confirmation", err)

    # ── Missing submitted_receipt blocks ───────────────────────────────

    def test_submitted_without_artifact_blocks(self):
        self._set_filing_confirmation()
        err = self._attempt_update("submitted")
        self.assertIn("missing submitted_receipt", err)

    def test_submitted_without_sha256_blocks(self):
        self._set_filing_confirmation()
        # Insert artifact WITHOUT sha256_hex.
        self.conn.execute(
            """
            INSERT INTO filing_artifacts
                (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
            VALUES ('JOB-T1', 'ORDER-T1', 'submitted_receipt', 'r.pdf', '/tmp/r.pdf', 1)
            """
        )
        self.conn.commit()
        err = self._attempt_update("submitted")
        self.assertIn("missing submitted_receipt", err)

    def test_submitted_with_empty_string_sha256_blocks(self):
        """Plan v2.6 PR7 round-9: sha256_hex='' must not satisfy the predicate.

        IS NOT NULL alone wasn't enough — empty/whitespace placeholders
        could slip through direct SQL writes.
        """
        self._set_filing_confirmation()
        self._insert_artifact("submitted_receipt", sha256_hex="")
        err = self._attempt_update("submitted")
        self.assertIn("missing submitted_receipt", err)

    def test_submitted_with_everything_passes(self):
        self._set_filing_confirmation()
        self._insert_artifact("submitted_receipt")
        err = self._attempt_update("submitted")
        self.assertEqual(err, "")

    # ── Approved-class predicates ──────────────────────────────────────

    def test_complete_without_approved_certificate_blocks_on_formation(self):
        self._set_filing_confirmation()
        self._insert_artifact("submitted_receipt")
        err = self._attempt_update("complete")
        self.assertIn("action_type-appropriate terminal artifact", err)

    def test_complete_with_both_artifacts_passes_on_formation(self):
        self._set_filing_confirmation()
        self._insert_artifact("submitted_receipt")
        self._insert_artifact("approved_certificate")
        err = self._attempt_update("complete")
        self.assertEqual(err, "")

    def test_annual_report_complete_accepts_state_correspondence(self):
        self._set_filing_confirmation()
        self._insert_artifact("submitted_receipt")
        self._insert_artifact("state_correspondence")
        # Flip job to annual_report
        self.conn.execute("UPDATE filing_jobs SET action_type = 'annual_report' WHERE id = 'JOB-T1'")
        self.conn.commit()
        err = self._attempt_update("complete")
        self.assertEqual(err, "")

    def test_formation_complete_rejects_state_correspondence(self):
        self._set_filing_confirmation()
        self._insert_artifact("submitted_receipt")
        self._insert_artifact("state_correspondence")
        err = self._attempt_update("complete")
        self.assertIn("action_type-appropriate terminal artifact", err)

    def test_amendment_complete_accepts_state_filed_document(self):
        self._set_filing_confirmation()
        self._insert_artifact("submitted_receipt")
        self._insert_artifact("state_filed_document")
        self.conn.execute("UPDATE filing_jobs SET action_type = 'amendment' WHERE id = 'JOB-T1'")
        self.conn.commit()
        err = self._attempt_update("complete")
        self.assertEqual(err, "")

    # ── Legacy status aliases also enforced ────────────────────────────

    def test_legacy_submitted_to_state_alias_enforced(self):
        # No filing_confirmation, no artifact.
        err = self._attempt_update("submitted_to_state")
        self.assertIn("missing or malformed filing_confirmation", err)

    def test_legacy_state_approved_alias_enforced(self):
        err = self._attempt_update("state_approved")
        self.assertIn("missing or malformed filing_confirmation", err)


if __name__ == "__main__":
    unittest.main()
