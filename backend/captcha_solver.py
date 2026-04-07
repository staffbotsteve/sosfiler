"""
SOSFiler — Captcha Solver Module
Uses Anti-Captcha for hCaptcha solving (Incapsula WAF bypass).
"""

import os
import json
import time
import logging
import urllib.request
import urllib.parse
import asyncio
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ANTICAPTCHA_API_KEY = os.getenv("ANTICAPTCHA_API_KEY", "")
ANTICAPTCHA_API_URL = "https://api.anti-captcha.com"


class CaptchaSolver:
    """Solve hCaptcha challenges via Anti-Captcha service."""
    
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or ANTICAPTCHA_API_KEY
        if not self.api_key:
            raise ValueError("ANTICAPTCHA_API_KEY not configured")
    
    def get_balance(self) -> float:
        """Check Anti-Captcha account balance."""
        payload = json.dumps({"clientKey": self.api_key}).encode()
        req = urllib.request.Request(
            f"{ANTICAPTCHA_API_URL}/getBalance",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("errorId", 0) > 0:
                raise Exception(f"API error: {result}")
            return result.get("balance", 0)
    
    def solve_hcaptcha(self, sitekey: str, url: str, timeout_seconds: int = 180) -> str:
        """
        Solve an hCaptcha challenge. Blocking call.
        
        Args:
            sitekey: The hCaptcha sitekey from the page
            url: The URL where the captcha appears
            timeout_seconds: Max time to wait for solution
            
        Returns:
            The hCaptcha response token
        """
        # Submit task
        payload = json.dumps({
            "clientKey": self.api_key,
            "task": {
                "type": "HCaptchaTaskProxyless",
                "websiteURL": url,
                "websiteKey": sitekey,
            }
        }).encode()
        
        req = urllib.request.Request(
            f"{ANTICAPTCHA_API_URL}/createTask",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if result.get("errorId", 0) > 0:
                raise Exception(f"createTask error: {result}")
            task_id = result["taskId"]
        
        logger.info(f"hCaptcha task submitted: {task_id}")
        
        # Poll for result
        start = time.time()
        poll_interval = 5
        
        while time.time() - start < timeout_seconds:
            time.sleep(poll_interval)
            
            payload = json.dumps({
                "clientKey": self.api_key,
                "taskId": task_id,
            }).encode()
            
            req = urllib.request.Request(
                f"{ANTICAPTCHA_API_URL}/getTaskResult",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                
                if result.get("status") == "ready":
                    token = result["solution"]["gRecaptchaResponse"]
                    cost = result.get("cost", "?")
                    elapsed = round(time.time() - start, 1)
                    logger.info(f"hCaptcha solved in {elapsed}s, cost: ${cost}")
                    return token
                
                if result.get("errorId", 0) > 0:
                    raise Exception(f"getTaskResult error: {result}")
        
        raise TimeoutError(f"hCaptcha solve timeout after {timeout_seconds}s")
    
    async def solve_hcaptcha_async(self, sitekey: str, url: str, timeout_seconds: int = 180) -> str:
        """Async wrapper for solve_hcaptcha."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.solve_hcaptcha(sitekey, url, timeout_seconds)
        )


def extract_hcaptcha_sitekey(page_frames) -> Optional[str]:
    """Extract hCaptcha sitekey from page frames (sync Playwright frames)."""
    for frame in page_frames:
        url = frame.url
        if 'hcaptcha.com' in url:
            parsed = urllib.parse.urlparse(url)
            frag_params = urllib.parse.parse_qs(parsed.fragment)
            sitekey = frag_params.get('sitekey', [None])[0]
            if sitekey:
                return sitekey
    return None


async def extract_hcaptcha_sitekey_async(page) -> Optional[str]:
    """Extract hCaptcha sitekey from async Playwright page."""
    for frame in page.frames:
        url = frame.url
        if 'hcaptcha.com' in url:
            parsed = urllib.parse.urlparse(url)
            frag_params = urllib.parse.parse_qs(parsed.fragment)
            sitekey = frag_params.get('sitekey', [None])[0]
            if sitekey:
                return sitekey
    return None


async def bypass_incapsula_waf(page, solver: CaptchaSolver) -> bool:
    """
    Bypass Incapsula WAF on a Playwright page.
    
    1. Detect if Incapsula is present
    2. Extract hCaptcha sitekey
    3. Solve via Anti-Captcha
    4. Inject token via onCaptchaFinished callback
    5. Wait for page to load
    
    Returns True if bypass succeeded.
    """
    html = await page.content()
    
    if '_Incapsula' not in html:
        logger.info("No Incapsula WAF detected")
        return True
    
    logger.info("Incapsula WAF detected, solving hCaptcha...")
    
    # Extract sitekey
    sitekey = await extract_hcaptcha_sitekey_async(page)
    if not sitekey:
        logger.error("Could not find hCaptcha sitekey")
        return False
    
    logger.info(f"Sitekey: {sitekey}")
    
    # Solve captcha
    token = await solver.solve_hcaptcha_async(sitekey, page.url)
    
    # Find Incapsula frame and inject
    for frame in page.frames:
        if '_Incapsula_Resource' in frame.url and 'SWUDNSAI' in frame.url:
            try:
                await frame.evaluate(f"""
                    document.querySelector('textarea[name="h-captcha-response"]').value = "{token}";
                    document.querySelector('textarea[name="g-recaptcha-response"]').value = "{token}";
                    onCaptchaFinished("{token}");
                """)
                logger.info("Token injected, onCaptchaFinished called")
            except Exception as e:
                logger.error(f"Token injection error: {e}")
                return False
            break
    else:
        logger.error("Incapsula challenge frame not found")
        return False
    
    # Wait for verification and page load
    await asyncio.sleep(15)
    
    # Verify bypass
    new_html = await page.content()
    if '_Incapsula' not in new_html and len(new_html) > 2000:
        logger.info("WAF bypass successful!")
        return True
    
    logger.warning("WAF bypass may have failed, HTML length: %d", len(new_html))
    return False
