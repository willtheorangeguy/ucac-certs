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
|---|---|---|---|
| `GET` | `/login` | none | Sign-in form. `?sent=1` confirms a link was sent; `?denied=1` reports an address with no access. |
| `POST` | `/login` | none | Form field `email`. Always `303`, to `/login?sent=1` for a manager or `/login?denied=1` otherwise. |
| `GET` | `/auth` | none | Query parameter `token`. Redeems a sign-in link, sets the session cookie, `303` to `/`. An invalid, expired, or already-used token is `303` to `/login` with no cookie. |
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
    keep the probing slow. A rejected address never has a token created for it.

### Dashboard and scans

| Method | Path | Auth | Description |
|---|---|---|---|
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
|---|---|---|---|
| `GET` | `/staff` | session | The active roster. `?error=` renders a message, HTML-escaped. |
| `POST` | `/staff` | session | Adds a staff member. Always `303` to `/staff`, or to `/staff?error=...` on rejection. |
| `POST` | `/staff/{staff_id}/adopt-name` | session | Replaces the roster spelling with the Society's. No-op when no Society name is stored. |
| `POST` | `/staff/{staff_id}/remove` | session | Soft delete. Historical scan results are kept. |

`POST /staff` accepts these form fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Roster spelling. Whitespace is collapsed. |
| `member_code` | string | yes | Lifesaving Society member ID. Upper-cased; rejected unless alphanumeric. |
| `email` | string | no | Reminder address. Without one, the member is silently skipped by every reminder. |
| `phone` | string | no | Stored but unused — no channel reads it. |
| `away` | boolean | no | Moves the member into the "Away Spring / Summer" section. |

The member ID is verified against the Society **before** the row is written, so the request
takes a second or two. Three rejections are possible, and none of them write anything:

```text
Member ID must be letters and digits only.
ABC123: Member ID was not found.
ABC123 is already on the roster.
```

### Exports and reporting

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/export.xlsx` | session | The grid as an Excel workbook, plus a Diagnostics sheet. |
| `GET` | `/export.pdf` | session | The grid as a single-page portrait PDF. |
| `GET` | `/diagnostics` | session | Stored notes from the latest scan. |
| `GET` | `/reminders` | session | The forward reminder schedule and the sent history. Read-only. |

Both exports are served as attachments named `certifications-YYYY-MM-DD.xlsx` or `.pdf`,
dated from the scan rather than from the moment of download. Both return `404` before any
scan has completed:

```json
{"detail": "No completed scan yet."}
```

### Health

| Method | Path | Auth | Description |
|---|---|---|---|
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
|---|---|---|---|
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
|---|---|---|---|
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
|---|---|
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

{{ support() }}
