#!/usr/bin/env python3
"""Track B follow-up #4 tests: NV hCaptcha test-key fallback removed.

Per docs/track_b_adapter_audit_2026-05-16.md recommendation #4. The
old fallback returned a hardcoded Incapsula test sitekey when iframe/
DOM extraction failed; 2Captcha would solve against that test key
and return a token that LOOKED valid but never cleared the real
Incapsula challenge. file_llc reported success while the page was
still WAF-blocked.

These tests pin the behavior change: when no sitekey can be
extracted, _solve_incapsula_captcha returns False. The caller then
sets needs_human_review=True and the run flows through to
_persist_filing_result, which escalates to operator_required.

We don't drive a real Playwright browser. We do drive _solve_
incapsula_captcha with a fake page whose locator/content returns
empty sitekey signals, and assert the function returns False.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")
os.environ.setdefault("TWOCAPTCHA_API_KEY", "test-key-for-track-b-no-test-key-tests")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

# Stub the twocaptcha module BEFORE silverflume_filer is imported so the
# function-level `from twocaptcha import TwoCaptcha` succeeds and control
# actually reaches the no-sitekey branch this PR pins. Codex review
# round-1 P2 flagged that the broad except handler was catching the
# ImportError before the changed code ever ran.
import types as _types
if "twocaptcha" not in sys.modules:
    _stub = _types.ModuleType("twocaptcha")

    class _FakeTwoCaptcha:
        def __init__(self, *args, **kwargs):
            pass

        def hcaptcha(self, **kwargs):
            # Never reached by the no-sitekey test (function returns
            # before this). If reached by another path, return empty.
            return {"code": ""}

    _stub.TwoCaptcha = _FakeTwoCaptcha
    sys.modules["twocaptcha"] = _stub


class FakeIframeList(list):
    """Awaitable wrapper to mimic locator(...).all() shape."""

    def __init__(self, items=None):
        super().__init__(items or [])


class FakeLocator:
    def __init__(self, src_attrs=None):
        self._src_attrs = list(src_attrs or [])

    async def all(self):
        return [FakeIframe(src) for src in self._src_attrs]


class FakeIframe:
    def __init__(self, src):
        self._src = src

    async def get_attribute(self, name):
        return self._src if name == "src" else None


class FakeFrame:
    def __init__(self, url=""):
        self.url = url


class FakePage:
    def __init__(self, html: str = "", iframe_srcs=None, frame_urls=None):
        self.html = html
        self.iframe_srcs = iframe_srcs or []
        # Codex Track B follow-up #4 review: _solve_incapsula_captcha
        # reads page.frames BEFORE falling back to data-sitekey scrape.
        # Without `frames` the implementation hits AttributeError and
        # the test would still pass via the broad except handler, never
        # exercising the no-sitekey branch this PR pins.
        self.frames = [FakeFrame(url) for url in (frame_urls or [])]
        self.url = "https://nvsilverflume.gov/test"
        self.evaluate_calls = []
        self.reload_called = False

    def locator(self, selector):
        return FakeLocator(self.iframe_srcs)

    async def content(self):
        return self.html

    async def evaluate(self, script):
        self.evaluate_calls.append(script)

    async def reload(self):
        self.reload_called = True


class NoSitekeyFallbackRemovedTests(unittest.TestCase):
    def test_no_sitekey_anywhere_returns_false_no_test_key_fallback(self):
        from silverflume_filer import SilverFlumeFiler  # type: ignore

        filer = SilverFlumeFiler()
        # No iframe src with sitekey, no data-sitekey in HTML.
        page = FakePage(html="<html>Incapsula blocked, no sitekey here</html>", iframe_srcs=[])
        result = asyncio.run(filer._solve_incapsula_captcha(page, "ORDER-NV-NO-KEY"))
        self.assertFalse(result)
        # And: no 2Captcha solve was attempted — evidence: no evaluate
        # calls that inject a token.
        self.assertEqual(page.evaluate_calls, [])

    def test_hardcoded_test_sitekey_string_no_longer_present_in_source(self):
        """Pin the audit fix: the Incapsula default test sitekey string
        (20000000-ffff-ffff-ffff-000000000002) must not appear as a
        runtime value anywhere in silverflume_filer.py. The audit
        flagged this as a production risk because a 2Captcha solve
        against the test key returned a token that looked valid but
        never cleared the real challenge."""
        source = (BACKEND / "silverflume_filer.py").read_text()
        # The string can only appear inside a comment (we cite it for
        # context); verify by splitting on the comment marker.
        for line in source.splitlines():
            if "20000000-ffff-ffff-ffff-000000000002" in line:
                stripped = line.strip()
                self.assertTrue(
                    stripped.startswith("#"),
                    f"hardcoded test sitekey found outside a comment: {stripped!r}",
                )

    def test_refuses_test_sitekey_log_message_present(self):
        """The new error path must log the refusal so the operator
        cockpit can correlate the failure with an actionable message."""
        source = (BACKEND / "silverflume_filer.py").read_text()
        # Source may have line breaks between the words due to Python
        # string concatenation; check for the unambiguous fragment.
        self.assertIn("refusing test-key", source)


if __name__ == "__main__":
    unittest.main()
