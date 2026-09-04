# Testing

The suite is 229 tests across 18 files and takes about ten seconds. It makes no network
calls — the Society and the Red Cross are each represented by stored HTML fixtures — so it
is safe to run anywhere, including CI.

## Running the tests

```bash
.venv/bin/python -m pytest
```

```text
229 passed, 2 warnings in 7.22s
```

Configuration lives in `pyproject.toml`: `testpaths` is `tests`, and `addopts` is `-q`, so
plain `pytest` from the repository root does the right thing.

Both warnings are deprecation notices from dependencies — `httpx` in the Starlette test
client, and an `anyio` alias — not from this project.

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
| `test_pdf.py` | The grid renders, and fits one page |
| `test_config.py` | Dotenv parsing, `staff.json` validation and its rejections |
| `test_cli.py` | The `lss-report` entry point |
| `test_repository.py` | Roster CRUD, soft delete, scan storage, manual dates, the schema migration, the reminder schedule |
| `test_auth.py` | Token issue and redeem, single use, expiry, rate limiting |
| `test_notify.py` | Resend delivery, reminder text, deduplication |
| `test_scheduler.py` | Weekly and daily firing, and that neither can double-fire |
| `test_files.py` | What an upload has to be to be stored, the size limit, and generated names |
| `test_web_app.py` | Routes, redirects, roster verification at entry, the edit panel, certificate copies |
| `test_web_render.py` | Every page rendered against stored scan data |
| `test_security.py` | The properties that must hold before this is exposed to the internet |

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

`test_security.py` is worth reading on its own. It asserts twelve properties rather than
exercising features:

- Every write endpoint rejects an anonymous caller and writes nothing.
- The session cookie is `HttpOnly` and `SameSite=Lax`.
- The cookie is *not* marked `Secure` over plain HTTP, because a `Secure` cookie would
  never be sent back and would silently break local development.
- Reflected error messages and staff names are HTML-escaped.
- A staff name containing an apostrophe cannot break out of the removal confirmation's
  JavaScript string.
- An unapproved address is told it has no access, never receives a token, and probing is
  rate-limited.
- A forged sign-in token sets no cookie.

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
