# Architecture

Written for someone about to modify the code. The organising idea is that the Lifesaving
Society publishes award titles and certification dates but no expiry dates, so expiry is
computed here — and almost every design decision below follows from that.

## Overview

A scan is the only thing that touches the network. Everything else reads stored results
out of SQLite, so the dashboard, the exports, and the reminder pass all agree with each
other by construction.

```mermaid
graph LR
  S[Lifesaving Society<br>member directory] -->|one HTTPS GET per member| C[SocietyClient]
  X2[Red Cross<br>certificate validator] -->|one HTTPS GET per certificate| C2[RedCrossClient]
  C -->|award titles and dates| A[awards.columns_for<br>awards.expiry_for]
  C2 -->|award title and expiry| A
  A -->|Certification records| G[grid.build_grid]
  G -->|MemberRow per staff| D[(SQLite)]
  M[Manual dates<br>entered by hand] --> D
  D -->|effective cells| W[Web dashboard]
  D -->|effective cells| X[Excel and PDF export]
  D -->|due thresholds| R[Reminder pass]
  R -->|one message per recipient| E[Resend]
```

Three sources feed one grid: the Society, the Red Cross, and dates entered by hand. They
are ranked against each other by the rule the grid already used within a single source —
a purpose-issued award beats a provisional credit, and otherwise the later expiry wins.

## Components

### Award mapping

`lss_report/awards.py` is the heart of correctness. It defines the six tracked columns
with their validity periods, and an **ordered** list of regular expressions mapping an
award title onto zero or more columns. The first pattern that matches wins.

Order carries real meaning. Exclusions come first, so `Lifesaving CPR Instructor/Examiner`
is not read as a CPR-C certification and `2023 National Lifeguard Update` is not read as a
National Lifeguard certification. `Swim Instructor` and `Lifesaving Instructor` are matched
before the generic instructor exclusion, because they are the two instructor awards that
*are* tracked.

A mapping can be marked **provisional**, meaning the award counts towards a column only as
a side effect. A CPR-C award credits first aid provisionally under Aquatic Centre policy; a
purpose-issued first aid award outranks it.

`columns_for` distinguishes three outcomes, and the difference matters downstream:

| Return | Meaning |
|---|---|
| `None` | The title matched no rule. It goes to diagnostics as an unmapped award. |
| `()` | Recognised, but not tracked on the grid — `Bronze Cross`, an exam clinic. |
| Non-empty | One or more `(column, provisional)` pairs. |

### The collector

`SocietyClient.fetch` performs one HTTPS request per member, spaced 1.1 seconds apart,
retrying twice on 429 and 5xx while honouring `Retry-After`. A 302 back to the directory
index means the member ID does not exist, and becomes a per-row error rather than an
exception that kills the scan.

`parse_member_page` reads the identity block and each certification card. A member ID
mismatch is a hard error — the Society returned somebody else. A **name** mismatch is only
a warning, because the roster genuinely contains typos and blanking those rows would lose
real certification data.

Cards come in two shapes. A current card carries a `Certification Date`. Some expired cards
carry only an `Expired On` value, which is worked backwards through the column's validity
period so every column derives its expiry the same way.

### The Red Cross validator

`lss_report/redcross.py` is the second collector. The Red Cross validates one certificate
at a time — number plus the holder's last name — and renders the answer into a single
paragraph, so `parse_validation` reads that paragraph and nothing else. Three outcomes:

| Result | Meaning |
|---|---|
| A `RedCrossCertificate` | The pair validated. Carries the award title and the expiry. |
| `None` | `No Certificate found` — the number and last name do not go together. |
| `ParseError` | The page said something the parser does not recognise. |

The validator publishes an **expiry** where the Society publishes a **certification date**,
so the two are reconciled before either reaches the grid. A Red Cross first aid card runs
three years, which is exactly the period the Aquatic Centre declines to honour; taking the
published expiry at face value would silently grant staff a third year. `ISSUER_VALIDITY_YEARS`
works the expiry back to the course date, and each column then applies its own validity —
two years for `FA`, one for `CPR-C` — the same arithmetic used for an `Expired On` card.

A failed Red Cross lookup is a **warning**, not an error. The Society awards are unaffected;
only the first aid cell falls back to whatever the Society has, and the reason lands in
Diagnostics under the `redcross` kind. An outage at the Red Cross cannot fail a scan.

### Row building

`grid.build_grid` turns records into rows. Per column it picks the best award: a confirmed
mapping beats a provisional one, and otherwise the latest expiry wins, which is how an
original plus two recerts collapses into one cell.

Status thresholds, as of the scan date:

| Status | Condition |
|---|---|
| `EXPIRED` | Expiry is before today |
| `EXPIRING` | Expiry is within 30 days |
| `CURRENT` | Anything later |
| `MISSING` | No award on record for that column |

It also cross-checks each computed status against the Society's own `card--expired` class
and records disagreements. `FA` and `CPR-C` are excluded from that check, because house
policy is shorter than the Society's and every staff member would otherwise disagree.

### Renderers

`excel.py` and `pdf.py` are two renderers over the same `Grid`. `theme.py` holds the fill
colours sampled from the original hand-maintained form, so the generated sheet matches what
staff already read. The PDF is portrait letter, sized so the whole overview fits one page.

### The web application

| Module | Responsibility |
|---|---|
| `settings.py` | Environment parsing and validation. Frozen dataclass; the manager allowlist lives here. |
| `db.py` | One shared `sqlite3` connection guarded by an `RLock`. |
| `files.py` | Uploaded copies of certificates: what is accepted, and where the bytes go. |
| `repository.py` | All SQL. `StaffRepository` owns the roster; `ScanRepository` owns scans, results, notes, and the reminder schedule. |
| `auth.py` | Sign-in code issue and redeem, rate limiting, signed session and pending cookies. |
| `scans.py` | `verify_member_code` and `verify_red_cross_number` for roster entry, `run_scan`, and `ScanRunner` — the worker thread. |
| `notify.py` | The `Channel` protocol, the Resend email channel, and reminder deduplication. |
| `scheduler.py` | A daemon thread ticking once a minute, firing the weekly scan and the daily reminder pass. |
| `app.py` | Route definitions and dependency wiring. |
| `server.py` | The `lss-web` entry point: argument parsing, seeding, uvicorn. |

## Data flow

A scan is asynchronous. The HTTP request that starts it returns immediately, because 45
lookups at 1.1 seconds each take about a minute.

```mermaid
sequenceDiagram
  participant M as Manager
  participant A as FastAPI route
  participant T as Scan thread
  participant S as Society
  participant X as Red Cross
  participant D as SQLite
  M->>A: POST /scan
  A->>T: start(triggered_by)
  A-->>M: 303 redirect, immediately
  T->>D: INSERT scan, status running
  loop each roster member
    T->>S: GET member page
    S-->>T: awards and dates
    opt member has a certificate number
      T->>X: GET validate certificate
      X-->>T: award title and expiry
    end
  end
  T->>D: store rows and notes, status complete
  M->>A: GET / on reload
  A->>D: read latest complete scan
  A-->>M: rendered grid
```

Only one scan runs at a time — `ScanRunner` holds a lock and refuses to start a second.
The scheduler additionally claims the day in memory before starting, because the worker
thread writes its scan row asynchronously and the database guard alone can miss a fast
second tick.

### Database schema

Ten tables, created with `CREATE TABLE IF NOT EXISTS` on every startup, plus a short list
of columns added to existing tables afterwards. There is no migration system beyond that
list: `db.migrate` compares `PRAGMA table_info` against `_ADDED_COLUMNS` and issues the
missing `ALTER TABLE`s, because `CREATE TABLE IF NOT EXISTS` leaves a live database's older
table untouched and a new column would otherwise never appear in production.

| Table | Holds |
|---|---|
| `staff` | The roster of record, including the Red Cross certificate number. Soft-deleted via `removed_at`. |
| `manual_cert` | Certification dates entered by hand, one per staff member per column. |
| `certificate_file` | An uploaded copy of a certificate: its own name, kind, size, uploader, and the generated name its bytes are stored under. |
| `scan` | One row per scan: start, finish, status, who triggered it. |
| `scan_result` | One row per staff member per column: expiry, status, source award, provisional flag. |
| `scan_note` | Diagnostics for a scan, keyed by kind: `error`, `name`, `unmapped`, `disagreement`, `redcross`. |
| `login_token` | Single-use sign-in codes, stored as a keyed digest, with an expiry. |
| `login_attempt` | Rate-limit ledger, keyed by address and by client IP. |
| `notification_log` | Every reminder sent. Doubles as the deduplication key. |
| `audit` | Every roster change, with actor and timestamp. |

Two indexes carry design intent rather than performance:

```sql
CREATE UNIQUE INDEX staff_active_code
    ON staff (member_code) WHERE removed_at IS NULL;
```

Member codes are unique only among staff still on the roster, so somebody who leaves and
returns does not collide with their own soft-deleted row.

```sql
CREATE UNIQUE INDEX notification_once
    ON notification_log (staff_id, column_code, expiry_date, threshold, channel);
```

One reminder per staff member, award, expiry, ladder step, and channel. Re-running a scan
cannot replay reminders, and neither can a restart.

```sql
CREATE UNIQUE INDEX manual_cert_once ON manual_cert (staff_id, column_code);
```

One hand-entered date per staff member per column. Saving the edit panel replaces what is
there rather than accumulating entries, which is what lets a blank field mean "clear it".

Manual dates are folded in on **read**, by `repository.effective_cells`, not written into
`scan_result`. The dashboard, the exports, and the reminder schedule all go through it, so
a date entered at 9am moves that afternoon's reminder without waiting for a scan, and
`scan_result` stays a faithful record of what the two verifiable sources actually said.

### Stored copies of certificates

The two verifiable sources answer whether a certification is current. Neither hands back
the card, so a copy — a scan, or a photograph taken on a phone — is uploaded by hand and
kept as the evidence behind the row.

The bytes go to a flat directory (`UPLOADS_PATH`, by default `uploads/` beside the
database) under a generated random name; `certificate_file` holds the rest. Files are kept
out of SQLite so that one volume backup covers both, and so serving a large PDF does not
read it through the database connection every request thread shares.

`files.py` decides what may be stored, and it decides from the file's own leading bytes.
The filename and the browser's content type are treated as claims, not evidence: they are
kept for display and ignored for everything else, so a script renamed `.pdf` is refused
rather than stored and later handed back to somebody's browser. The size limit is applied
while the bytes stream in, since a declared `Content-Length` is the caller's to understate.
On the way out, a copy is always an attachment with `X-Content-Type-Options: nosniff` — it
was uploaded by a manager but it is served from the application's own origin, and it is
not given the chance to run there.

## Directory layout

Only the paths that matter.

```text
lss_report/
├── awards.py          Award title to column mapping, validity, expiry arithmetic
├── scraper.py         Society HTTP client and page parser
├── redcross.py        Red Cross validator client and result parser
├── models.py          StaffMember, Certification, MemberRecord, CellStatus
├── grid.py            Row building, best-award selection, status thresholds
├── excel.py           openpyxl workbook, plus a Diagnostics sheet
├── pdf.py             reportlab portrait grid
├── theme.py           Fill colours sampled from the original form
├── config.py          Dotenv loading and staff.json validation
├── cli.py             lss-report, for maintenance and debugging
└── web/
    ├── app.py         Routes
    ├── server.py      lss-web entry point
    ├── files.py       Upload validation and on-disk storage of certificate copies
    ├── schema.sql     The ten tables
    └── templates/     Jinja pages
scripts/
├── extract_roster.py  Rebuilds staff.json from a certification form PDF
└── run-local.ps1      Venv, install, live scan, write report.pdf and report.xlsx
tests/                 229 tests, no network access
```

## Design decisions

**Expiry is computed, not read.** Verified against the live site: a member page publishes
Name, Member ID, award titles, and certification dates, and nothing else. An earlier
version tried to parse an expiry out of a card modal and returned `None` for every award in
production, which meant the "expiring soon" highlight had never once fired.

**Raw `sqlite3`, one connection, one lock.** The application is single-process and handles
a handful of requests a week. A connection pool or an ORM would be more machinery than the
workload justifies. The lock is not optional, though: request threads and the scan worker
share the connection, and a rollback in one thread would otherwise discard another thread's
uncommitted rows.

**A daemon thread instead of a scheduling library.** Two jobs on one always-on machine do
not justify the dependency. Both jobs are idempotent — a repeat scan on the same day is
skipped by date, and reminders are deduplicated in the database — so a mid-day restart
cannot double-fire.

**Always-on rather than scale-to-zero.** The scheduler needs a live process, and a Fly
volume attaches to exactly one machine at a time, so splitting the scheduler onto a second
machine is not available either. See [Deployment](deployment.md).

**Soft delete.** Removing a staff member sets `removed_at` and keeps their historical
`scan_result` rows, so a past report stays reproducible and their member code becomes free
again.

**One channel today, a protocol anyway.** `notify.Channel` is a `Protocol` with a single
implementation. The indirection costs almost nothing and means a second delivery channel
can be added without touching the scheduler or the deduplication logic.

{{ support() }}
