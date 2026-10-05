"""Core data model: vessels, evidence collected per vessel-day, findings and results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class Stage(int, Enum):
    REGISTRY = 1
    RAW = 2
    PROCESSED = 3
    PLAUSIBLE = 4
    SIDE = 0  # checks that run alongside the chain (D2): DPR, forecast, registry hygiene


class Diagnosis(str, Enum):
    """Outcome of the four-stage chain for one vessel-day (Mark's diagram + D15)."""

    KNOWN = "known"                    # stage 1: expected to be quiet
    OFFLINE = "offline"                # stage 2: nothing arrived
    IN_TRANSIT = "in_transit"          # archive on its way, not yet late
    STUCK_MOVER = "stuck_mover"        # stage 2/3: archive stuck in FTP_VESSEL_DATA
    STUCK_PROCESSING = "stuck_processing"  # stage 3: archive still in Raw_vessel_data2 after deadline
    NOT_LOADED = "not_loaded"          # stage 3: .mat exists, nothing in MongoDB
    DATA_ISSUE = "data_issue"          # stage 4: processed but incomplete / implausible
    OK = "ok"

    @property
    def needs_action(self) -> bool:
        return self in {
            Diagnosis.OFFLINE,
            Diagnosis.STUCK_MOVER,
            Diagnosis.STUCK_PROCESSING,
            Diagnosis.NOT_LOADED,
            Diagnosis.DATA_ISSUE,
        }


class Severity(str, Enum):
    INFO = "info"
    SUSPECT = "suspect"
    FAIL = "fail"


class Owner(str, Enum):
    """Stored per finding, not shown in the post yet (D22)."""

    OPS = "ops"
    DEV = "dev"
    ACCOUNT_MANAGER = "account_manager"
    REGISTRY = "registry"  # Martijn: expired / unparseable registry entries


@dataclass
class Vessel:
    id: str
    mmsi: int | None
    name: str
    system: str | None
    operations_class: str | None
    client: str | None
    on_hire: bool
    status: str | None = None
    status_reason: str | None = None
    status_owner: str | None = None
    status_expiry: date | None = None
    priority: bool = False
    clients: list[str] = field(default_factory=list)
    daughtercraft: bool = False
    site: str | None = None  # forecast area for v2 (D27)

    @property
    def key(self) -> str:
        return self.system or (str(self.mmsi) if self.mmsi else self.id)


@dataclass
class FileInfo:
    path: str
    day: date
    modified: datetime  # timezone-aware
    size: int = 0


@dataclass
class DprInfo:
    status: str  # no-dpr-available | dpr-available-not-filled | dpr-filled | dpr-signed-off


@dataclass
class Evidence:
    """Everything collected for one vessel on one data day, before any checks run."""

    landing_archives: list[FileInfo] = field(default_factory=list)   # FTP_VESSEL_DATA
    staging_archives: list[FileInfo] = field(default_factory=list)   # Raw_vessel_data2
    processed_file: FileInfo | None = None                           # vessel_data6/.../improveddata<day>.mat
    backlog_days: list[date] = field(default_factory=list)           # older days still in an inbox
    folder_found: bool = True                                        # vessel has a folder on the shares
    db_processed: bool = False                                       # general / SOV_general / timeTraces present
    has_recorded_code: int | None = None                             # operationsStats.hasRecordedCode
    transit_max_gap_min: float | None = None
    transit_samples: int | None = None
    dpr: DprInfo | None = None
    clients: list[str] = field(default_factory=list)


@dataclass
class Finding:
    vessel: str
    day: date
    stage: Stage
    check: str
    severity: Severity
    owner: Owner
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.vessel}|{self.check}"


@dataclass
class VesselResult:
    vessel: Vessel
    day: date
    diagnosis: Diagnosis
    findings: list[Finding] = field(default_factory=list)
    dpr_status: str | None = None

    @property
    def headline(self) -> str:
        """The first failing finding's message, used as the one-line summary."""
        for f in self.findings:
            if f.stage != Stage.SIDE and f.severity != Severity.INFO:
                return f.message
        return self.diagnosis.value


@dataclass
class RunReport:
    run_at: datetime
    slot: str            # "morning" | "recheck"
    data_day: date
    results: list[VesselResult]
    warnings: list[str] = field(default_factory=list)  # registry stale, unknown folders, ...
    check_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        def conv(o: Any) -> Any:
            if isinstance(o, Enum):
                return o.value
            if isinstance(o, (date, datetime)):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: conv(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [conv(v) for v in o]
            return o

        return conv(asdict(self))
