#!/usr/bin/env python3
"""Track B follow-up #3 tests: TX SOSDirect MFA / 2FA detection.

Plan v2.6 §4.5 / audit recommendation #3. tx_sosdirect_document_worker
used to fall opaquely through to "login failed" when SOSDirect routed
into an MFA / verification flow. The new login() guard detects MFA
tokens in the response body, takes a checkpoint screenshot, and
raises TexasMfaChallenge — which the worker-level except wraps via
escalate_to_operator_required (PR #8) so the cockpit surfaces it.

These tests exercise the detection helper directly via a fake page
stand-in so we don't need a live Playwright browser.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")
os.environ.setdefault("TX_SOSDIRECT_USER_ID", "test-user")
os.environ.setdefault("TX_SOSDIRECT_PASSWORD", "test-pass")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


class FakeLocator:
    def __init__(self, count: int = 0):
        self._count = count

    async def count(self):
        return self._count


class FakePage:
    """Minimal Playwright Page stand-in for login() flow."""

    def __init__(self, response_html: str):
        self.response_html = response_html
        self.fills = []
        self.clicks = []
        self.screenshots = []

    async def goto(self, url, **kwargs):
        return None

    async def fill(self, selector, value):
        self.fills.append((selector, value))

    async def click(self, selector, **kwargs):
        self.clicks.append(selector)

    async def wait_for_load_state(self, *args, **kwargs):
        return None

    async def content(self):
        return self.response_html

    def locator(self, selector):
        return FakeLocator(count=0)

    async def screenshot(self, **kwargs):
        path = kwargs.get("path")
        if path:
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
            self.screenshots.append(path)


class MfaDetectionTests(unittest.TestCase):
    def test_mfa_challenge_token_raises_typed_exception(self):
        from tx_sosdirect_document_worker import TexasMfaChallenge, login  # type: ignore

        page = FakePage("<html>Please enter your Multi-Factor Authentication code</html>")
        with self.assertRaises(TexasMfaChallenge) as ctx:
            asyncio.run(login(page, "user", "pass"))
        self.assertIn("MFA/identity challenge", str(ctx.exception))
        self.assertTrue(ctx.exception.evidence_path.endswith(".png"))
        self.assertEqual(len(page.screenshots), 1)

    def test_verification_code_token_raises(self):
        from tx_sosdirect_document_worker import TexasMfaChallenge, login  # type: ignore

        page = FakePage("<p>Enter the verification code we just sent.</p>")
        with self.assertRaises(TexasMfaChallenge):
            asyncio.run(login(page, "user", "pass"))

    def test_non_mfa_login_falls_through_to_normal_failure(self):
        from tx_sosdirect_document_worker import login  # type: ignore

        # The normal login-failure path raises a plain RuntimeError, not
        # TexasMfaChallenge.
        page = FakePage(
            '<html>SOSDirect Account Login <input type="text" name="client_id"></html>'
        )
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(login(page, "user", "pass"))
        # Must be the generic message, not the MFA one.
        self.assertIn("password reset", str(ctx.exception))

    def test_normal_success_page_returns_session_code(self):
        from tx_sosdirect_document_worker import login  # type: ignore

        page = FakePage("<html>Welcome session code is: ABC123XYZ</html>")
        session_code = asyncio.run(login(page, "user", "pass"))
        self.assertEqual(session_code, "ABC123XYZ")

    def test_all_known_mfa_tokens_trigger(self):
        from tx_sosdirect_document_worker import MFA_CHALLENGE_TOKENS, TexasMfaChallenge, login  # type: ignore

        for token in MFA_CHALLENGE_TOKENS:
            page = FakePage(f"<html>Stuff... {token} ...more</html>")
            with self.assertRaises(TexasMfaChallenge, msg=f"token {token!r} did not trigger"):
                asyncio.run(login(page, "user", "pass"))


if __name__ == "__main__":
    unittest.main()
