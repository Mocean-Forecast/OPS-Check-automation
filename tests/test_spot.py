"""DPR spot check: the ten vessel-days from the manual checks (handover test set, DPR days
corrected to check day - 1) plus unit tests for the rules.

Fixtures (tests/fixtures/spot, made by scripts/make_spot_fixtures.py) hold the documents
as they are now. Each case applies the state at the 11:00 run on top, because the 11:00 run
is the final verdict (Martijn, 11 Oct): later DPR edits and nowcast rewrites don't count.
"""

import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ops_check.checks import spot
from ops_check.checks.spot import MissedTransfer, SpotInput, WaveSeries, run_spot
from ops_check.models import Tag

TZ = ZoneInfo("Europe/Amsterdam")
FIX = Path(__file__).parent / "fixtures" / "spot"


def load(name: str, day: str) -> SpotInput:
    d = json.loads((FIX / f"{name.replace(' ', '_')}_{day}.json").read_text())
    return SpotInput.from_json(d["input"])


def run(name: str, day: str, at_11=None):
    inp = load(name, day)
    if at_11:
        at_11(inp)
    res = run_spot(name, inp, date.fromisoformat(day), TZ)
    return res, {f.check.removeprefix("spot_"): f for f in res.findings}


def flagged(found) -> set[str]:
    return {k for k in found if k not in ("timeline", "hs_gaps")}


# ------------------------------------------------------------------ 11:00 states

def flat_zero_hs(inp: SpotInput) -> None:
    """SC Onyx 07/10: the weather overview showed Hs flat 0 at the check (nowcast rewritten 10/10)."""
    inp.wave.samples = [(t, 0.0, r) for t, _, r in inp.wave.samples]


def dpr_not_filled(inp: SpotInput) -> None:
    """Windcat 50 02/10: DPR filled + signed 04/10 11:27, after the run."""
    inp.dpr.update(accessDayType="", remarks="", signOff={"signOffStatus": 0})
    for r in inp.dpr["consumptionList"]:
        r["consumed"] = 0


def entered_missing(inp: SpotInput) -> None:
    """NOS Bringer 02/10: fuel entered 03/10 11:17, after the run."""
    for r in inp.dpr["consumptionList"]:
        r["consumed"] = 0


def vessel_only(inp: SpotInput) -> None:
    """Grampian Kestrel 02/10: client signature added 03/10 11:42, after the run."""
    inp.dpr["signedOff"] = {"amount": 1, "signedOffSkipper": "redacted", "signedOffClient": ""}


# ------------------------------------------------------------------ the test set

def test_sc_onyx_0710():
    res, f = run("SC Onyx", "2026-10-07", flat_zero_hs)
    assert flagged(f) == {"fuel_margin", "hs_missing_transfers", "route_end"}
    m = f["fuel_margin"]
    assert (m.details["entered"], m.details["measured"], m.details["pct"]) == (1249, 851, 46.8)
    assert m.tag == Tag.OPS and "unreliable" not in m.message  # both legs present
    hs = f["hs_missing_transfers"]
    assert hs.details["missing"] == hs.details["transfers"] == 19
    assert "19 of 19 turbine transfers (08:02–16:46)" in hs.message and "Hermess nowcast" in hs.message
    assert hs.tag == Tag.OPS
    assert res.dpr_state == spot.CTV_FILLED


def test_nos_bolder_0310():
    res, f = run("NOS Bolder", "2026-10-03")
    assert flagged(f) == {"weather_empty", "route_end"}  # 671 vs 794 L = +18%: passes
    assert "not available" in f["weather_empty"].message and "Miros" in f["weather_empty"].message
    assert res.dpr_state == spot.CTV_FILLED


def test_mo8_0310_multi_day():
    res, f = run("MO8", "2026-10-03")
    assert flagged(f) == {"fuel_margin"}
    m = f["fuel_margin"]
    assert (m.details["entered"], m.details["measured"]) == (2456, 1111)
    assert m.details["pct"] == pytest.approx(121.1, abs=0.1)
    assert "did not return to port" in m.message
    assert res.dpr_state == spot.CTV_SIGNED


def test_windcat_51_0310():
    res, f = run("Windcat 51", "2026-10-03")
    assert "fuel_margin" not in f  # 1,131 vs 1,250 L = +10.5%
    assert res.dpr_state == spot.CTV_SIGNED
    # Not in the manual check: the portal's VF API series (NBHZZ2) holds 3 zero samples that day.
    assert flagged(f) == {"hs_missing_transfers", "route_end"}
    assert "(flat 0)" in f["hs_missing_transfers"].message and "VF API" in f["hs_missing_transfers"].message


def test_grampian_tweed_0310():
    res, f = run("Grampian Tweed", "2026-10-03")
    assert res.dpr_state == spot.SIGNED_VESSEL
    assert flagged(f) == set()
    assert f["timeline"].message == "timeline: DP radius / slip graph not yet checked"


def test_wavemaster_horizon_0310():
    res, f = run("Wavemaster Horizon", "2026-10-03")
    assert res.dpr_state == spot.FILLED
    assert flagged(f) == {"dpr"}
    assert f["dpr"].message == "DPR not signed off" and f["dpr"].tag is None


def test_sc_amethyst_0210():
    res, f = run("SC Amethyst", "2026-10-02")
    assert flagged(f) == {"route_end"}  # 939 vs 984 L = +5%
    assert res.dpr_state == spot.CTV_FILLED


def test_windcat_50_0210():
    res, f = run("Windcat 50", "2026-10-02", dpr_not_filled)
    assert res.dpr_state == spot.NOT_FILLED
    assert f["dpr"].tag is None
    assert f["fuel_entered_missing"].details["measured"] == 998 and f["fuel_entered_missing"].tag == Tag.OPS
    assert flagged(f) == {"dpr", "fuel_entered_missing", "hs_missing_transfers", "route_end"}


def test_nos_bringer_0210():
    res, f = run("NOS Bringer", "2026-10-02", entered_missing)
    assert flagged(f) == {"fuel_missing", "route_end"}  # no engine data either
    assert f["fuel_missing"].tag == Tag.OPS


def test_grampian_kestrel_0210():
    res, f = run("Grampian Kestrel", "2026-10-02", vessel_only)
    assert res.dpr_state == spot.SIGNED_VESSEL
    assert flagged(f) == set()


# ------------------------------------------------------------------ extra cases

def test_nos_bringer_0310_missed_and_calm_sea():
    res, f = run("NOS Bringer", "2026-10-03")
    m = f["missed_transfers"]
    assert m.message == "2 missed transfers logged" and m.tag == Tag.OPS
    assert m.details["rows"][0] == 'E3, 09:42–09:57, pax 3↑ 0↓, cargo 1↑ 0↓, "Transfer OK"'
    assert "hs_missing_transfers" not in f  # real Hs 0.02 m (portal rounds it to 0)


def test_grampian_kestrel_0410_missed_rows():
    _, f = run("Grampian Kestrel", "2026-10-04")
    assert f["missed_transfers"].message == "15 missed transfers logged"


def test_nos_proceeder_1010_timeline_and_route():
    """Martijn, 11 Oct: 8 transfers in the log, 4 turbines on the map, timeline = transit + port.
    Raw data 06:56-16:35 UTC; route 00:18-09:58 (shifted); timeline stops 07:11."""
    _, f = run("NOS Proceeder", "2026-10-10")
    assert f["timeline_transfers"].details == {"missing": 7, "logged": 7}  # 7 dockings + 1 tied-off
    assert f["timeline_transfers"].tag == Tag.DATA
    assert f["timeline_end"].message == "Timeline ends 09:11, data runs to 18:36"
    assert f["route_end"].message == ("Route 02:18–11:58 ends before harbour arrival 18:35, "
                                      "data 08:55–18:36")
    assert all(f[k].tag == Tag.DATA for k in ("timeline_transfers", "timeline_end", "route_end"))


def test_complete_timeline_is_quiet():
    _, f = run("Grampian Tweed", "2026-10-03")
    assert not {"timeline_transfers", "timeline_end", "route_end"} & set(f)


# ------------------------------------------------------------------ unit rules

T0 = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)


def ev(t, name, start_min, stop_min, **details):
    return {"event_type": t, "event_name": name, "details": details,
            "start_time": (T0 + timedelta(minutes=start_min)).isoformat(),
            "end_time": (T0 + timedelta(minutes=stop_min)).isoformat()}


def ctv(entered, measured, events=(), access="FULL_ACCESS") -> SpotInput:
    return SpotInput("CTV", {"accessDayType": access, "remarks": "",
                             "consumptionList": [{"type": "Diesel", "consumed": entered}]},
                     measured, list(events))


TRIP = [ev("port", "Port A", 0, 1, port="A"),
        ev("transit", "Transit A - F", 1, 60, **{"from": "A", "to": "F", "fuel usage": 200}),
        ev("docking", "Turbine transfer at T1", 70, 80, field="F", **{"fuel usage": 400}),
        ev("transit", "Transit F - A", 600, 660, **{"from": "F", "to": "A", "fuel usage": 0})]


def test_missing_leg_marks_margin_unreliable():
    res = run_spot("X", ctv(1000, 600, TRIP), date(2026, 10, 7), TZ)
    f = {x.check: x for x in res.findings}
    assert "spot_fuel_missing_leg" in f
    assert "measured incomplete, % unreliable" in f["spot_fuel_margin"].message


def test_missing_leg_skipped_on_weather_day():
    res = run_spot("X", ctv(600, 600, TRIP, access="WEATHER_DAY"), date(2026, 10, 7), TZ)
    assert "spot_fuel_missing_leg" not in {x.check for x in res.findings}


def test_margin_boundary_is_strictly_above_20pct():
    trip = [TRIP[0], TRIP[1], TRIP[2], ev("transit", "Transit F - A", 600, 660, **{"from": "F", "to": "A", "fuel usage": 100})]
    checks = lambda e: {x.check for x in run_spot("X", ctv(e, 700, trip), date(2026, 10, 7), TZ).findings}  # noqa: E731
    assert "spot_fuel_margin" not in checks(840)   # +20.0%
    assert "spot_fuel_margin" in checks(841)


def test_breakdown_mismatch():
    res = run_spot("X", ctv(600, 600, TRIP[:3] + [ev("transit", "Transit F - A", 600, 660,
                                                    **{"from": "F", "to": "A", "fuel usage": 100})]),
                   date(2026, 10, 7), TZ)
    assert {x.check for x in res.findings} >= {"spot_fuel_breakdown"}  # 700 L on timeline vs 600 L total


def test_port_fuel_below_measured_is_fine():
    res = run_spot("X", ctv(600, 900, TRIP[:3] + [ev("transit", "Transit F - A", 600, 660,
                                                    **{"from": "F", "to": "A", "fuel usage": 100})]),
                   date(2026, 10, 7), TZ)
    assert "spot_fuel_breakdown" not in {x.check for x in res.findings}  # 700 L on timeline, 900 L measured


def wave(points, res=15):
    return WaveSeries("Test", None, res, [(T0 + timedelta(minutes=m), h, True) for m, h in points])


def test_hs_tolerance_is_one_sample_interval():
    inp = SpotInput("OSV", {"signedOff": {"amount": 1}}, timeline=[ev("turbine transfer", "T1", 100, 110)])
    inp.wave = wave([(0, 1.0), (125, 1.0)])         # nearest sample 15 min after the window
    assert "spot_hs_missing_transfers" not in {x.check for x in run_spot("X", inp, date(2026, 10, 7), TZ).findings}
    inp.wave = wave([(0, 1.0), (126, 1.0)])         # 16 min: missing
    assert "spot_hs_missing_transfers" in {x.check for x in run_spot("X", inp, date(2026, 10, 7), TZ).findings}


@pytest.mark.parametrize("hs,missing", [(0.0, True), (float("nan"), True), (None, True), (0.02, False)])
def test_hs_zero_nan_missing(hs, missing):
    inp = SpotInput("OSV", {"signedOff": {"amount": 1}}, timeline=[ev("turbine transfer", "T1", 100, 110)])
    inp.wave = wave([(105, hs), (0, 1.0)])
    got = "spot_hs_missing_transfers" in {x.check for x in run_spot("X", inp, date(2026, 10, 7), TZ).findings}
    assert got is missing


def test_ctv_gaps_are_context_without_extra_tag():
    tl = [ev("transit", "Transit A - F", 0, 60, **{"from": "A", "to": "F"}), ev("docking", "T1", 60, 70)]
    inp = SpotInput("CTV", None, timeline=tl, wave=wave([(60, 1.0), (75, 1.0)]))
    f = [x for x in run_spot("X", inp, date(2026, 10, 7), TZ).findings if x.check == "spot_hs_gaps"]
    assert f and f[0].tag is None and "gaps outside transfers: 08:00–" in f[0].message


@pytest.mark.parametrize("signed,state", [
    ({"amount": 0}, spot.NOT_FILLED), ({"amount": 1}, spot.SIGNED_VESSEL),
    ({"amount": 2, "signedOffClient": "x"}, spot.SIGNED_BOTH)])
def test_sov_dpr_states(signed, state):
    assert spot.dpr_state("OSV", {"signedOff": signed, "remarks": "", "accessDayType": {"status": ""}}) == state


def test_render_puts_tag_last_and_lists_rows():
    inp = SpotInput("OSV", {"signedOff": {"amount": 1}},
                    missed=[MissedTransfer("WTG A07", T0, T0 + timedelta(minutes=28), 3, 0, comment="Not detected")])
    lines = spot.render_lines("SC Onyx", run_spot("SC Onyx", inp, date(2026, 10, 7), TZ))
    i = next(i for i, x in enumerate(lines) if "missed" in x)
    assert lines[i:i + 2] == ["SC Onyx – 1 missed transfer logged @Team OPS",
                         '  • WTG A07, 08:00–08:28, pax 3↑ 0↓, "Not detected"']


def test_no_dpr_record_is_listed_with_last_dpr_day():
    inp = SpotInput("CTV", None, last_dpr_day="2026-10-08")
    f = {x.check: x for x in run_spot("X", inp, date(2026, 10, 10), TZ).findings}
    assert f["spot_dpr"].message == "DPR not created, last DPR 08/10" and f["spot_dpr"].tag is None


def test_small_fuel_gap_is_a_warning():
    res = run_spot("X", ctv(0, 37, []), date(2026, 10, 10), TZ)
    f = {x.check: x for x in res.findings}
    assert f["spot_fuel_entered_missing"].severity.value == "suspect"
    assert "X – ⚠ Fuel entered missing (measured 37 L) @Team OPS" in spot.render_lines("X", res)


def test_wave_checked_on_idle_day():
    inp = SpotInput("CTV", {"accessDayType": "NOT_REQUIRED", "remarks": ""})
    assert "spot_weather_empty" in {x.check for x in run_spot("X", inp, date(2026, 10, 10), TZ).findings}


def test_grouped_block_per_vessel():
    _, f = run("NOS Proceeder", "2026-10-10")
    res, _ = run("NOS Proceeder", "2026-10-10")
    lines = spot.render_vessel("NOS Proceeder", res)
    assert lines[0] == "NOS Proceeder · @Team Data @Team OPS"
    assert lines[1].startswith("  • Timeline misses 7 logged transfers") and lines[1].endswith("(Team Data)")
    assert all(l.startswith("  • ") for l in lines[1:])
    assert not any("not yet checked" in l for l in lines)


def test_quiet_vessel_is_left_out():
    res, _ = run("Grampian Kestrel", "2026-10-02", vessel_only)
    assert spot.render_vessel("Grampian Kestrel", res) == []


# ------------------------------------------------------------------ transit vessels (vMRU)

def test_transit_complete_days_are_quiet():
    for name in ("Bloemgracht", "Xin Yao Hua"):
        res, f = run(name, "2026-10-10")
        assert flagged(f) == set(), name
    res, _ = run("Xin Yao Hua", "2026-10-10")
    assert res.dpr_state == "project Inch Cape project (Zhuhai → Leith UK)"


def test_transit_without_data_notifies_ops():
    _, f = run("Brouwersgracht", "2026-10-10")
    assert f["transit_no_vmru"].message == "No vMRU data for 10/10, last data 24/09 23:45 UTC"
    assert f["transit_no_vmru"].tag == Tag.OPS


def _transit(stamps_by_sensor, expected=(), project=True):
    return SpotInput("transit", vmru=stamps_by_sensor, vmru_expected=list(expected),
                     project={"name": "P", "origin": "A", "destination": "B"} if project else None)


DAY0 = datetime(2026, 10, 10, tzinfo=timezone.utc)
FULL = [DAY0 + timedelta(minutes=15 * i - 1 / 60 if i == 1 else 15 * i) for i in range(1, 96)]  # 00:14:59 … 23:45
S1 = "vMRU_jviHq3ZyNLWsviXE3yqLC_70.000_0.000_10.000"
S2 = "vMRU_Cz5-83iwsT4MhxkzK2w70_133.733_0.000_19.810"


def tchecks(inp):
    return {x.check: x for x in run_spot("X", inp, date(2026, 10, 10), TZ).findings}


def test_transit_full_day_passes():
    assert tchecks(_transit({S1: FULL})) == {}


def test_transit_ends_early_and_gap():
    stamps = [t for t in FULL if not (DAY0 + timedelta(hours=4) < t < DAY0 + timedelta(hours=6))]
    stamps = [t for t in stamps if t <= DAY0 + timedelta(hours=21, minutes=30)]
    m = tchecks(_transit({S1: stamps}))["spot_transit_vmru"].message
    assert m == "vMRU data not continuous to 23:59 UTC: vMRU jviHq3 (70, 0, 10) ends 21:30 UTC; gaps 04:00–06:00"


def test_transit_data_of_today_counts():
    stamps = FULL[:-1] + [DAY0 + timedelta(days=1, minutes=30)]  # 23:45 missing, but data runs into today
    assert "spot_transit_vmru" not in tchecks(_transit({S1: stamps}))


def test_transit_missing_sensor_and_project():
    f = tchecks(_transit({S1: FULL}, expected=[S1, S2], project=False))
    assert "vMRU Cz5-83 (133.733, 0, 19.81) missing" in f["spot_transit_vmru"].message
    assert f["spot_transit_project"].severity.value == "suspect"
