# Handover — DPR spot check (from claude.ai chat, 11 Oct 2026)

Spec agreed with Martijn in the claude.ai Project "OPS Check Automation". This file is the source of truth for the spot-check work until it is folded into the Decisions Log. Start with **Next steps** at the bottom.

## Goal

Replace the manual spot check (2 SOVs + 3 CTVs, hand-picked) with the same checks for **every on-hire vessel**, every run (07:30, 11:00 recheck). The checks run alongside the four-stage chain, like DPR status (D2). Every failure is listed (D3).

## Checks per vessel type

| Check | SOV | CTV |
|---|---|---|
| DPR state | not filled / filled, not signed off / signed off by vessel / signed off by vessel + client | not filled / filled / filled + signed off (rare, sign-off not rolled out to CTV clients) |
| Weather overview present | yes | yes |
| Hs during transfers | yes | yes |
| Hs continuity | ignored outside transfers (wave radar often off while sailing in field) | gaps outside transfers listed as context on the same line, no extra tag |
| Missed transfers | yes | yes |
| Fuel measured vs entered | n/a for now | yes |
| Timeline | **\* placeholder** — output "timeline: not yet checked"; spec to follow (e.g. DP radius anomaly, Grampian Tweed 04/10) | **\* placeholder** — slip graph present on relevant timeline items; spec to follow |

### Fuel (CTV)

- **Measured** = DPR fuel details "Total fuel consumed (engine measurements)".
- **Entered** = sum of *Consumed* over all diesel rows in the DPR Consumption table (usually one row, "Fuel tank").
- Findings (each its own line under the vessel):
  1. |entered − measured| / measured **> 20%** → flag, with both values and the %. Base = measured.
  2. Entered missing (empty/0 while measured > 0); measured missing.
  3. **Missing leg**: transit from park = 0 while transit to park > 0 (or vice versa). If present, finding 1 notes "measured incomplete, % unreliable".
  4. **Breakdown ≠ total**: transit to + transit from + dockings + other ≠ total (small rounding tolerance).
- Multi-day trips (e.g. MO8 2–4 Oct): still flagged at 20%, with note "vessel did not return to port today, entered value may cover a multi-day dip".
- Waiting/port days (DPR `accessDayType`) are excluded from missing-leg checks.

### Weather / Hs

- DPR summary tab "Weather overview" graph: Wave Hs, Tp, Tz, direction, wind speed/direction, turbine transfers. Key values: **Hs and transfers** (clients see Hs over transfers).
- **Hs during transfers**: every turbine transfer and vessel-to-vessel transfer (and missed transfers, if they have start/end times) must have valid Hs within the transfer window. Tolerance = sample interval of the source. Hs = 0 or NaN counts as missing.
- Example line: `SC Onyx – Hs missing during 22 of 22 turbine transfers (08:05–16:45) @Team OPS`
- The graph shows its source (e.g. "Source: Hermess nowcast"); include it on the line as context. **Owner: Team OPS for all wave findings, whatever the source** (Martijn, 11 Oct; revises the earlier source-based split).

### Missed transfers

- Crew adds them on the transfers page ("Add missed transfer"): location, two HH:mm fields (start/end?), pax ↑↓, cargo ↑↓, comment dropdown, comment text. Listed separately from auto-logged transfers, included in totals.
- Any saved row (non-empty) → finding, listing each row:
  ```
  SC Onyx – 2 missed transfers logged @Team OPS
    • WTG A07, 09:12–09:40, pax 3↑ 0↓, "Not detected by system"
  ```
- Candidate sources (verify): `SOV_MIST_TRANSFERS` (SOV), a missed-transfers collection in `bmo_dataviewer` (CTV), and the new portal's analytics vessel operations collection with an operation type (turbine / V2V / missed). Prefer the single new-portal source if it holds all three.

## Teams tags (revises D22)

Findings @mention a Teams tag on the vessel's line, with the reason. If a tag mention can't be posted via the Workflows webhook Adaptive Card, fall back to bold text "Team OPS" / "Team Data" (or post via Graph). Tag IDs needed for both tags. No person names.

| Tag | Findings |
|---|---|
| **Team Data** | No raw data anywhere; stuck at mover (FTP_VESSEL_DATA); stuck at processing (Raw_vessel_data2); processed but not loaded into MongoDB; data integrity |
| **Team OPS** | All fuel findings (20% margin, missing leg, entered/measured missing, breakdown ≠ total); missed transfers logged; expected channel missing; weather overview empty; Hs missing during transfers (any source: onboard, Hermes, client feeds); coverage / flatline / ranges; system health |
| **No tag** | DPR not filled / not signed off; Known (daughtercraft) |

## Test set (known answers from manual checks)

| Vessel | DPR day | Expected |
|---|---|---|
| SC Onyx | 07/10 | Fuel entered 1,249 L vs measured 850.5 L (+47%) @Team OPS; breakdown 200.9+198.8+198+252.8 = 850.5 passes; both legs present; Hs missing (flat 0) during all turbine transfers 08:00–16:45 |
| NOS Bolder | 04/10 | Fuel 671 L measured vs 984 L entered (+47%) @Team OPS; DPR filled, not signed off; wave data not available |
| MO8 | 04/10 | Fuel 1,111.3 vs 2,456 L (+121%) @Team OPS with multi-day note; filled + signed off |
| Windcat 51 | 04/10 | Fuel 1,131.2 vs 1,250 L (+10%) pass; filled + signed off |
| Grampian Tweed | 04/10 | Signed off by vessel; wave OK; no missed transfers; timeline \* (DP radius anomaly) |
| Wavemaster Horizon | 04/10 | Not signed off; wave OK; no missed transfers |
| SC Amethyst | 03/10 | Fuel 938.8 vs 984 L (+5%) pass; filled, not signed off |
| Windcat 50 | 03/10 | Fuel 997.7 L measured, entered missing @Team OPS; DPR not filled |
| NOS Bringer | 03/10 | Fuel entered missing @Team OPS; wave OK |
| Grampian Kestrel | 03/10 | Signed off by vessel; wave OK; no missed transfers |

## Open questions

- ✅ Answered in Field mapping. Which MongoDB fields hold: SOV sign-off by vessel vs client; CTV sign-off; DPR fuel details (total + breakdown); Consumption table rows; weather/Hs series and its source; transfer windows (turbine, V2V); missed transfers. → `scripts/inspect_dpr.py` answers these.
- ✅ Hs sample interval per source: 15 min for all portal meteo sources.
- ✅ Missed-transfer time fields: start and end, CTV and SOV.
- Does a tag @mention render as a real notification when posted through the Workflows webhook?
- **Decisions Log numbering:** the 6 Oct chat added D29–D37 (spot check daily for all on-hire vessels, DPR judged at 11:00, day classification, fuel per trip + drift check, …), but the Project's log has a different D29 (MacBook launchd) and no D30–D37. Merge first, then add the tag decision under the next free number.

## Field mapping (inspect run, 11 Oct 2026)

Source: `scripts/inspect_dpr.py` (collection sampling) plus targeted read-only queries on the ten test vessel-days. Raw output in `out/inspect_dpr/2026-10-10/` (git-ignored). Dates are MATLAB datenums (`date`, `matlabDate`) unless noted. Portal = database `portal`, legacy = `bmo_dataviewer`.

| Check | Collection | Fields / rule |
|---|---|---|
| Vessel list, type | `bmo_dataviewer.vessels` | `onHire`, `operationsClass` (CTV / OSV); `portal.analyticsVessels.vesselType` agrees |
| DPR state CTV | `bmo_dataviewer.CTV_dprInput` {mmsi, date} | Filled = existing `dpr.py` rule. Sign-off: `signOff.signOffStatus` 0/1, `signOff.signedOffByVesselmaster`, `signedOffByClient`, `confirmedByClientRepresentative`, `refusedByClientRepresentative` (schemaVersion 5) |
| DPR state SOV | `bmo_dataviewer.SOV_dprInput` {mmsi, date} | `signedOff.amount`: 0 none, 1 vessel (`signedOffSkipper`), 2 vessel + client (`signedOffClient`). `SOV_hseDprInput.signedOff` is the separate HSE sign-off, out of scope |
| Fuel entered (CTV) | `CTV_dprInput.consumptionList[]` | Sum `consumed` where `type` = "Diesel" (case-insensitive). Row names vary ("Fuel tank", "Sc Amethyst"); water rows exist ("FW Tank" 671 L on MO8). Older schema: `consumption.fuel.used` |
| Fuel measured (CTV) | `portal.analyticsVesselEquipments` {mmsi, matlabDate, equipmentType "engine", engineId "combined"} | `fuelTotalM3` × 1000 = litres. Multiplier already applied (Windcat 51: 1.131 m³ = portal 1,131.2 L). 1 L resolution. Not `general.DPRstats.TotalFuel` / `analyticsGeneralOverviews.fuelTotalM3` (litres despite the name, and differ: SC Onyx 865.9 vs 850.5). No engine doc = measured missing (NOS Bringer, every day) |
| Fuel legs | `portal.analyticsDprTimeline` {mmsi, matlabDate} `events[]` | `event_type` "transit", detail "fuel usage" (L), details `from` / `to` / `port` / `field` to tell port→field from field→port. Names also include "Transit near X", "Transit from and to X", "Relocating near X" (not legs). All events' fuel usage sums to measured (SC Onyx 851.4 vs 851) |
| Fuel breakdown | ? | Portal showed 200.9 + 198.8 + 198 + 252.8 = 850.5. "Other" equals total − the rest, so the check is tautological if the portal computes it that way. Docking 198 ≠ timeline docking sum 239.2. **Need the portal DPR source** |
| Day type | `CTV_dprInput.accessDayType` (FULL_ACCESS, HALF_ACCESS_WEATHER, NOT_REQUIRED, …); `SOV_dprInput.accessDayType.status` ("Full access day", "Excluded day", …) | Full enum still to list |
| Wave source (CTV) | `operationsStats.waveSourceId` → `bmo_dataviewer.waveSources` (`provider`, `info`) and `portal.analyticsMeteoSources` (`dataviewerSourceId`, `dataSource`, `dataSourceDisplayName` = graph label "Hermess nowcast", `outputResolutionMinutes`) | All 32 portal sources are 15 min → tolerance 15 min. A source missing from `analyticsMeteoSources` has no portal series (NOS Bolder: Miros WTG11, `meteoAvailableCode` 0 → "wave data not available") |
| Wave source (SOV) | `operationsStats.waveSourceId` is empty for SOVs | Timeline detail "wave source" (Grampian Tweed: Hermess Doggerbank A); Wavemaster Horizon / Grampian Kestrel have none in the timeline but have per-transfer Hs → probably `SOV_general.weatherConditions` (`wavesource` e.g. "Furuno" = onboard radar). To confirm |
| Hs series | `portal.analyticsMeteoData` {sourceMongoId = analyticsMeteoSources._id, matlabDate} | `waveData.timeStamp[]`, `hs[]`, `tp[]`, `tz[]`, `waveDirectionDeg[]`, `meteoIsReliable[]`, 96 × 15 min. Legacy `bmo_dataviewer.waveData` {source, date}, 48 × 30 min |
| Hs per transfer | `analyticsVesselOperations.hs` | **Rounded to 0.1 m**: NOS Bringer 03/10 real Hs 0.02 m shows as 0. Don't use it for "Hs = 0 is missing"; use the series. Missing = no sample within ±15 min, None/NaN, or `meteoIsReliable` false; "flat 0" = exact 0.0 in the raw series |
| Transfer windows | `portal.analyticsVesselOperations` {mmsi, matlabDate} | `operationType` turbineTransfer / v2vTransfer, `operationStatus` (CTV: docking / tiedOff / impact / "transfer OK"; SOV: empty), `startTime` / `stopTime` UTC. One empty placeholder doc per day. **Incomplete for SC Onyx 07/10: 11 ops up to 11:10 vs 19 timeline dockings up to 14:46.** Timeline `events[]` with type "docking" / "turbine transfer" / "vessel2vessel transfer" is complete |
| Missed transfers CTV | `bmo_dataviewer.missedTransfers` {mmsi, date} `missedTransfers[]` | `location`, `paxIn/Out`, `cargoIn/Out`, `comment`, `otherComment`, `startTimeIso` / `stopTimeIso` (use these; `startTime.hours` is UTC) |
| Missed transfers SOV | `SOV_dprInput.missedPaxCargo[]` (turbine) and `SOV_vessel2vesselTransfers.missedTransfers[]` (V2V) | `location`, `from` / `to` {hour, minutes}, `paxIn/Out`, `cargoIn/Out`; no comment field. `SOV_missedTransfers` is empty. Not in `analyticsVesselOperations` (no "missed" type), so no single new-portal source |
| Timeline \* | `analyticsDprTimeline.events[]`; slip graph in `bmo_dataviewer.transfers.slipGraph` / `analyticsVesselOperations.transferGraph` | For the placeholder spec |

### Test set: corrected days

Confirmed by Martijn (11 Oct): a check always covers the **previous day's** data (processing cycle), so the run on 11/10 checks DPR day 10/10. The 03/04 test-set dates were check dates; the DPR day is one day earlier. SC Onyx 07/10 is already a DPR day (06/10 doesn't match: 762 vs 1,186 L). Values as in the DB now:

| Vessel | Handover day | DPR day | DB now |
|---|---|---|---|
| SC Onyx | 07/10 | 07/10 | Entered 1,249 / measured 851 (+47%) ✓. Both legs (200.7 / 198.8). Hs in DB now valid 0.82–0.90 m (Hermess Horns Rev 1, series rewritten 10/10 22:00), so the flat 0 isn't reproducible from today's DB |
| NOS Bolder | 04/10 | 03/10 | Measured 671 ✓; entered **794** (confirmed in the portal by Martijn; 984 in the original test set was wrong) → +18%, **passes**. Not signed off ✓. Miros WTG11, no portal series ✓ |
| MO8 | 04/10 | 03/10 | 1,111 vs 2,456 (+121%) ✓; signed off ✓. 02/10 has the same 2,456 entered |
| Windcat 51 | 04/10 | 03/10 | 1,131 vs 1,250 (+10.5%) ✓; signed off ✓ |
| Grampian Tweed | 04/10 | 03/10 | Signed off by vessel ✓; Hs present; no missed |
| Wavemaster Horizon | 04/10 | 03/10 | Not signed off ✓ ("Excluded day"); Hs present; no missed |
| SC Amethyst | 03/10 | 02/10 | 939 vs 984 (+5%) ✓; not signed off ✓ |
| Windcat 50 | 03/10 | 02/10 | Measured 998 ✓. DPR now filled + signed off, last modified 04/10 11:27 (after the check) |
| NOS Bringer | 03/10 | 02/10 | Entered now 405, modified 03/10 11:17 (after the check). **No measured fuel at all** → also "measured missing". Hs OK (0.2–0.3 m) |
| Grampian Kestrel | 03/10 | 02/10 | Now signed off by vessel + client; client signature added 03/10 11:42 (after the check) |

**Final-values rule (Martijn, 11 Oct):** two runs per day, early morning and late morning (11:00). The late-morning run is final: whatever the DPR and data say then is the verdict. Edits made after it are not followed up; a change to a signed-off DPR only goes through a client change request. So the tests use **frozen fixtures** holding the documents as they were at the 11:00 run (e.g. Windcat 50 02/10: DPR not filled at 11:00, filled at 11:27 → finding stands), not the live DB.

Extra cases seen: NOS Bringer 03/10 (2 missed CTV transfers, Hs 0.02 m real); Grampian Kestrel 04/10 (15 rows in `missedPaxCargo`); Grampian Tweed 01/10 (2 V2V transfers without Hs).

### Implementation choices (11 Oct, provisional, all constants at the top of `spot.py`)

- **Final verdict = the 11:00 run**; edits after it are not followed up (Martijn).
- **Wave findings always Team OPS** (Martijn). Line names the source.
- Wave series: the source named by `operationsStats.waveSourceId` or the timeline (Hermess, client feeds) → portal `analyticsMeteoData` (15 min). SOVs without a named source use their onboard radar series `SOV_general.weatherConditions` (Furuno, Miros, Nextocean; 15 min). Martijn: some SOVs use external sources like Hermess, others their own radar.
- Wave data is checked **every day, port and idle days included**, CTV and SOV (Martijn). No numeric Hs at all → "Weather overview empty"; a flat-0 series with transfers → "Hs missing (flat 0) during N of M transfers".
- Transfers for the Hs check: timeline `docking`, `turbine transfer`, `platform transfer`, V2V events and missed transfers with times. Not `tiedOff`, `impact`, `waiting tied off` (open question 4).
- Legs: transit events with `from` ≠ `to`, port side = a port seen that day or not a field. No port at all that day → multi-day note.
- Missing-leg check skipped on: NOT_REQUIRED, OFF_CONTRACT, WEATHER_DAY, MAINTENANCE, TECHNICAL; SOV: Excluded / No access / Adverse weather / Port call days.
- Entered / measured missing below **50 L** is a **warning** (⚠, `Severity.SUSPECT`), from 50 L a fail. Martijn: rather flag slightly more than less.
- **No DPR record** for the day = the crew hasn't saved that day's DPR at all (every vessel without one on 10/10 had DPR history). Listed as "DPR not created, last DPR dd/mm", no tag.
- Breakdown check = fuel on the timeline **more** than measured, tolerance max(2 %, 5 L). Less is normal: fuel burnt in port (engines on, cruising inside the port geofence, by design) is in no timeline event (Martijn, 11 Oct). The portal's four-part split isn't checked: "other" is the remainder, so it always adds up.
- **Times are UTC in the data** (confirmed: the portal converts to each user's timezone setting). SOV missed-transfer `from`/`to` are read as UTC; the Teams message shows them in `TIMEZONE` (Europe/Amsterdam).

### Message format (Martijn, 11 Oct)

One block per vessel that needs attention: the vessel line with each team @mentioned once, then a bullet for **every** problem (owner in brackets when two teams are involved). Vessels without problems are left out; info lines (timeline placeholder, Hs gaps context) aren't bullets. `spot.render_vessel` / `spot.render_report`. Example:

```
NOS Bringer · @Team Data @Team OPS
  • Route 02:06–05:52 ends before harbour arrival 11:25, data 07:37–11:25 (Team Data)
  • Fuel measured missing (entered 120 L) (Team OPS)
  • DPR not filled
```

10/10 (run Sunday evening): 44 of 47 vessels, 129 lines; most blocks are "DPR not created" + a wave line.

### New findings from the first live run (10/10, 47 vessels)

- **VF API wave sources are broken in the portal**: `analyticsMeteoData` for NBHZZ2 holds 3 zero samples (02–03/10) or all-NaN (05–06/10), while legacy `waveData` has valid Hs (~0.5 m). Affects Windcat 50/51, Cwind, NOS P-boats. Shown as "Hs missing (flat 0)" / "Weather overview empty".
- `analyticsVesselOperations` misses transfers (SC Onyx 07/10: 11 of 19), so the timeline is used.
- 10/10 output after the changes above: ~70 lines for 47 vessels (29 DPR, 38 wave, 13 fuel). Many wave lines repeat the VF API defect and vessels without data that day, so the message may want grouping. Windcat 41: 150 L entered vs 12 L measured (+1150 %) – small measured values make % large; consider an absolute floor too.

### Portal route / timeline time-base problem (found 11 Oct, NOS Proceeder 10/10)

Martijn: NOS Proceeder 10/10 shows 8 transfers in the transfers log and 4 turbines on the map, but the timeline only has a transit out and port.

- **Raw data is complete:** `timeTraces` and portal `analyticsTimeSeries`, all signals incl. GPS (101–104): 06:56–16:35 UTC, 580 samples.
- **Transfers are complete:** `bmo_dataviewer.transfers` and `analyticsVesselOperations`: 8 operations 07:18–15:57.
- **Portal route** (`analyticsGeneralOverviews.route.timeStamps`, also `general.time`): **00:18–09:58**, the same ~9 h 40 min span **shifted 6 h 38 min earlier**. Harbour departure / arrival in the same document are right (06:56 / 16:35).
- **Timeline** (`analyticsDprTimeline`, and legacy `dprTimeline`, both written 11/10 03:11): port 06:55, transit Aberdeen–AOWF 06:56–07:11, then nothing, although `endOfDay` = 16:36. Looks like the timeline is built along the shifted route and stops where it ends.
- Same on NOS Proceeder 05–10/10 every day (route ends 08:53–10:50, arrival 16:35–17:26). Also a level-3 ops-stats issue "VBB time sync error of 14–15 minutes" every day (probably unrelated: 15 min ≠ 6 h 38 min).
- SC Onyx 07/10 fits: route 00:03–10:54 vs real 04:57–15:50, and only 11 of 19 operations in `analyticsVesselOperations` (cut at 11:10).
- **Fleet:** last 14 days, **226 of 401** vessel-days with a route have it end > 1 h before the detected harbour arrival (nearly all CTVs; Windcats look shifted by ~2–3 h, NOS boats by more). **12 of 247** CTV vessel-days with dockings have fewer dockings on the timeline than in the transfers log (NOS Proceeder 07–10/10 zero; Hydrocat 48 30/09 10 of 22; MHO Esbjerg 04/10 4 of 11; NOS Piloter 06/10 and NR Rebellion 03/10 no timeline).
- Owner: processing (Team Data). Not yet confirmed by them; the "shift" is an inference from equal span and sample spacing.
- **Implemented (11 Oct), all @Team Data**, first content of the timeline check: (a) `timeline_transfers`: logged transfers (CTV `transfers` docking/impact, SOV `SOV_turbineTransfers`) with no overlapping timeline event (± 5 min); (b) `timeline_end`: last timeline event ends > 60 min before `endOfDay`; (c) `route_end`: route ends > 60 min before harbour arrival. **Martijn: (c) stays a per-vessel line until Team Data says the route is fine; then we update the check.** Still placeholder: DP radius / slip graph.

### Open questions from the mapping

1. ✅ Date shift confirmed. NOS Bolder 03/10 entered = 794 L (confirmed), fuel passes.
2. ✅ Fuel breakdown: only timeline > measured is flagged (port fuel explains timeline < measured).
3. ✅ Team OPS owns all wave data findings for now.
4. Do tiedOff and impact operations count as transfers for the Hs check?
5. Transfer list: take it from the timeline (complete) rather than `analyticsVesselOperations` (SC Onyx gap)? And is that gap itself a Team Data finding?

## Next steps (for the Cowork session)

1. ✅ (11 Oct) Save `inspect_dpr.py` to `scripts/`. Run it with `MONGO_ATLAS_URI` set: `python scripts/inspect_dpr.py` (defaults to yesterday's DPR day; read-only, 20 s per query).
   Run notes: the client now uses `datetime_conversion="DATETIME_AUTO"` (the portal has year-0 dates that crashed BSON decoding). The test-case and coverage passes are too slow to be useful (OR'ed vessel/date keys can't use indexes, every query waits out the 20 s limit; stopped after 2 of 10 vessels). The mapping below came from targeted indexed queries instead.
2. ✅ (11 Oct, see Field mapping) Read `out/inspect_dpr/<date>/summary.md` and `testcases.md`; map each check above to its collection and fields. Write the mapping into this file.
3. ✅ (11 Oct) Checks in `src/ops_check/checks/spot.py` (pure: `SpotInput` → DPR state + findings with `tag`), MongoDB loader in `src/ops_check/sources/spot_mongo.py` (batched, 47 vessels in < 1 s). `Finding.tag` (`Tag.OPS` / `Tag.DATA` / None) added to `models.py`. **Not yet wired into `run.py` / the Teams message** (next step: add spot findings per vessel line, dedupe with the chain's DPR lists, tag @mention or bold fallback).
4. ✅ (11 Oct) `tests/test_spot.py`: the ten vessel-days (corrected DPR days) at their 11:00 state + extra cases + rule unit tests; 66/66 pass with the existing suite. Fixtures in `tests/fixtures/spot/` via `scripts/make_spot_fixtures.py` (addresses and remarks redacted).
4b. ✅ (11 Oct) **Pipeline + spot check in one run and one card.** `run.execute` runs the spot check after the chain (`spot_check()`), stores spot issues and per-vessel blocks in the run file (`run["spot"]`), and posts one card from `report/spot_card.render_ops_card` (old `report/card.render_card` no longer posted; text message and priority alerts unchanged). Card: header counts · PIPELINE (always visible, rows per diagnosis, @Team Data; data issue @Team OPS) · NEW since previous run (07:30 vs yesterday's final run, 11:00 vs 07:30; ⚠ warnings and issues joining an open root cause are folded) · ROOT CAUSES · BY TOPIC (fuel, missed transfers, DPR incl. not signed off) · all vessels per vessel (pipeline line first) · Known / Clean / warnings / off-hire footer. Vessels whose data isn't in MongoDB (offline, stuck, not loaded, in transit) only keep the DPR line; Known vessels get no spot lines. Mock for 10/10: 65 on hire, 6 pipeline, 49 spot, 9 clean, 6 known, ~8 s. 67/67 tests pass.
   Open: the 11:00 recheck can't post as a thread reply through the Workflows webhook (needs Graph); for now it's its own card with the diff since 07:30. Teams tag ids still needed. ✅ On-hire test entries SOV_example, Test_BMO, Test_BMO2 added to the `config/vessels.toml` exclude list (11 Oct); card footer shows the excluded count.
4c. ✅ (11 Oct) **Transit vessels** (Bloemgracht, Brouwersgracht, Xin Yao Hua; `operationsClass` "transit"). Martijn: their DPR is a Summary tab with shipping projects (name, start, optional end, origin → destination) and a Transit Details tab with motion sensors, MRU and virtual vMRU. Check: **continuous vMRU data until at least 23:59 UTC on the data day; no data → Team OPS.**
   - Data: `bmo_dataviewer.timeTraces`, `stream.sensorId` starting `vMRU_` (e.g. `vMRU_jviHq3ZyNLWsviXE3yqLC_70.000_0.000_10.000` = id + position), 5 signals (231–233 acc X/Y/Z max, 244/245 roll/pitch max), one trace per 15 min stamped 00:14:59 … 23:45:00 UTC (95 per complete day). Projects: `DPR_shippingprojects` (mmsi, projectName, startDate, endDate, origin.name, destination.name).
   - Rules (`check_transit`): no vMRU trace on the day → "No vMRU data for dd/mm, last data …" @Team OPS. Per sensor: last trace before 23:45 UTC (unless data already runs into today) = "ends hh:mm"; first after 00:20 = "starts"; step > 20 min = gap; a vMRU seen in the 7 days before but not today = "missing". No active project = ⚠ warning. Times in UTC (the check is on the UTC day). **Assumption:** the 23:45 trace covers 23:45–23:59.
   - Card: own section "Transit vessels (vMRU)" @Team OPS. A transit vessel that's offline shows there, not as a pipeline row; stuck at mover / processing stays in the pipeline for Team Data.
   - 09–10/10: Bloemgracht (1 vMRU) and Xin Yao Hua (4) complete; Brouwersgracht no data since 24/09 23:45 UTC. 73/73 tests pass.
4d. ⏳ **TODO – Offshore Waddenzee (priority vessel) is excluded for now** (11 Oct, Martijn: "one of the weird ones"). Its registry `vesselname` is `Waddenzee`, and no folder under that name exists on FTP_VESSEL_DATA, Raw_vessel_data2 or vessel_data6, so it would show as offline every day. To do: find where its data actually lands, add a `[folders]` alias in `config/vessels.toml`, take it off the `exclude` list. Until then it's not checked at all (not even its DPR).
5. Merge D29–D37 from the 6 Oct log with the tag decision; update the Decisions Log.
