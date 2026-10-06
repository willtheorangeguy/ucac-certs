# Testing

The suite covers certification scans, roster editing, supervisor lockers, downloads, and
reminders. A full run takes about fifteen seconds. It makes no network
calls — the Society and the Red Cross are each represented by stored HTML fixtures — so it
is safe to run anywhere, including CI.

## Running the tests

```bash
.venv/bin/python -m pytest
```

```text
283 passed, 1 warning in 16.67s
```

Configuration lives in `pyproject.toml`: `testpaths` is `tests`, and `addopts` is `-q`, so
plain `pytest` from the repository root does the right thing.

The warning in this run is a dependency deprecation notice about `httpx` in the Starlette
test client. Warning counts and timings depend on installed dependency versions.

### Narrower runs

```bash
python -m pytest tests/test_awards.py
```

```bash
python -m pytest -k "expiry or reminder" -v
```

## What is covered

| File | Covers |
|---|---|
| `test_awards.py` | Award title to column mapping, the ordering traps, leap-year date arithmetic |
| `test_grid.py` | Best-award selection, the status boundaries, away grouping, cross-check exclusions |
| `test_scraper.py` | Page parsing, both card shapes, name warnings, member ID mismatches, retries |
| `test_redcross.py` | Validator result parsing, the three-year working-back, retries, digits-only numbers |
| `test_scans.py` | The two sources merged into one grid, and a Red Cross failure staying a warning |
| `test_excel.py` | Fill colours landing on the right cells, real date values, the Diagnostics sheet |
| `test_pdf.py` | Certification and first aider PDFs, markup escaping, and a large first aider roster kept to one page |
| `test_lockers.py` | Five-column workbook and PDF exports, expiry colours, literal locker numbers, empty reports, and PDF pagination |
| `test_settings.py` | Environment validation, schedule hours, email configuration, and upload path defaults |
| `test_config.py` | Dotenv parsing, `staff.json` validation and its rejections |
| `test_cli.py` | The `lss-report` entry point |
| `test_repository.py` | Roster CRUD, soft delete, scan storage, manual dates, the schema migration, the reminder schedule |
| `test_auth.py` | Code issue and redeem, single use, expiry, keyed digest, rate limiting on both issuing and guessing |
| `test_notify.py` | Resend delivery, certification and locker reminder text, all three thresholds, deduplication, and renewal scheduling |
| `test_scheduler.py` | Weekly and daily firing, duplicate prevention, and locker reminders before the first scan |
| `test_files.py` | What an upload has to be to be stored, the size limit, and generated names |
| `test_web_app.py` | Protected routes, roster verification, staff table checkmarks, locker fields and validation, supervisor filtering, colour boundaries, and downloads without a scan |
| `test_web_render.py` | Every page rendered against stored scan data |
| `test_security.py` | The properties that must hold before this is exposed to the internet |

### Locker regression checks

```bash
python -m pytest tests/test_lockers.py tests/test_web_app.py tests/test_notify.py tests/test_repository.py tests/test_scheduler.py
```

These tests cover the same five columns in the page and exports, two- and five-year
expiry calculations, leap-day anniversaries, missing/expired/current cells, and the
inclusive 30-day warning boundary. They also check legacy database migration, invalid
calendar dates and out-of-range expiry years, immediate renewal scheduling, preserving
locker details when Supervisor is cleared, and omitting removed staff.

Excel assertions inspect date types, cell fills, and leading zeroes. PDF checks verify
table content and colours, markup escaping, empty reports, and pagination. Anonymous
callers cannot access the page or either download. No scan or outbound lookup is needed.

## Fixtures

`tests/conftest.py` provides two fixtures used almost everywhere:

- `settings` — a `Settings` instance with a throwaway secret, one manager address, and a
  database path and an uploads directory under pytest's `tmp_path`.
- `database` — a `Database` built on that path, closed on teardown.

Because `base_url` is `http://testserver`, `Settings.is_local` is true throughout the
suite, which is what keeps the session cookie from being marked `Secure` in tests.

`tests/fixtures/member.html` is a copy of the real Society member page structure. It was
rewritten once already, when the original hand-written fixture turned out to contain an
expiry modal that does not exist in production and had hidden the fact that the scraper's
expiry parsing was dead code.

`tests/fixtures/redcross_valid.html` and `redcross_not_found.html` do the same for the Red
Cross validator, and are trimmed copies of what it actually returns — including the
non-breaking spaces that separate the fields in its result paragraph, which the parser has
to normalise before it can read anything.

!!! warning
    Keep the fixtures honest. A fixture that describes a page the Society or the Red Cross
    does not serve will pass tests for behaviour that cannot work.

## The security tests

`test_security.py` is worth reading on its own. It asserts security properties rather than
exercising features:

- Every write endpoint rejects an anonymous caller and writes nothing.
- The session cookie is `HttpOnly` and `SameSite=Lax`.
- The cookie is *not* marked `Secure` over plain HTTP, because a `Secure` cookie would
  never be sent back and would silently break local development.
- Reflected error messages and staff names are HTML-escaped.
- A staff name containing an apostrophe cannot break out of the removal confirmation's
  JavaScript string.
- An unapproved address is told it has no access, never receives a code, and probing is
  rate-limited.
- A wrong code, a missing pending cookie, and a forged pending cookie each set no session
  cookie.

The rejection notice is a deliberate trade: it is clearer for staff, and it does turn the
sign-in endpoint into an oracle for which addresses are managers. The test that asserts
rate limiting is what makes that trade defensible, and it is commented as such.

## Conventions

- Tests are named as sentences: `test_removing_staff_takes_them_off_the_roster`.
- A test asserts one behaviour. Parametrise rather than looping inside a test.
- No network access. If a new test needs the Society, it needs a fixture instead.
- When a bug is fixed, the test comes with it. Several tests in this suite exist because
  they caught a real defect — the scheduler double-fire guard and the award-ordering rules
  among them.

## Adding a certification rule

The most common change to this project is teaching it a new award title. The order of work
matters:

1. Add a case to `test_awards.py` asserting the title maps to the column you expect.
2. Add the pattern to `_RULES` in `lss_report/awards.py`, in the right position — the first
   match wins, and exclusions come before the rules they protect.
3. Run the suite. `test_awards.py` covers the traps that ordering mistakes fall into.

Unrecognised titles are not silent. They appear on the **Diagnostics** page after a scan,
which is where new or renamed Society awards surface.

{{ support() }}
