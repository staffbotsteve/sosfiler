"""
IRS EIN browser-worker harness.

This worker is intentionally separate from the main web app server. The IRS EIN
assistant may block generic cloud/headless environments, so every worker must
pass a preflight before it can claim live EIN submissions.
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "generated_docs"
IRS_EIN_URL = "https://sa.www4.irs.gov/modiein/individual/index.jsp"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def queue_files() -> list[Path]:
    if not DOCS_DIR.exists():
        return []
    return sorted(DOCS_DIR.glob("*/ein_queue.json"))


def load_queue(path: Path) -> dict:
    return json.loads(path.read_text())


def save_queue(path: Path, payload: dict) -> None:
    payload["updated_at"] = utc_now()
    path.write_text(json.dumps(payload, indent=2))


def redacted_queue_summary(path: Path, payload: dict) -> dict:
    ss4 = payload.get("ss4_data", {})
    responsible = ss4.get("responsible_party", {})
    ssn_digits = re.sub(r"\D", "", str(responsible.get("ssn", "")))
    return {
        "queue_file": str(path),
        "order_id": payload.get("order_id"),
        "status": payload.get("status"),
        "entity_name": ss4.get("entity_name"),
        "entity_type": ss4.get("entity_type"),
        "formation_state": ss4.get("state"),
        "responsible_party_name": responsible.get("name"),
        "has_full_ssn": bool(re.fullmatch(r"\d{9}", ssn_digits)),
        "ssn_last4": ssn_digits[-4:] if re.fullmatch(r"\d{9}", ssn_digits) else "",
        "business_city": (ss4.get("business_address") or {}).get("city"),
        "business_state": (ss4.get("business_address") or {}).get("state"),
    }


async def irs_preflight(headless: bool = True) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status": "playwright_missing",
            "message": "Playwright is not installed in this worker environment.",
        }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        try:
            await page.goto(IRS_EIN_URL, wait_until="domcontentloaded", timeout=45_000)
            title = await page.title()
            body = (await page.locator("body").inner_text(timeout=15_000))[:2000]
            normalized = f"{title}\n{body}".lower()
            if "access denied" in normalized or "don't have permission" in normalized:
                return {
                    "status": "blocked_by_access_control",
                    "url": page.url,
                    "title": title,
                    "message": "IRS EIN assistant blocked this worker environment before the wizard loaded.",
                }
            if "unavailable" in normalized or "maintenance" in normalized:
                return {
                    "status": "irs_unavailable",
                    "url": page.url,
                    "title": title,
                    "message": "IRS EIN assistant loaded but is unavailable or under maintenance.",
                }
            return {
                "status": "available",
                "url": page.url,
                "title": title,
                "message": "IRS EIN assistant appears reachable from this worker.",
            }
        finally:
            await browser.close()


async def mark_ready_items_with_preflight(limit: int, headless: bool, dry_run: bool) -> list[dict]:
    preflight = await irs_preflight(headless=headless)
    results: list[dict] = []
    for path in queue_files()[:limit]:
        payload = load_queue(path)
        summary = redacted_queue_summary(path, payload)
        if payload.get("status") != "ready_for_submission":
            summary["action"] = "skipped"
            results.append(summary)
            continue
        summary["preflight"] = preflight["status"]
        if preflight["status"] != "available":
            summary["action"] = "marked_blocked" if not dry_run else "would_mark_blocked"
            if not dry_run:
                payload["status"] = preflight["status"]
                payload["worker_preflight"] = preflight
                save_queue(path, payload)
        else:
            summary["action"] = "ready_for_browser_submission"
            if not dry_run:
                payload["worker_preflight"] = preflight
                payload["last_ready_check_at"] = utc_now()
                save_queue(path, payload)
        results.append(summary)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="IRS EIN browser-worker preflight and queue classifier.")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--headed", action="store_true", help="Run browser headed for local worker diagnostics.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    import asyncio

    if args.preflight_only:
        result = asyncio.run(irs_preflight(headless=not args.headed))
        print(json.dumps(result, indent=2))
        return 0

    results = asyncio.run(
        mark_ready_items_with_preflight(limit=args.limit, headless=not args.headed, dry_run=args.dry_run)
    )
    print(json.dumps({"count": len(results), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
