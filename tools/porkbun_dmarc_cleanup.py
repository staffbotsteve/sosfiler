#!/usr/bin/env python3
"""Clean up SOSFiler DMARC records via Porkbun API.

This tool intentionally reads Porkbun credentials from an env file and never
prints them. It only touches DMARC TXT records for the target domain.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"env file not found: {path}")
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class PorkbunDns:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base = "https://api.porkbun.com/api/json/v3/dns"

    def call(self, path: str, payload: dict | None = None) -> dict:
        body = dict(payload or {})
        body["apikey"] = self.api_key
        body["secretapikey"] = self.secret_key
        req = urllib.request.Request(
            f"{self.base}/{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("status") != "SUCCESS":
            raise RuntimeError(f"Porkbun API failed for {path}: {data}")
        return data


def dmarc_records(records: list[dict], domain: str) -> tuple[list[dict], list[dict]]:
    root_dmarc = []
    real_dmarc = []
    for record in records:
        rtype = (record.get("type") or "").upper()
        name = (record.get("name") or "").rstrip(".")
        content = (record.get("content") or "").strip()
        if rtype != "TXT" or not content.lower().startswith("v=dmarc1"):
            continue
        if name == domain:
            root_dmarc.append(record)
        elif name == f"_dmarc.{domain}":
            real_dmarc.append(record)
    return root_dmarc, real_dmarc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default="sosfiler.com")
    parser.add_argument("--env", default="/opt/sosfiler/app/.env")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--dmarc",
        default="v=DMARC1; p=none; rua=mailto:dmarc@sosfiler.com; fo=1",
    )
    args = parser.parse_args()

    load_env(Path(args.env))
    api_key = os.environ.get("PORKBUN_API_KEY")
    secret_key = os.environ.get("PORKBUN_SECRET_API_KEY")
    if not api_key or not secret_key:
        raise SystemExit("missing PORKBUN_API_KEY or PORKBUN_SECRET_API_KEY")

    client = PorkbunDns(api_key, secret_key)
    records = client.call(f"retrieve/{args.domain}").get("records", [])
    root_dmarc, real_dmarc = dmarc_records(records, args.domain)

    changes = []
    for record in root_dmarc:
        changes.append({
            "action": "delete_root_dmarc",
            "id": record.get("id"),
            "name": record.get("name"),
            "content": record.get("content"),
        })
        if not args.dry_run:
            client.call(f"delete/{args.domain}/{record['id']}")

    needs_recreate = len(real_dmarc) != 1 or (
        real_dmarc and (real_dmarc[0].get("content") or "").strip() != args.dmarc
    )
    if needs_recreate:
        for record in real_dmarc:
            changes.append({
                "action": "delete_old__dmarc",
                "id": record.get("id"),
                "name": record.get("name"),
                "content": record.get("content"),
            })
            if not args.dry_run:
                client.call(f"delete/{args.domain}/{record['id']}")
        changes.append({
            "action": "create__dmarc",
            "name": f"_dmarc.{args.domain}",
            "content": args.dmarc,
            "ttl": "600",
        })
        if not args.dry_run:
            created = client.call(
                f"create/{args.domain}",
                {"type": "TXT", "name": "_dmarc", "content": args.dmarc, "ttl": "600"},
            )
            changes[-1]["id"] = created.get("id")

    print(json.dumps({"domain": args.domain, "dry_run": args.dry_run, "changed": bool(changes), "changes": changes}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
