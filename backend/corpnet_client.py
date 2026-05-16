"""
CorpNet partner API client scaffold for Registered Agent flows.

This module is intentionally conservative:
- It supports dry-run mode when credentials are not configured.
- It does not modify SOSFiler order/job status.
- It only sends partner API calls when explicitly configured.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class CorpNetConfig:
    base_url: str
    api_key: str
    quote_path: str
    order_path: str
    status_path_template: str
    timeout_seconds: int


class CorpNetClient:
    def __init__(self, config: Optional[CorpNetConfig] = None):
        if config:
            self.config = config
        else:
            self.config = CorpNetConfig(
                base_url=os.getenv("CORPNET_API_BASE_URL", "").rstrip("/"),
                api_key=os.getenv("CORPNET_API_KEY", ""),
                quote_path=os.getenv("CORPNET_RA_QUOTE_PATH", "/v1/registered-agent/quotes"),
                order_path=os.getenv("CORPNET_RA_ORDER_PATH", "/v1/registered-agent/orders"),
                status_path_template=os.getenv(
                    "CORPNET_RA_STATUS_PATH_TEMPLATE",
                    "/v1/registered-agent/orders/{external_order_id}",
                ),
                timeout_seconds=int(os.getenv("CORPNET_API_TIMEOUT_SECONDS", "25")),
            )

    @property
    def configured(self) -> bool:
        return bool(self.config.base_url and self.config.api_key)

    def _full_url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def _request_json(self, method: str, path: str, payload: Optional[dict] = None) -> Dict[str, Any]:
        if not self.configured:
            raise RuntimeError("CorpNet API is not configured")

        body = None
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            self._full_url(path),
            data=body,
            headers=headers,
            method=method.upper(),
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                return {
                    "ok": True,
                    "http_status": resp.status,
                    "data": parsed,
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"raw": raw}
            return {
                "ok": False,
                "http_status": exc.code,
                "error": "corpnet_http_error",
                "details": parsed,
            }
        except urllib.error.URLError as exc:
            return {
                "ok": False,
                "http_status": None,
                "error": "corpnet_connection_error",
                "details": str(exc.reason),
            }

    def quote_registered_agent(self, payload: dict) -> Dict[str, Any]:
        return self._request_json("POST", self.config.quote_path, payload)

    def create_registered_agent_order(self, payload: dict) -> Dict[str, Any]:
        return self._request_json("POST", self.config.order_path, payload)

    def get_registered_agent_order(self, external_order_id: str) -> Dict[str, Any]:
        path = self.config.status_path_template.format(external_order_id=external_order_id)
        return self._request_json("GET", path, None)
