"""Jurisdiction seed registry for the regulatory research pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json

from .paths import regulatory_data_dir

DATA_DIR = regulatory_data_dir()
JURISDICTIONS_PATH = DATA_DIR / "jurisdictions.json"


STATE_SEEDS: list[tuple[str, str, list[str]]] = [
    ("AL", "Alabama", ["sos.alabama.gov"]),
    ("AK", "Alaska", ["corporations.alaska.gov"]),
    ("AZ", "Arizona", ["azcc.gov", "azleg.gov"]),
    ("AR", "Arkansas", ["sos.arkansas.gov"]),
    ("CA", "California", ["bizfileonline.sos.ca.gov", "sos.ca.gov", "ftb.ca.gov"]),
    ("CO", "Colorado", ["sos.state.co.us"]),
    ("CT", "Connecticut", ["business.ct.gov"]),
    ("DE", "Delaware", ["corp.delaware.gov"]),
    ("DC", "District of Columbia", ["dlcp.dc.gov", "dcra.dc.gov"]),
    ("FL", "Florida", ["dos.fl.gov", "sunbiz.org"]),
    ("GA", "Georgia", ["sos.ga.gov"]),
    ("HI", "Hawaii", ["cca.hawaii.gov"]),
    ("ID", "Idaho", ["sosbiz.idaho.gov", "sos.idaho.gov"]),
    ("IL", "Illinois", ["ilsos.gov"]),
    ("IN", "Indiana", ["inbiz.in.gov", "in.gov/sos"]),
    ("IA", "Iowa", ["sos.iowa.gov"]),
    ("KS", "Kansas", ["sos.ks.gov"]),
    ("KY", "Kentucky", ["sos.ky.gov"]),
    ("LA", "Louisiana", ["sos.la.gov"]),
    ("ME", "Maine", ["maine.gov/sos"]),
    ("MD", "Maryland", ["egov.maryland.gov", "dat.maryland.gov"]),
    ("MA", "Massachusetts", ["sec.state.ma.us"]),
    ("MI", "Michigan", ["michigan.gov/lara"]),
    ("MN", "Minnesota", ["sos.state.mn.us"]),
    ("MS", "Mississippi", ["sos.ms.gov"]),
    ("MO", "Missouri", ["sos.mo.gov"]),
    ("MT", "Montana", ["sosmt.gov"]),
    ("NE", "Nebraska", ["sos.nebraska.gov"]),
    ("NV", "Nevada", ["nvsilverflume.gov", "sos.nv.gov"]),
    ("NH", "New Hampshire", ["sos.nh.gov"]),
    ("NJ", "New Jersey", ["business.nj.gov", "njportal.com"]),
    ("NM", "New Mexico", ["sos.nm.gov"]),
    ("NY", "New York", ["dos.ny.gov", "businessexpress.ny.gov"]),
    ("NC", "North Carolina", ["sosnc.gov"]),
    ("ND", "North Dakota", ["sos.nd.gov"]),
    ("OH", "Ohio", ["ohiosos.gov"]),
    ("OK", "Oklahoma", ["sos.ok.gov"]),
    ("OR", "Oregon", ["sos.oregon.gov"]),
    ("PA", "Pennsylvania", ["pa.gov", "file.dos.pa.gov"]),
    ("RI", "Rhode Island", ["business.sos.ri.gov"]),
    ("SC", "South Carolina", ["sos.sc.gov"]),
    ("SD", "South Dakota", ["sdsos.gov"]),
    ("TN", "Tennessee", ["sos.tn.gov"]),
    ("TX", "Texas", ["sos.state.tx.us", "direct.sos.state.tx.us", "comptroller.texas.gov"]),
    ("UT", "Utah", ["corporations.utah.gov"]),
    ("VT", "Vermont", ["sos.vermont.gov"]),
    ("VA", "Virginia", ["scc.virginia.gov"]),
    ("WA", "Washington", ["sos.wa.gov", "dor.wa.gov"]),
    ("WV", "West Virginia", ["business4.wv.gov", "sos.wv.gov"]),
    ("WI", "Wisconsin", ["dfi.wi.gov"]),
    ("WY", "Wyoming", ["wyobiz.wyo.gov", "sos.wyo.gov"]),
]

TOP_PRIORITY_STATES = ["TX", "CA", "FL", "DE", "NV", "AZ", "NY", "WY", "GA", "IL"]


@dataclass
class Jurisdiction:
    jurisdiction_id: str
    name: str
    level: str
    state: str
    parent_id: str | None = None
    official_domains: list[str] = field(default_factory=list)
    filing_authority: str = ""
    portal_type: str = "unknown"
    priority: int = 999
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_state_registry() -> list[Jurisdiction]:
    jurisdictions: list[Jurisdiction] = []
    for index, (state, name, domains) in enumerate(STATE_SEEDS, start=1):
        priority = 100 + index
        if state in TOP_PRIORITY_STATES:
            priority = TOP_PRIORITY_STATES.index(state) + 1
        jurisdictions.append(
            Jurisdiction(
                jurisdiction_id=f"{state.lower()}_state",
                name=name,
                level="state",
                state=state,
                official_domains=domains,
                filing_authority="Secretary of State or equivalent business filing office",
                portal_type="to_research",
                priority=priority,
            )
        )
    return sorted(jurisdictions, key=lambda item: (item.priority, item.state))


def write_state_registry(path: Path = JURISDICTIONS_PATH) -> list[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "coverage_goal": "All U.S. states plus District of Columbia; county and city children are discovered by research batches.",
        "jurisdictions": [item.to_dict() for item in build_state_registry()],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload["jurisdictions"]


def load_registry(path: Path = JURISDICTIONS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return write_state_registry(path)
    return json.loads(path.read_text()).get("jurisdictions", [])
