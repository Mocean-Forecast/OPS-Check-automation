# Current OPS Check Tool ("Portal Check Dashboard") — Analysis & Reuse Assessment

_Analysed 2026-10-05 by Claude for Martijn. Source: live app at http://192.168.100.250:5173 (frontend) and http://192.168.100.250:3001 (backend). No source repo was available; findings come from the production JS bundle and live API responses._

---

## 1. TL;DR

- The current tool is a **manual, human-in-the-loop dashboard**: an Express API computes one status per vessel per day, and a React UI lets an operator browse vessels, filter, open the Portal/VesselFinder links, log "issues" and copy a plain-text report.
- It checks **two things only**: (a) does the vessel have *processed operations stats* for the day, and (b) what is the *DPR* (Daily Progress Report) state. A vessel is flagged **"issues" purely when there's no processed data**. DPR state is informational only.
- **Raw-data checks, MRU-gap checks and portal visualisation checks are not implemented** (enum values exist in the UI but never come back from the API; the UI even falls back to "Coming Soon").
- Nothing runs on a schedule and nothing notifies — someone has to open the page.
- **Highly reusable**: the vessel/fleet resolution query (49 active vessels, MMSI, client, operations class), the status vocabulary, the portal deep-link format, the text-report generator (≈ ready-made Teams message body) and the issue/known-issue store (for suppressing alerts on known problems).

---

## 2. Architecture

| Layer | Tech | Location |
|---|---|---|
| Frontend | React + TypeScript, Vite build, Tailwind (+ tailwind-merge), lucide icons | `192.168.100.250:5173` (static build, `vite preview`-style) |
| Backend | Node.js / **Express** ("Portal Check API") | `192.168.100.250:3001` |
| Vessel data | MongoDB (ids are 24-hex ObjectIds, e.g. `652e4fc311697824daf63792`) — most likely the portal's own database | — |
| Issue store | Separate store with UUID ids (likely JSON file or own collection) | — |

The API base URL is **hard-coded** in the bundle: `const ay = "http://192.168.100.250:3001"`.

---

## 3. Backend API (observed)

| Method | Endpoint | Purpose | Notes |
|---|---|---|---|
| GET | `/health` | Liveness | `{"status":"OK","timestamp":…,"service":"Portal Check API"}` |
| GET | `/api/vessels?date=YYYY-MM-DD` | Per-vessel status for a day | Without `date` → defaults to **yesterday**. Returns `success, targetDate, count, data[], debug{}` |
| GET | `/api/vessels/:id` | Single vessel | **Broken** — returns 404 "Vessel not found" for valid ids |
| GET | `/api/issues` | Open issues | `{success, issues[], count}` |
| GET | `/api/issues/history` | Resolved issues | same shape |
| POST | `/api/issues` | Create issue | body `{vesselId, vesselName, title, createdBy}` |
| POST | `/api/issues/:id/comments` | Add comment | body `{text, author}` |
| PATCH | `/api/issues/:id/resolve` | Resolve | body `{resolvedBy}` |
| DELETE | `/api/issues/:id` | Delete | no confirmation in UI |

Unknown routes return `{"success":false,"error":"Endpoint not found","path":…}`. **No authentication** on any endpoint, including writes.

### 3.1 Vessel record (example)

```json
{
  "id": "652e4fc311697824daf63792",
  "name": "NOS Bringer",
  "clientNames": ["Vattenfall"],
  "date": "2026-10-04",
  "portalUrl": "https://portal.mo4.online/reports/dpr;mmsi=219016663?date=740259",
  "vesselFinderUrl": "https://www.vesselfinder.com/nl/vessels/details/219016663",
  "mmsi": 219016663,
  "operationsClass": "CTV",            // CTV | OSV | transit
  "dprStatus": "dpr-available-not-filled",
  "dataStatus": "no-data",
  "status": "issues",                  // good | issues
  "hasOperationsStats": false, "operationsStatsCount": 0,
  "hasCtvDprData": true,  "ctvDprDataCount": 1
}
```

`debug` block (for 2026-10-04): `mmsiCount 49, statsCount 36, ctvMmsiCount 37, ctvDprCount 18, osvMmsiCount 9, sovDprCount 2`.

### 3.2 Status logic (reverse-engineered from live data)

| Field | Values | Derived from |
|---|---|---|
| `dataStatus` | `processed-data-available`, `no-data` (observed); `raw-data-available`, `partial-mru-gap` (defined, **never returned**) | Presence of an **operations-stats** document for the vessel/day |
| `dprStatus` | `no-dpr-available`, `dpr-available-not-filled`, `dpr-filled`, `dpr-signed-off` | CTV DPR collection (CTVs) / SOV DPR collection (OSVs; "signed-off" only seen on OSVs) |
| `status` | `good` / `issues` | **`issues` ⇔ `hasOperationsStats == false`**. DPR state does not affect it (e.g. "Offshore Waddenzee": DPR not filled + processed data → `good`) |

Snapshot 2026-10-04: 49 vessels, 36 good, 13 issues (all 13 = no processed data).

### 3.3 Useful details
- **Portal date param is a MATLAB `datenum`** — `740259` = 2026-10-04 (`date.toordinal() + 366` in Python). Needed for any portal deep links / portal visualisation checks.
- Portal DPR deep link: `https://portal.mo4.online/reports/dpr;mmsi=<MMSI>?date=<datenum>`.
- Fleet includes a placeholder MMSI `999999998` (Crowley Barge) — the AIS/VesselFinder link is meaningless for it; exclude such vessels from AIS-based checks.

---

## 4. Frontend features

**Header:** "Portal Check Dashboard", tabs *Vessels / Issues / History* with counters, *Copy Report* button, user picker.

**Vessels tab**
- Date picker (prev/next day, calendar, "Today"); defaults to yesterday.
- Summary: "X vessels OK / Y vessels with issues (showing n of m)".
- Filters: Vessel Type (operationsClass), DPR Status, Data Status — with counts.
- Vessel card: name, MMSI, "Issue" badge if an open issue exists, client names, links to Portal + VesselFinder, expandable DPR/Data status, *Report Issue* modal.

**Issues / History tabs** — open and resolved issues with comments, relative timestamps, resolve/delete.

**Users** — hard-coded list (Chris, Sjoerd, Daan, Erwin, Cees, Sven, Livia, Martijn, Ewout, Mark, Mai), stored in `localStorage` key `portal-check-current-user`. No auth, attribution is honour-based.

**Copy Report** — builds this text and copies to clipboard:
```
PORTAL CHECK STATUS REPORT
Generated: <date> at <time>
Total Vessels: N
Vessels OK: N
Vessels with Issues: N

VESSELS WITH ISSUES (n):
========================================
• <Name> (<Clients>) - <Data status> [MMSI: …]

TRANSIT VESSELS STATUS (n):
========================================
✓/⚠ <Name> (<Clients>) - <Data status> [MMSI: …]
```
This is effectively what is posted manually to the *Operations Check* Teams channel today.

---

## 5. Gaps vs. the project goals

| Project goal | Current tool | Gap |
|---|---|---|
| Run several times a day automatically | On-demand page | Needs scheduler (cron / worker) |
| Check **raw data** | Not implemented | New check against raw-data store |
| Check **processed data** | Yes — presence only (count ≥ 1) | Add completeness/quality: coverage %, MRU gaps, stale/frozen values, out-of-range values |
| Check **portal visualisation** | Only a link for a human to click | Need automated portal check (API or headless browser, e.g. Playwright) |
| DPR checks | Status reported, not alerted | Decide alert rules (e.g. "available-not-filled" after X hours) |
| **Teams notification** | Manual copy/paste | Teams webhook / Power Automate / Graph API |
| Anomaly detection | Binary yes/no | Thresholds + trend vs. previous days |
| Known-issue handling | Manual "issues" | Use open issues to suppress/annotate repeat alerts |

---

## 6. Bugs & risks found

1. **Issues API uses a relative URL** (`/api/issues`) while vessels use the absolute `:3001` base — the Issues/History tabs only work if the frontend server proxies `/api` to 3001. Needs verifying.
2. **`GET /api/vessels/:id` returns 404** for valid vessel ids.
3. **UTC date handling** — the frontend uses `toISOString().split("T")[0]` for "yesterday" and day navigation; around midnight in NL (UTC+1/+2) this can select the wrong day.
4. **Status ignores DPR** and only checks *presence* of stats — a vessel with one tiny stats record is "good".
5. **No auth** on write endpoints; anyone on the LAN can create/resolve/delete issues. Delete has no confirmation.
6. Hard-coded API host and user list — must be rebuilt for any move.
7. "Coming Soon"/unused enum values (`raw-data-available`, `partial-mru-gap`) suggest started-but-unfinished work.

---

## 7. What to reuse

**Reuse as-is / port directly**
- **Fleet resolution logic** in the backend (which vessels are active, MMSI, client names, operations class CTV/OSV/transit). This is the hardest-to-rediscover piece — get the backend source.
- **Status vocabulary** (`dataStatus`, `dprStatus`, `status`) — extend rather than replace, so the old dashboard and the new automation speak the same language.
- **Mongo queries** behind `statsCount`, `ctvDprCount`, `sovDprCount` — the processed-data and DPR checks already exist here.
- **Portal deep-link + MATLAB datenum conversion**.
- **Copy Report generator** → template for the Teams message (wrap it in an Adaptive Card).
- **Issue store** → becomes the "known issues / suppression list": if a vessel has an open issue, the alert can say "known issue: …" instead of re-alerting.
- `/health` pattern for monitoring the automation itself.

**Reuse with changes**
- `/api/vessels?date=` as the first check module in a scheduled pipeline (call it, diff against previous run, alert on new `issues`).
- The React dashboard can stay as the human UI on top of the new results (add result history and check type columns).

**Rebuild / new**
- Scheduler + check runner (raw data, processed-data quality, portal visualisation).
- Teams notifier.
- Run history / results store (so trends and "new since last run" are possible).
- Auth (at least for write endpoints).

---

## 8. Suggested target shape

```
[Scheduler, e.g. 06:00 / 10:00 / 14:00 / 18:00]
        │
        ▼
[Check runner] ──► Fleet resolver (reused from Portal Check API)
        │      ──► Check: raw data present & fresh            (new)
        │      ──► Check: processed data present + quality     (reused + extended)
        │      ──► Check: DPR state vs. time-of-day rules      (reused + rules)
        │      ──► Check: portal page renders / values sane    (new, Playwright)
        │      ──► Check: AIS position via VesselFinder        (cf. ops-check skill)
        ▼
[Results store]  ── suppression via open issues (reused issue store)
        ▼
[Teams notifier]  – only on new/changed findings; daily summary using Copy Report format
        ▼
[Dashboard] – existing React UI extended with run history
```

---

## 9. Open questions / next steps

1. Where is the source repo for the Portal Check API + dashboard, and who owns it (Chris?) — needed to lift the Mongo queries.
2. What is the raw-data source (blob storage / vessel sync files?) and what does "partial MRU gap" mean in terms of data (gap threshold)?
3. Is there a portal API to query, or must visualisation checks go through the UI?
4. Teams delivery: incoming webhook vs. Power Automate workflow vs. Graph — and which channel (Operations Check?).
5. Alert rules: which DPR states at what time count as an alert? Which vessels are excluded (dry-dock, dead-ship, placeholder MMSI)?
6. Verify whether the Issues tab works through the 5173 server (relative `/api/issues`).
