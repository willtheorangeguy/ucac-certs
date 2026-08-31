# Configuration

Everything is configured through environment variables. There is no configuration file
format of the project's own — `--env-file` reads a small dotenv-style file and loads it
into the environment.

## Precedence

Highest wins:

**Command-line flag > existing environment variable > `--env-file` value > built-in default**

The middle two are the pair that surprises people. `--env-file` is implemented with
`os.environ.setdefault`, so a variable already present in the environment is **kept** and
the file's value is discarded. A stale exported `DATABASE_PATH` silently beats the one
written in `.env`.

!!! tip
    When a setting refuses to apply, check the real environment before editing the file:
    `printenv DATABASE_PATH` on macOS or Linux, `$env:DATABASE_PATH` in PowerShell.

## Environment variables

Names are used verbatim — there is no prefix scheme.

| Option | Type | Default | Description |
|---|---|---|---|
| `SESSION_SECRET` | string | *required* | Signing key for the session cookie. Minimum 32 characters. Rotating it signs everyone out. |
| `MANAGER_EMAILS` | list | *required* | Comma- or newline-separated addresses allowed to sign in. Compared case-insensitively. |
| `RESEND_API_KEY` | string | unset | Resend API key. Unset means no mail is sent at all. |
| `MAIL_FROM` | string | `certifications@example.org` | Envelope sender. Must be an address on a domain verified with Resend. |
| `BASE_URL` | string | `http://127.0.0.1:8000` | Public origin. Sign-in links are built from it, and an `https` value is what marks the session cookie `Secure`. A trailing slash is stripped. |
| `DATABASE_PATH` | path | `data/lss.sqlite3` | SQLite file. Parent directories are created on startup. |
| `SCAN_HOUR` | integer | `6` | Hour, 0–23, at which the Monday scan runs, in `America/Edmonton`. |
| `REMINDER_HOUR` | integer | `7` | Hour, 0–23, at which the daily reminder pass runs, in `America/Edmonton`. |
| `DISABLE_SCHEDULER` | flag | unset | Set to `1`, `true`, or `yes` to start the app without the background scheduler. |
| `PORT` | integer | `8000` | Default for `--port`. The `--port` flag overrides it. |

### Validation

`SESSION_SECRET` and `MANAGER_EMAILS` are validated at startup and a bad value stops the
process with a one-line message and exit code 2:

```text
Configuration error: SESSION_SECRET must be at least 32 characters.
```

```text
Configuration error: MANAGER_EMAILS contains an entry that is not an address.
```

The two hour settings are not validated the same way, and both failure modes are worth
knowing:

- A non-numeric value raises an uncaught `ValueError` and the process exits with a
  traceback rather than the clean message above.
- A numeric value outside 0–23 is accepted silently. `SCAN_HOUR=99` never matches a real
  hour, so the weekly scan simply never runs and nothing reports the problem.

Both are recorded in the project's internal defect log.

## Settings that are not configurable

These are constants in the source. Changing one means editing the code, and each is
listed here so nobody hunts for an environment variable that does not exist.

| Behaviour | Value | Defined in |
|---|---|---|
| Reminder ladder | 30, 14, and 7 days before expiry | `lss_report/web/settings.py` |
| Scan day | Monday | `lss_report/web/scheduler.py` |
| Scheduler tick | 60 seconds | `lss_report/web/scheduler.py` |
| Timezone | `America/Edmonton` | `lss_report/web/scans.py` |
| Sign-in link lifetime | 15 minutes, single use | `lss_report/web/settings.py` |
| Session lifetime | 30 days | `lss_report/web/settings.py` |
| Sign-in rate limit | 5 attempts per 15 minutes, per address and per IP | `lss_report/web/auth.py` |
| "Expiring soon" threshold | 30 days | `lss_report/grid.py` |
| Society request spacing | 1.1 seconds, 2 retries, 20-second timeout | `lss_report/scraper.py` |

## Certification validity periods

The expiry of every cell is the certification date plus the validity period for its
column. These are policy, not configuration, and live in `lss_report/awards.py`.

| Column | Award | Valid for |
|---|---|---|
| `NL` | National Lifeguard | 2 years |
| `SI` | Swim Instructor | 2 years |
| `LSI` | Lifesaving Instructor | 2 years |
| `FA` | First Aid | 2 years |
| `CPR-C` | CPR Level C | 1 year |
| `O2` | O2 Administration | 2 years |

First aid is deliberately stricter than the certificate. The issuing body grants three
years; the Aquatic Centre honours two. Because the house policy for `FA` and `CPR-C` is
shorter than the Society's, those two columns are excluded from the cross-check that
compares a computed expiry against the Society's own current/expired flag — otherwise
every staff member would generate a false disagreement.

## Example configuration

A complete local development file:

```ini title=".env"
SESSION_SECRET=change-me-to-a-long-random-string-at-least-32-chars
MANAGER_EMAILS=manager@example.org

RESEND_API_KEY=
MAIL_FROM=certifications@example.org
BASE_URL=http://127.0.0.1:8000

DATABASE_PATH=data/lss.sqlite3
SCAN_HOUR=6
REMINDER_HOUR=7
```

With `RESEND_API_KEY` empty, sign-in links are written to the log instead of emailed.
That is the intended local behaviour and the only way to sign in without a mail provider.

For production values, see [Deployment](deployment.md).

## Roster file format

`staff.json` is not configuration — the database is the roster of record — but the
`--seed` flag reads one for a one-time import, and `lss-report` reads one directly.

```json title="staff.json"
[
  { "name": "Robin Rivers", "memberCode": "RRV001" },
  { "name": "Sam Summers", "memberCode": "SSM002", "away": true }
]
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Roster spelling. Used only as a cross-check; lookups key off the member ID. |
| `memberCode` | string | yes | Lifesaving Society member ID. Letters and digits only; upper-cased on load. |
| `away` | boolean | no | Moves the member into the "Away Spring / Summer" section. Defaults to `false`. |

The file must be a non-empty array. Duplicate names and duplicate member codes are both
rejected. `staff.json` is gitignored because it carries staff personal information;
`staff.example.json` is the committed placeholder.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Configuration error: SESSION_SECRET is required.` | The variable is unset or empty. `--env-file` was possibly not passed. |
| Sign-in link points at the wrong host | `BASE_URL` does not match the origin you are actually serving from. It is the only source for the link. |
| Signed in, then immediately signed out | The address was removed from `MANAGER_EMAILS`. Membership is re-checked on every request, not just at sign-in. |
| Everyone was signed out at once | `SESSION_SECRET` changed. Existing cookies no longer verify. |
| Reminders never send | `RESEND_API_KEY` is unset, or the staff have no email address on their roster entry. Both are silent by design. |
| The weekly scan never runs | `SCAN_HOUR` is outside 0–23, or `DISABLE_SCHEDULER` is set. |

{{ support() }}
