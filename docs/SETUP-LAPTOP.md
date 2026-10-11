# Laptop setup: shares, test runs, daily schedule

Phase 1 runs on Martijn's MacBook (D5, D29). Replace `RO_USER` and `SERVER` with the read-only
account and the file server. Run everything from the repo root:

```bash
cd ~/Programming/OPS-Check-automation
source .venv/bin/activate
```

## 1. Code and tests

```bash
pip install -e ".[dev]"     # picks up the new modules; no new dependencies
pytest -q                   # expect: 73 passed
```

## 2. Mount the three shares with the read-only account

The check expects `/Volumes/FTP_VESSEL_DATA`, `/Volumes/Raw_vessel_data2` and `/Volumes/vessel_data6`.

1. See what's mounted now:
   ```bash
   mount | grep -i smbfs
   ```
   If a share is already mounted with **your own** account, unmount it first. Otherwise the second
   mount lands on `/Volumes/<share>-1` and the check reads your account's mount:
   ```bash
   diskutil unmount /Volumes/FTP_VESSEL_DATA     # same for the other two if needed
   ```
2. Mount each share with the read-only account. A dialog asks for the password: enter it and tick
   **Remember this password in my keychain** (the scheduled runs use the Keychain):
   ```bash
   osascript -e 'mount volume "smb://RO_USER@SERVER/FTP_VESSEL_DATA"'
   osascript -e 'mount volume "smb://RO_USER@SERVER/Raw_vessel_data2"'
   osascript -e 'mount volume "smb://RO_USER@SERVER/vessel_data6"'
   ```
3. Check the mounts point at the right folders and use the read-only user:
   ```bash
   ls -d /Volumes/FTP_VESSEL_DATA /Volumes/Raw_vessel_data2 /Volumes/vessel_data6
   mount | grep -i smbfs          # each line should show //RO_USER@SERVER/...
   ```
4. Prove it's read-only. This must **fail** (Permission denied / Read-only file system):
   ```bash
   touch /Volumes/FTP_VESSEL_DATA/.ops_rw_test
   ```
   If it succeeds, remove the file (`rm /Volumes/FTP_VESSEL_DATA/.ops_rw_test`) and stop: the account
   can write, and we only want a read-only one here.

## 3. `.env`

Add the share URLs (the scheduled runs mount from these) and check the rest:

```bash
SHARE_URLS="smb://RO_USER@SERVER/FTP_VESSEL_DATA smb://RO_USER@SERVER/Raw_vessel_data2 smb://RO_USER@SERVER/vessel_data6"
```

```bash
grep -E '^(LANDING|STAGING|PROCESSED)_DIR|^SHARE_URLS|^TEAMS_WEBHOOK_URL|^WATCHDOG_WEBHOOK_URL|^MONGO_DB' .env | cut -c1-70
```

`LANDING_DIR`, `STAGING_DIR`, `PROCESSED_DIR` = the three `/Volumes/...` paths. `WATCHDOG_WEBHOOK_URL`
may stay empty (the watchdog then posts to the main webhook).

## 4. Folder sanity check

```bash
python scripts/check_folders.py
```

Lists every on-hire vessel and whether it has a folder on each share. A vessel without a folder
anywhere needs a `[folders]` alias in `config/vessels.toml`.

## 5. Dry run (posts nothing)

```bash
ops-check run --slot morning --dry-run
open out/preview-*-morning.html
```

- Progress shows on the way: each step with a clock and its duration, and a bar for the share scan
  (vessel n of N, the slowest part: ~4 min). Scheduled runs write the same steps to `logs/<date>.log`,
  one line per vessel with its time. `--quiet` turns it off.
- The terminal shows the pipeline text and the spot-check list per vessel.
- The preview is the real Teams card; click the ▸ rows.
- There must be **no "INCOMPLETE: file shares not reachable" banner**. If there is, step 2 didn't work.
- The dry run stores `results/<today>/morning.json` (not posted). The real run overwrites it.

## 6. Webhook test

```bash
ops-check test-post        # small labelled test card in the channel
```

Want to try the full card in a private channel first? Copy `.env` to `.env.test`, put that channel's
webhook in `TEAMS_WEBHOOK_URL`, and add `--env .env.test` right after `ops-check` in steps 7–8.

## 7. Real run

```bash
ops-check run --slot morning            # posts the card; exit code 0 = posted
ops-check watchdog --slot morning --dry-run    # expect: "morning run posted – OK"
ops-check run --slot recheck            # optional: the 11:00-style diff against the run above
```

A manual run is titled with the time you run it. Tomorrow's 07:30 run compares with today's last run.

## 8. Daily schedule (launchd)

launchd is macOS's own scheduler (D29). Unlike cron, it runs a job that was due while the Mac slept as
soon as it wakes. Four jobs, every day, Europe/Amsterdam: 07:30 run, 07:45 watchdog, 11:00 recheck,
11:15 watchdog. Each calls `scripts/scheduled-run.sh`: waits for the network, mounts missing shares
from `SHARE_URLS` (Keychain password), runs with the Mac kept awake, logs to `logs/<date>.log`.

```bash
scripts/install-launchd.sh                          # installs the 4 jobs, lists them
sudo pmset repeat wakeorpoweron MTWRFSU 07:25:00    # wake the Mac at 07:25 every day
pmset -g sched                                      # check the wake schedule
```

Test the scheduled path once (this **posts** to Teams):

```bash
launchctl kickstart gui/$(id -u)/online.mo4.opscheck.morning
tail -f logs/$(date +%F).log                        # ends with "=== exit 0"
```

The first scheduled run may trigger macOS prompts (Keychain access for `osascript`, access to network
volumes). Choose **Always Allow** / **Allow**, otherwise the next unattended run stalls on them.

**Conditions for the unattended runs**

- Logged in (LaunchAgents run in your user session), on power, lid open (or closed with an external display).
- On a network that reaches the file server and MongoDB Atlas (VPN if needed).
- If the shares can't be mounted the card still goes out, with the INCOMPLETE banner.
- If no card was posted by 07:45 / 11:15, the watchdog posts an alert.

**Daily follow-up:** `logs/<date>.log`, `results/<date>/morning.json` and `recheck.json`.

**Changes:** code changes need no reinstall (the jobs call the repo). Moving the repo does: run
`scripts/install-launchd.sh` again. Stop everything:

```bash
scripts/install-launchd.sh --remove
sudo pmset repeat cancel
```
