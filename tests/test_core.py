from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ops_check.checks import dpr
from ops_check.checks.chain import evaluate
from ops_check.config import Settings
from ops_check.datenum import from_datenum, to_datenum
from ops_check.models import Diagnosis, DprInfo, Evidence, FileInfo, Vessel
from ops_check.report.card import render_card
from ops_check.report.diff import Change, diff_runs
from ops_check.report.message import build_message
from ops_check.run import FixtureSources, execute
from ops_check.sources.files import scan_shares

TZ = ZoneInfo("Europe/Amsterdam")
DAY = date(2026, 10, 4)
MORNING = datetime(2026, 10, 5, 7, 30, tzinfo=TZ)
RECHECK = datetime(2026, 10, 5, 11, 0, tzinfo=TZ)
FIXTURE = Path(__file__).parent / "fixtures" / "sample_day.json"


def vessel(**kw) -> Vessel:
    base = dict(id="x", mmsi=1, name="Test", system="TEST", operations_class="CTV", client="C", on_hire=True)
    base.update(kw)
    return Vessel(**base)


def archive(hours_before: float, now: datetime = MORNING) -> FileInfo:
    return FileInfo("TEST_2026-10-04_1.7z.001", DAY, now - timedelta(hours=hours_before))


S = Settings()


# ---------------------------------------------------------------- datenum

def test_datenum_matches_portal():
    assert to_datenum(DAY) == 740259  # seen in the live portal link for 2026-10-04
    assert from_datenum(740259.5) == datetime(2026, 10, 4, 12, 0)


# ---------------------------------------------------------------- DPR rules (ported)

@pytest.mark.parametrize("doc,expected", [
    (None, dpr.NO_DPR),
    ({"accessDayType": "", "remarks": ""}, dpr.NOT_FILLED),
    ({"accessDayType": "", "remarks": "ok"}, dpr.FILLED),
])
def test_ctv_dpr(doc, expected):
    assert dpr.dpr_status("CTV", doc) == expected


@pytest.mark.parametrize("doc,expected", [
    (None, dpr.NO_DPR),
    ({"signedOff": {"amount": 1}}, dpr.SIGNED_OFF),
    ({"signedOff": {"amount": 0}, "accessDayType": {"status": "x"}}, dpr.FILLED),
    ({"signedOff": {"amount": 0}, "accessDayType": {"status": ""}, "remarks": ""}, dpr.NOT_FILLED),
])
def test_sov_dpr(doc, expected):
    assert dpr.dpr_status("OSV", doc) == expected


def test_transit_has_no_dpr():
    assert dpr.dpr_status("transit", {"remarks": "x"}) == dpr.NO_DPR


# ---------------------------------------------------------------- chain: stage 1

def test_off_hire_is_known():
    assert evaluate(vessel(on_hire=False), Evidence(), DAY, MORNING, S).diagnosis == Diagnosis.KNOWN


def test_known_status_not_expired():
    v = vessel(status="demob", status_expiry=date(2026, 12, 31))
    assert evaluate(v, Evidence(), DAY, MORNING, S).diagnosis == Diagnosis.KNOWN


def test_expired_status_is_checked_again():
    v = vessel(status="DC", status_expiry=date(2026, 9, 30))
    r = evaluate(v, Evidence(), DAY, MORNING, S)
    assert r.diagnosis == Diagnosis.OFFLINE
    assert any(f.check == "registry_expired" for f in r.findings)


def test_unknown_status_flagged_and_checked():
    r = evaluate(vessel(status="on holiday"), Evidence(), DAY, MORNING, S)
    assert r.diagnosis == Diagnosis.OFFLINE
    assert any(f.check == "registry_unparseable" for f in r.findings)


# ---------------------------------------------------------------- chain: stages 2–3 (D15)

def test_nothing_anywhere_is_offline():
    assert evaluate(vessel(), Evidence(), DAY, MORNING, S).diagnosis == Diagnosis.OFFLINE


def test_fresh_archive_in_landing_is_in_transit():
    ev = Evidence(landing_archives=[archive(2)])
    assert evaluate(vessel(), ev, DAY, MORNING, S).diagnosis == Diagnosis.IN_TRANSIT


def test_old_archive_in_landing_is_stuck_at_mover():
    ev = Evidence(landing_archives=[archive(S.mover_stuck_hours + 1)])
    assert evaluate(vessel(), ev, DAY, MORNING, S).diagnosis == Diagnosis.STUCK_MOVER


def test_staging_archive_waits_until_deadline():
    assert evaluate(vessel(), Evidence(staging_archives=[archive(3)]), DAY, MORNING, S).diagnosis \
        == Diagnosis.IN_TRANSIT
    ev = Evidence(staging_archives=[archive(3, RECHECK)])
    assert evaluate(vessel(), ev, DAY, RECHECK, S).diagnosis == Diagnosis.STUCK_PROCESSING


def test_processed_file_without_db_is_not_loaded():
    ev = Evidence(processed_file=archive(1))
    assert evaluate(vessel(), ev, DAY, MORNING, S).diagnosis == Diagnosis.NOT_LOADED


def test_processed_and_loaded_is_ok():
    ev = Evidence(processed_file=archive(1), db_processed=True)
    assert evaluate(vessel(), ev, DAY, MORNING, S).diagnosis == Diagnosis.OK


def test_backlog_makes_ok_vessel_actionable():
    ev = Evidence(processed_file=archive(1), db_processed=True, backlog_days=[date(2026, 9, 26)])
    r = evaluate(vessel(), ev, DAY, MORNING, S)
    assert r.diagnosis == Diagnosis.STUCK_PROCESSING
    assert "day 9" in next(f.message for f in r.findings if f.check == "backlog")


# ---------------------------------------------------------------- chain: stage 4 + side (D2, D3)

def test_transit_gap_is_data_issue():
    v = vessel(operations_class="transit")
    ev = Evidence(processed_file=archive(1), db_processed=True, transit_max_gap_min=95, transit_samples=900)
    assert evaluate(v, ev, DAY, MORNING, S).diagnosis == Diagnosis.DATA_ISSUE


def test_dpr_runs_alongside_offline():
    r = evaluate(vessel(), Evidence(dpr=DprInfo(dpr.NOT_FILLED)), DAY, MORNING, S)
    assert r.diagnosis == Diagnosis.OFFLINE
    assert r.dpr_status == dpr.NOT_FILLED


# ---------------------------------------------------------------- file scan

def test_scan_shares(tmp_path):
    land, stage, proc = tmp_path / "FTP_VESSEL_DATA", tmp_path / "Raw_vessel_data2", tmp_path / "vessel_data6"
    (land / "HST_Hudson" / "quarantaine").mkdir(parents=True)
    (land / "HST_Hudson" / "HST Hudson_2026-10-04_1.7z.001").write_bytes(b"x")        # space in prefix
    (land / "HST_Hudson" / "HST Hudson_2026-09-01_1.7z.001").write_bytes(b"x")        # outside lookback
    (land / "HST_Hudson" / "quarantaine" / "HST Hudson_2026-10-03_1.7z.001").write_bytes(b"x")
    (land / "Mystery_Boat").mkdir()
    (land / "Mystery_Boat" / "Mystery Boat_2026-10-04_1.7z.001").write_bytes(b"x")
    (stage / "windcat_41" / "2026-10-04" / "AISreader").mkdir(parents=True)          # lower case folder
    (stage / "windcat_41" / "tempUnzip").mkdir()
    (stage / "#recycle").mkdir()
    (proc / "Windcat_41" / "processeddata").mkdir(parents=True)
    (proc / "Windcat_41" / "processeddata" / "improveddata2026-10-04.mat").write_bytes(b"x")

    scan = scan_shares(land, stage, proc, {"HST_Hudson": ["HST_Hudson"], "Windcat_41": ["Windcat_41"]}, DAY)
    hst, wc = scan.get("HST_Hudson"), scan.get("Windcat_41")
    assert [a.day for a in hst.landing] == [DAY]           # quarantaine + old archive ignored
    assert DAY in wc.staging_days and DAY in wc.processed  # matched case-insensitively
    assert wc.shares == {"staging", "processed"}
    assert scan.unknown_active == ["Mystery_Boat"]


def test_scan_reports_missing_share(tmp_path):
    scan = scan_shares(tmp_path / "nope", None, None, {}, DAY)
    assert scan.missing_roots == [str(tmp_path / "nope")]


def test_unpacked_day_waits_then_stuck():
    ev = Evidence(staging_day=archive(3))
    assert evaluate(vessel(), ev, DAY, MORNING, S).diagnosis == Diagnosis.IN_TRANSIT
    ev = Evidence(staging_day=archive(5, RECHECK))
    assert evaluate(vessel(), ev, DAY, RECHECK, S).diagnosis == Diagnosis.STUCK_PROCESSING


# ---------------------------------------------------------------- end to end on the fixture

def _run(tmp_path, slot, now):
    s = Settings(results_dir=tmp_path, vessels_config=FIXTURE.parent / "vessels.toml")
    return execute(s, FixtureSources(FIXTURE, s), slot, now=now, post=False)


def test_fixture_morning(tmp_path):
    out = _run(tmp_path, "morning", MORNING)
    diag = {r["vessel"]["name"]: r["diagnosis"] for r in out.run["results"]}
    assert "MO4 test rig" not in diag  # D25: client MO4 excluded
    assert diag == {
        "Windcat 41": "ok", "NOS Bringer": "offline", "HST Hudson": "stuck_mover",
        "Bloemgracht": "data_issue", "Grampian Tyne": "ok", "Offshore Waddenzee": "in_transit",
        "Bokalift 2": "known", "Grace Darling": "not_loaded",
        "Alison Kay": "offline", "MO8": "ok",
        "Alison Mantripp": "known",   # isDaughterCraft (MATLAB array value)
    }  # off-hire (Rachel Parsons, Aberdaron Bay, Old Timer) not evaluated at all
    assert out.run["warnings"] and any("Windcat Dorothea" in w for w in out.run["warnings"])
    assert any("Old_Vessel_X" in w for w in out.run["warnings"])  # archives arriving, no vessel
    assert not any("MO4-70" in w for w in out.run["warnings"])
    assert len(out.priority_cards) == 1  # Bloemgracht (Waddenzee only in transit at 07:30)
    assert out.card["type"] == "AdaptiveCard"
    # off-hire vessels are counted, not listed
    assert "11 on hire · 9 checked · 5 need action · 2 known" in out.text
    assert out.text.rstrip().endswith("Off-hire, not checked: 3")
    assert "Aberdaron" not in out.text and "Rachel Parsons" not in out.text
    assert not any("Rachel_Parsons" in w or "Aberdaron_Bay" in w for w in out.run["warnings"])


def test_fixture_recheck_marks_changes(tmp_path):
    _run(tmp_path, "morning", MORNING)
    out = _run(tmp_path, "recheck", datetime(2026, 10, 5, 12, 0, tzinfo=TZ))
    assert "[NEW] Offshore Waddenzee" in out.text
    new = [b for b in out.card["body"] if b.get("text", "").startswith("NEW · ")]
    assert new and new[0]["color"] == "Attention" and new[0]["weight"] == "Bolder"


def test_diff_resolved():
    def run(diags):
        return {"results": [{"vessel": {"system": k, "id": k}, "diagnosis": d} for k, d in diags.items()]}
    d = diff_runs(run({"A": "offline", "B": "offline"}), run({"A": "ok", "B": "not_loaded", "C": "offline"}))
    assert d.change("A") == Change.RESOLVED
    assert d.change("B") == Change.CHANGED
    assert d.change("C") == Change.NEW


# ---------------------------------------------------------------- vessels documents (real field names)

def test_vessel_from_real_doc():
    from ops_check.sources.mongo import registry_synced_at, vessel_from_doc
    doc = {"_id": "652e4fc311697824daf63761", "mmsi": 235080215, "nicename": "Aberdaron Bay",
           "vesselname": "Aberdaron_Bay", "operationsClass": "CTV", "client": ["SSE", "MO4"],
           "onHire": 0, "isDaughterCraft": "1", "Site": "Greater Gabbard North-East",
           "lastModified": 1790857203,
           "mothercraft_mmsi": {"_ArrayType_": "double", "_ArraySize_": [0, 0]}}
    v = vessel_from_doc(doc, S)
    assert (v.system, v.on_hire, v.daughtercraft, v.site) == ("Aberdaron_Bay", False, True,
                                                              "Greater Gabbard North-East")
    assert v.clients == ["SSE", "MO4"]
    assert registry_synced_at([doc], S) == datetime(2026, 10, 1, 12, 20, 3, tzinfo=timezone.utc)
