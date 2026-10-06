# API

The project exposes three interfaces: an HTTP application that managers use, two
command-line entry points for maintenance, and a small Python package that both are built
on. The HTTP application is the one to start with.

There is no public REST API for third parties. Every route is either part of the manager
interface or a health check, and the interactive OpenAPI pages are disabled — `/docs` and
`/redoc` both return 404.

## HTTP routes

All routes are served by `lss_report.web.app.create_app`. Sessions are carried in a signed
`lss_session` cookie. A route marked **session** answers `303 See Other` with
`Location: /login` when the cookie is missing, invalid, or belongs to an address that is no
longer in `MANAGER_EMAILS`.

### Authentication

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/login` | none | Sign-in form. `?sent=1` swaps it for the code form; `?bad=1` marks a wrong code; `?denied=1` reports an address with no access. |
| `POST` | `/login` | none | Form field `email`. Always `303`, to `/login?sent=1` for a manager or `/login?denied=1` otherwise. A manager also gets the `lss_pending` cookie, a signed record of which address the code went to. |
| `POST` | `/verify` | `lss_pending` | Form field `code`. Redeems the six-digit code for the address in the cookie, sets the session cookie, `303` to `/`. A wrong, expired, or already-used code is `303` to `/login?sent=1&bad=1`; a missing or forged cookie is `303` to `/login`. Neither sets a session cookie. |
| `POST` | `/logout` | none | Clears the cookie and redirects to `/login`. |

```bash
curl -i -X POST -d "email=manager@example.org" http://127.0.0.1:8000/login
```

```text
HTTP/1.1 303 See Other
location: /login?sent=1
```

!!! warning
    `POST /login` deliberately distinguishes an approved address from an unapproved one.
    That is clearer for staff, and it does let someone probe which addresses are managers.
    The per-address and per-IP rate limits — five attempts per fifteen minutes — are what
    keep the probing slow. A rejected address never has a code created for it.

### Dashboard and scans

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/` | session | The certification grid from the most recent completed scan. |
| `POST` | `/scan` | session | Starts a scan on a worker thread and returns `303` to `/` immediately. A second call while one is running is ignored. |
| `GET` | `/scan/status` | session | JSON: whether a scan is running, and the most recent scan row. |

```bash
curl -b "lss_session=$COOKIE" http://127.0.0.1:8000/scan/status
```

```json
{"running": false, "latest": null}
```

Once a scan has run, `latest` is the scan row itself — `id`, `started_at`, `finished_at`,
`status`, `triggered_by`, and `detail`. `status` is one of `running`, `complete`, or
`failed`, and `triggered_by` is either a manager's email address or the literal
`schedule`.

### Roster

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/staff` | session | The active roster. `?error=` renders a message, HTML-escaped. |
| `POST` | `/staff` | session | Adds a staff member from the panel. Always `303` to `/staff`, or to `/staff?error=...` on rejection. |
| `POST` | `/staff/{staff_id}/edit` | session | Saves the same panel for an existing member. |
| `POST` | `/staff/{staff_id}/adopt-name` | session | Replaces the roster spelling with the Society's. No-op when no Society name is stored. |
| `POST` | `/staff/{staff_id}/remove` | session | Soft delete. Historical scan results are kept. |

`POST /staff` and `POST /staff/{staff_id}/edit` take the same form, because the interface
uses one panel for both — the **Add a staff member** button and the pencil on a row open
the same dialog, empty or filled in:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | Preferred display name for pages, all exports, and reminder emails. Whitespace is collapsed. |
| `member_code` | string | yes | Lifesaving Society member ID. Upper-cased; rejected unless alphanumeric. |
| `member_code_2` | string | no | Second Society member ID for staff whose awards are split across two profiles. Validated like the primary ID; blank clears it. |
| `red_cross_cpr_number` | string | no | Canadian Red Cross CPR-C certificate number. Digits only; validated as CPR-C. Blank clears it. |
| `red_cross_fa_number` | string | no | Canadian Red Cross Standard First Aid certificate number. Digits only; validated as Standard First Aid. Blank clears it. |
| `email` | string | no | Reminder address. Without one, the member is silently skipped by every reminder. |
| `phone` | string | no | Legacy API field, stored but unused. It is no longer shown in the staff panel or table; omitting it preserves the stored value. |
| `away` | boolean | no | Moves the member into the "Away" section. |
| `supervisor` | checkbox | no | Send `true` when checked; omit when unchecked. Includes the member in Lockers and enables locker reminders. |
| `locker_number` | string | no | Locker identifier, such as `007`. Preserves leading zeroes; blank clears it. |
| `fit_test_date` | date | no | Test date, such as `2024-10-06`. Expiry is two years later. Blank clears it. |
| `cartridge_date` | date | no | Issue date, such as `2022-10-06`. Expiry is five years later. Blank clears it. |
| `boot_size` | string | no | Boot size, such as `9.5`. Blank clears it. |
| `manual_<CODE>` | date | no | An `ISO 8601` certification date entered by hand, one field per column — `manual_FA`, `manual_CPR-C`, and so on. Blank clears the entry. |

On an add, every member ID and certificate number is verified before anything is
written, so the request takes a second or two. On an edit, each check runs only when its
own field changed, so a save that touches neither reaches no network at all.

Everything is validated before anything is written. A malformed date at the bottom of the
panel rejects the whole submission rather than leaving the details above it saved:

```text
A name is required.
Member ID must be letters and digits only.
ABC123: Member ID was not found.
ABC123 is already on the roster.
The Red Cross CPR-C number must be digits only.
The Red Cross Standard First Aid number must be digits only.
Red Cross 999999999: No Red Cross certificate matches that number and last name.
FA manual date must be a real date.
Fit test date must be a real date with a valid expiry.
Cartridge issue date must be a real date with a valid expiry.
```

A manual date is an additional source rather than an override. It competes with whatever
the last scan found on the same terms the grid already uses — a purpose-issued award beats
a provisional credit, and otherwise the later expiry wins — so entering an old date cannot
hide a current award. It applies immediately, without waiting for the next scan.

The preferred name is `staff.name`. The stored `society_name` is used for comparison and
certificate matching. Scans update `society_name` without changing `name`.
`POST /staff/{staff_id}/adopt-name`, exposed as **Use this** on Staff, explicitly copies
the Society spelling into `name`. Reports built from an earlier scan use the current
preferred name on the next request. Reminder history also displays the current staff name.
Red Cross validation uses the Society name when available, falling back to the preferred
name when no Society name is known.

The shared dialog has staff details, lifeguarding dates, and locker information sections.
Both role flags appear as checkmarks in the staff table. Locker dates are independent of
certification scans. Clearing `supervisor` stops locker reminders and hides the member
from Lockers. Locker details submitted with the form remain stored. Blank or omitted
locker fields clear their stored values; an omitted `supervisor` field clears the flag.

### Lockers

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/lockers` | session | Active supervisors' names, locker numbers, fit test expiries, cartridge expiries, and boot sizes. |
| `GET` | `/lockers.xlsx` | session | The same five columns in an Excel workbook with real dates and expiry colours. |
| `GET` | `/lockers.pdf` | session | The same five columns in a portrait PDF; large rosters paginate with repeated headings. |

All three work without a completed scan. They read the active roster at request time and
calculate status against the current date in `America/Edmonton`. Removed staff and staff
without the Supervisor flag are excluded. Away supervisors appear in a separate section.
Missing dates are grey, dates before today are red, and dates from today through 30 days
ahead are yellow. Later dates have no fill.

With a signed-in session in `COOKIE`, save locker details through the existing edit route:

```bash
curl -i -b "lss_session=$COOKIE" --data-urlencode "supervisor=true" --data-urlencode "locker_number=007" --data-urlencode "fit_test_date=2024-10-06" --data-urlencode "cartridge_date=2022-10-06" --data-urlencode "boot_size=9.5" http://127.0.0.1:8000/staff/1/edit
```

```text
HTTP/1.1 303 See Other
location: /staff
```

The edit endpoint replaces the optional fields in its shared form. Include existing
email, secondary member ID, certificate numbers, and manual dates when preserving them.

```bash
curl -b "lss_session=$COOKIE" http://127.0.0.1:8000/lockers -o lockers.html
curl -b "lss_session=$COOKIE" http://127.0.0.1:8000/lockers.xlsx -o lockers.xlsx
curl -b "lss_session=$COOKIE" http://127.0.0.1:8000/lockers.pdf -o lockers.pdf
```

The page returns `200` HTML. Each download returns `200` with `Content-Disposition:
attachment` and a filename `lockers-YYYY-MM-DD.xlsx` or `.pdf`, dated on the day of
download. Content types are
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` and `application/pdf`.
An empty supervisor roster still produces valid files containing the column headings.

### Certificate copies

A scan proves a certification is current; the copy is the evidence behind it, for the
inspector who asks to see the card. The **file** button on a roster row opens the copies
kept for that member.

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/staff/{staff_id}/files` | session | Stores one uploaded copy. `multipart/form-data`, field `document`. Always `303` to `/staff`, or to `/staff?error=...` on rejection. |
| `GET` | `/staff/{staff_id}/files/{file_id}` | session | The stored copy, as an attachment under the name it was uploaded with. |
| `POST` | `/staff/{staff_id}/files/{file_id}/remove` | session | Forgets the copy and deletes the file itself. |

An upload is identified by its own leading bytes, never by the filename or the content
type the browser claims, and only a PDF, PNG, JPEG, GIF, WebP or HEIC is stored. The limit
is 10 MB, checked while the bytes stream in rather than from a declared length:

```text
A copy must be a PDF or an image (PNG, JPEG, GIF, WebP or HEIC).
A copy must be 10 MB or smaller.
Choose a file to attach.
```

The bytes live in the uploads directory ([`UPLOADS_PATH`](configuration.md), by default an
`uploads/` directory beside the database) under a generated name; the database holds the
uploaded name, kind, size and uploader. A download is always served as an attachment with
`X-Content-Type-Options: nosniff`, so a stored file never runs on the application's origin,
and the path checks the staff id as well as the file id, so a guessed number cannot be read
under another member's URL. Removing a staff member is a soft delete and keeps their
copies; deleting a copy is immediate and permanent.

### Exports and reporting

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/export.xlsx` | session | The grid as an Excel workbook, plus a Diagnostics sheet. |
| `GET` | `/export.pdf` | session | The grid as a portrait PDF with repeated column headings. |
| `GET` | `/workplace-first-aiders.xlsx` | session | Workplace first aiders with only name, Standard First Aid expiry, and CPR-C expiry. |
| `GET` | `/workplace-first-aiders.pdf` | session | The same workplace first aiders list as a portrait PDF. |
| `GET` | `/diagnostics` | session | Stored notes from the latest scan. |
| `GET` | `/reminders` | session | Upcoming certification and supervisor locker reminders, including manual dates before the first scan, and sent history. Read-only. |

The full-grid attachments are named `certifications-YYYY-MM-DD.xlsx` or `.pdf`; the companion
attachments are named `workplace-first-aiders-YYYY-MM-DD.xlsx` or `.pdf`. Dates come from
the scan rather than the moment of download. All four return `404` before any scan has
completed:

```json
{"detail": "No completed scan yet."}
```

Locker downloads use the current roster and date; the four certification downloads above
require a completed scan. The daily reminder pass also works before a scan when manual
certification dates or supervisor locker dates are present. Both locker expiry types use
the same 30-, 14-, and 7-day ladder and notification deduplication as certifications.

### Health

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/healthz` | none | Liveness probe. Used by the Docker `HEALTHCHECK` and the Fly.io HTTP check. |

```bash
curl http://127.0.0.1:8000/healthz
```

```json
{"ok":true}
```

## Command line

Installing the package registers two console scripts. Both can also be run as modules —
`python -m lss_report.cli` and `python -m lss_report.web.server`.

### `lss-web`

Runs the web application. This is the primary entry point.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--host` | string | `0.0.0.0` | Interface to bind. |
| `--port` | integer | `PORT`, else `8000` | Port to bind. |
| `--env-file` | path | none | Load settings from a dotenv file. Existing environment variables win. |
| `--seed` | path | none | One-time roster import from a `staff.json`. Ignored once the database has any staff. |
| `--seed-only` | flag | off | Import the roster and exit without starting the server. |

```bash
lss-web --env-file .env --port 8000
```

Seeding a deployed instance without serving traffic:

```bash
lss-web --env-file .env --seed /data/staff.json --seed-only
```

```text
INFO Seeded 45 staff from /data/staff.json; the database is the roster now.
```

A configuration problem exits with status `2` and a single line on standard error.

### `lss-report`

Generates the grid straight from a `staff.json`, bypassing the database. It exists for
maintenance and debugging; the web application owns the roster.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--staff-file` | path | none | Roster to read. Required. |
| `--env-file` | path | none | Load settings from a dotenv file. |
| `--output` | path | none | Write the PDF here. |
| `--excel` | path | none | Write the Excel workbook here. |

`--staff-file` is required, and at least one of `--output` and `--excel`; both outputs may
be given together. The roster is only ever read from a file — it cannot be passed as a JSON
string, because argv and the environment are readable by other processes and land in shell
history, and the roster is staff names and Society member IDs.

```bash
lss-report --staff-file staff.json --output report.pdf --excel report.xlsx
```

```text
Report completed for 45 staff record(s).
```

| Exit code | Meaning |
| --- | --- |
| `0` | The report was written. |
| `1` | The Society could not be reached, or generation failed. |
| `2` | A configuration problem — no roster, or neither output flag. |

!!! warning
    This command performs a live lookup for every roster entry, spaced 1.1 seconds apart.
    Only use member IDs that staff have supplied for verification.

### `scripts/extract_roster.py`

Rebuilds `staff.json` from a certification form PDF. It has no dependencies beyond the
standard library — the form's fonts are subset with glyph-id encoding, so the text is
decoded through the PDF's own embedded `ToUnicode` maps.

```bash
python scripts/extract_roster.py "Cert Form June 2024.pdf" staff.json
```

Names are read from the left-hand column and the six-character member ID from the end of
each row. Everyone below the `Away Spring / Summer` marker is flagged `away`.

That marker is the literal heading in the hand-maintained form this script reads, so it
stays as it is. The application itself labels the section `Away`, because which semester
someone is away for changes.

## Python package

The modules below carry the certification logic and depend on nothing outside the standard
library, so they can be imported and reused on their own. The collector, the renderers, and
the web application all build on them.

Start with `awards`: it defines what a certification column is and how long one lasts, and
every other module takes that as given.

### Awards

::: lss_report.awards
    options:
      members:
        - CertColumn
        - COLUMNS
        - columns_for
        - add_years
        - expiry_for
        - certification_date_from_expiry
        - normalize_award

### Models

::: lss_report.models
    options:
      members:
        - StaffMember
        - CellStatus
        - Certification
        - MemberRecord
        - ReportData

### Grid

::: lss_report.grid
    options:
      members:
        - EXPIRY_WARNING_DAYS
        - status_for
        - GridCell
        - MemberRow
        - Grid
        - build_grid

### Locker reports

The web route builds a `LockerReport` from active supervisors. It carries the current date
and rows with locker details and expiry statuses, and is shared with both download
renderers. `LockerExpiry.for_date` uses the grid's status thresholds.

::: lss_report.lockers
    options:
      members:
        - LOCKER_HEADINGS
        - LockerExpiry
        - LockerRow
        - LockerReport

{{ support() }}
