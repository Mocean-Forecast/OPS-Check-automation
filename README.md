# OPS-Check-automation

Automated replacement for the daily ops check and spot check. For every vessel and every DPR day the
service walks the data through four stages, stops at the first that fails, and posts the result to the
**Operations Check** Teams channel.

Design: `docs/` and the claude.ai project **OPS Check Automation** (Draft Proposal, Decisions Log D1–D26,
Portal Check source review).

```
1 Registry   expected to be quiet?   (vessels collection: onHire, opsStatus + expiry)  -> Known
2 Raw        archive arrived?        (FTP_VESSEL_DATA → Raw_vessel_data2)              -> Offline / Stuck at mover
3 Processed  improveddata<day>.mat + MongoDB record                                    -> Not processed / Not loaded
4 Plausible  complete and plausible? (all checks run, every failure listed)            -> Data issue
   alongside: DPR status (never stops the chain)
```

## Status – phase 1 scaffold

| Part | State |
|---|---|
| Registry from the existing `vessels` fields (D13, D25): `vesselname` = folder, `onHire`, `isDaughterCraft`, `lastModified` = sync time (D14); known cases + priority in `config/vessels.toml` | done – `vesselname` = folder name still to confirm |
| Share scan: landing, staging, processed, backlog, unknown folders (D15, D16) | done |
| MongoDB reads ported from Sjoerd's portal-api (D10, D11) | done – untested against the live DB |
| Four-stage chain + DPR side check (D1–D4) | done |
| Stage 4: transit MRU gap (ported) | done – other stage 4 checks are phase 2 |
| Teams Adaptive Card, 11:00 diff with colour/bold (D6), priority alerts (D8) | done |
| Watchdog 07:45 / 11:15 (D7) | done |
| Run storage (JSON per run, audit trail) | done – MongoDB results collection later |
| LLM context notes (D20), Spire liveness (D19), system health | not started |
| Forecast freshness via metocean-api (D27) | v2 – needs vessel → forecast-area mapping |

## Run

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # fill in Mongo URI, share paths, webhook

pytest                           # tests, no DB or shares needed

# dry run on the synthetic sample (prints the message, posts nothing)
ops-check run --slot morning --fixture tests/fixtures/sample_day.json --dry-run --now 2026-10-05T07:30
ops-check run --slot recheck --fixture tests/fixtures/sample_day.json --dry-run --now 2026-10-05T12:00

# live
ops-check run --slot morning
ops-check run --slot recheck
ops-check watchdog --slot morning
```

## Schedule (D5, D7) – Europe/Amsterdam

```cron
30 7  * * *  cd /opt/ops-check && .venv/bin/ops-check run --slot morning
45 7  * * *  cd /opt/ops-check && .venv/bin/ops-check watchdog --slot morning
0  11 * * *  cd /opt/ops-check && .venv/bin/ops-check run --slot recheck
15 11 * * *  cd /opt/ops-check && .venv/bin/ops-check watchdog --slot recheck
```

## Layout

```
src/ops_check/
  config.py           settings from .env
  registry_config.py  config/vessels.toml: known cases + priority vessels
  models.py           Vessel, Evidence, Finding, Diagnosis, RunReport
  datenum.py          MATLAB datenum <-> date
  sources/mongo.py    MongoDB reads (read-only)
  sources/files.py    share scan (names + timestamps only)
  collect.py          evidence per vessel + run warnings
  checks/chain.py     the four-stage chain
  checks/dpr.py       DPR status rules
  report/message.py   deterministic message model + plain text
  report/card.py      Adaptive Card rendering
  report/diff.py      07:30 vs 11:00 comparison
  notify/teams.py     Workflow webhook post
  store.py            run storage
  run.py, cli.py      orchestration and CLI
```
