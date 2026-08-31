# Lifesaving certification report

This project checks authorized staff records through the Lifesaving Society Alberta and
Northwest Territories verification page and produces a certification grid — one row per
staff member, one column per award type — as an Excel workbook and a PDF.

**Documentation: [williamvdg.me/ucac-certs](https://williamvdg.me/ucac-certs/)**

## How expiry is determined

**The Society does not publish expiry dates.** A member page lists only the award title
and its certification date, so every expiry here is *computed* as certification date plus
a validity period. Some expired cards carry an "Expired On" value instead of a
certification date; those are worked backwards to a certification date so all columns
derive the same way.

| Column | Award | Valid for |
| --- | --- | --- |
| NL | National Lifeguard | 2 years |
| SI | Swim Instructor | 2 years |
| LSI | Lifesaving Instructor | 2 years |
| FA | First Aid | 2 years |
| CPR-C | CPR Level C | 1 year |
| O2 | O2 Administration | 2 years |

First aid is deliberately stricter than the certificate: the issuing body grants three
years, the Aquatic Centre honours two. Because house policy for FA and CPR-C is shorter
than the Society's, those two columns are not cross-checked against the Society's own
current/expired flag — the other four are, and disagreements land in Diagnostics.

Where a member has several awards in one column (an original plus recerts), the latest
expiry wins. An award issued for a different purpose that still counts — first aid
credited from "Lifesaving CPR C & AED" — is marked provisional and loses to a
purpose-issued award. Red Cross first aid is not yet collected; those cells fall back to
the provisional Society award or show grey.

## Cell colours

| Colour | Meaning |
| --- | --- |
| Red `#FF6565` | Expired |
| Yellow `#FFD13F` | Expires within 30 days |
| Grey `#808080` | No award on record |
| Pink `#FFC7CE` | Member lookup failed — the row carries no data |

## The web application

The web app is the main entrypoint. Managers sign in, manage the roster, run scans, download
the Excel and PDF, and send reminders. **The database is the roster of record** — `staff.json`
is only a one-time seed.

```powershell
Copy-Item .env.example .env      # fill in SESSION_SECRET and MANAGER_EMAILS
.venv\Scripts\python.exe -m lss_report.web.server --env-file .env --seed staff.json
```

Then open http://127.0.0.1:8000. Without `RESEND_API_KEY` the sign-in link is written to the
log instead of emailed, which is how you sign in locally.

### Sign-in

Magic link only, no passwords. `MANAGER_EMAILS` is the entire security boundary: only listed
addresses can obtain a link. The endpoint responds identically either way, so it cannot be
used to discover who is a manager. Tokens are stored hashed, single-use, and expire in 15
minutes; requests are rate-limited per address and per IP.

### Roster

Adding a staff member verifies the LS# against the Society before saving, so a typo is caught
at entry rather than appearing as an empty row after the next scan. Where the Society spells a
name differently, the roster screen offers to adopt its spelling. Removal is a soft delete —
past scan results stay so old reports remain reproducible, and the member code becomes free
again for re-adding.

### Schedule

A plain daemon thread runs the scan weekly (Monday, `SCAN_HOUR`) and a reminder pass daily
(`REMINDER_HOUR`), both in `America/Edmonton`. Both are idempotent: a repeat scan on the same
day is skipped, and reminders are deduped in `notification_log`, so a restart cannot
double-fire. Set `DISABLE_SCHEDULER=1` to turn it off.

### Reminders

Email through Resend (3,000/month free, 100/day; one recipient per call because Resend counts
each recipient separately). Staff are reminded at 30, 14 and 7 days before expiry, once each.
Anyone without an email address on their roster entry is skipped.

Sending is entirely automatic — there is no manual send. The **Reminders** page is read-only:
the schedule for the next 60 days above, everything already sent below, both drawn from the
latest scan and `notification_log`.

Delivery goes through a `Channel` protocol, so a second channel can be added later without
touching the scheduler or the dedupe logic.

## Deployment

One always-on Fly machine (`shared-cpu-1x`, 256 MB, ~$2/month) with a volume for SQLite.
It stays always-on deliberately: the in-process scheduler needs a live process, and a Fly
volume attaches to only one machine, so scale-to-zero and a separate scheduler machine are
both ruled out.

```bash
fly launch --no-deploy               # uses the checked-in fly.toml
fly volumes create lss_data --size 1
fly secrets set SESSION_SECRET=... MANAGER_EMAILS=... RESEND_API_KEY=... \
                MAIL_FROM=... BASE_URL=https://<app>.fly.dev
fly deploy
fly volumes snapshots list <volume>  # confirm backups before the first real scan
```

`BASE_URL` must match the deployed origin or sign-in links point at the wrong host. Seed the
roster once via `fly ssh console -C "lss-web --seed /data/staff.json"`, or just add staff
through the UI.

GitHub Actions runs tests on every push and deploys to Fly on `main` using a `FLY_API_TOKEN`
deploy token scoped to the app.

## Privacy

Staff PII under PIPEDA: names, Society member IDs, emails, phone numbers. Access is limited to
the manager allowlist, every roster change is written to an audit trail, and the SQLite volume
should be backed up and snapshotted.

## Command line

The CLI remains for maintenance and debugging. It reads `staff.json` directly, not the
database.

## Local setup and test run

Python 3.11 or newer is required.

```powershell
.\scripts\run-local.ps1
```

The script creates `.venv`, installs the application and test dependencies, performs the
live lookups, and writes `report.pdf` and `report.xlsx`. Subsequent runs can skip
installation with `-SkipInstall`. To run the tests independently:

```powershell
.venv\Scripts\python.exe -m pytest
```

The collector deliberately sends requests one at a time with a delay — a full roster takes
about a minute. Only use Member IDs that staff have supplied for verification, and stop
the run if the Society asks you to.

## Roster

`staff.json` is a JSON array of `name`, `memberCode`, and an optional `away` flag that
moves the member into the "Away Spring / Summer" section. It is ignored by Git.

To rebuild it from a certification form PDF:

```powershell
.venv\Scripts\python.exe scripts\extract_roster.py "Cert Form June 2024.pdf" staff.json
```

Names are only a cross-check — lookups key off the Member ID. A name that differs from the
Society's record is reported as a warning and the Society's spelling is used; a Member ID
that returns someone else is a hard error.

## Diagnostics

The workbook's second sheet lists anything needing a human:

- **Lookup error** — the Member ID was not found. Verify it against the Society.
- **Name warning** — the roster spelling differs from the Society's.
- **Unmapped award** — an award title matching no rule. If it should count towards a
  column, add it to `_RULES` in `lss_report/awards.py`.
- **Status disagreement** — our computed expiry contradicts the Society's own flag.
- **Provisional** — a first aid cell credited from a CPR award.

## CI

GitHub Actions runs the tests on push, and deploys on `main` once they pass — the deploy
workflow runs the suite itself and gates on it, and skips documentation-only commits. No
scraping, roster data, or credentials belong in the repository; the deployed server
performs all certification work.

## License

MIT — see [`LICENSE.md`](LICENSE.md).
