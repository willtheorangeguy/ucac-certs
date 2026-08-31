# Known Issues — ucac-certs

Concrete defects and gaps found while writing this repository's documentation.
**Nothing here was changed** — each one needs a code, configuration, or
licensing decision rather than a documentation one.

Ordered by severity. This directory is excluded from the published documentation site.

**8 open:** 1 high, 5 medium, 2 low.

## 1. The repository is public with no licence

**Severity:** High
**Where:** Repository root; `pyproject.toml`

**What:** There is no `LICENSE` or `LICENSE.md` file, and `pyproject.toml` declares no
`license` field or classifier. The repository's visibility is public.

**Why it matters:** Public code with no licence is "all rights reserved" by default. Nobody
can legally copy, modify, or reuse it, which is the opposite of what publishing it implies.
It also blocks the account's standard README structure, which mandates a `## License`
section, and leaves the documentation site's About group without the Licence page every
other site in the account has.

**Suggested fix:** Add `LICENSE.md` and a matching `license` field in `pyproject.toml`. MIT
matches the rest of the account, but the choice belongs to the owner — this repository
carries an institution's name and encodes its internal policy, so a deliberate decision is
worth more than a default.

## 2. Deploying to production is not gated on the tests

**Severity:** Medium
**Where:** `.github/workflows/deploy.yml`

**What:** `deploy.yml` triggers on `push` to `main`. `ci.yml` triggers on the same event.
They are independent jobs with no `needs:` or `workflow_run` relationship, so they race.

**Why it matters:** A commit whose tests fail still deploys. The target is the live instance
holding staff names, Lifesaving Society member IDs, and email addresses, and it is the only
copy — there is no staging environment to catch it afterwards.

**Suggested fix:** Trigger the deploy from `workflow_run` on a successful CI run, or move
the test step into `deploy.yml` as a job that the deploy job `needs:`. The second is
simpler and keeps the failure visible in one place.

## 3. Every commit to main redeploys, including documentation-only ones

**Severity:** Medium
**Where:** `.github/workflows/deploy.yml`

**What:** The workflow has no `paths` or `paths-ignore` filter, so any push to `main`
rebuilds the image and replaces the running machine.

**Why it matters:** Now that the repository carries a documentation site, routine prose
edits restart production. Each restart is brief but not free: it interrupts any scan in
progress, and an interrupted scan is left in a bad state — see issue 4.

**Suggested fix:** Add a `paths-ignore` covering `docs/**`, `overrides/**`, `mkdocs.yml`,
`**.md`, and the two documentation workflows.

## 4. A scan interrupted by a restart is never marked failed

**Severity:** Medium
**Where:** `lss_report/web/scans.py`; `lss_report/web/repository.py`

**What:** `ScanRunner` runs the scan on a daemon thread. If the process exits while a scan
is running — a deploy, a machine restart, an out-of-memory kill — the `scan` row keeps
`status = 'running'` with `finished_at` still `NULL`. Nothing reconciles those rows at
startup.

**Why it matters:** The dashboard reads `ScanRepository.latest()` for its status line, so
it reports a scan permanently in progress, and an operator cannot tell it apart from a real
one. Exports and the reminder schedule read `latest_complete_id()` and are unaffected, so
the tool keeps working while appearing stuck, which is the confusing combination.

**Suggested fix:** On startup, mark any scan still `running` as `failed` with a detail such
as "interrupted by a restart". No scan can legitimately survive a process boundary, so the
rule needs no timeout heuristic.

## 5. An out-of-range SCAN_HOUR or REMINDER_HOUR is accepted silently

**Severity:** Medium
**Where:** `lss_report/web/settings.py`, `load_settings`

**What:** Both values are parsed with `int()` and stored without a range check.
`SCAN_HOUR=99` loads successfully and produces `scan_hour = 99`.

**Why it matters:** The scheduler fires on `now.hour == settings.scan_hour`, which never
matches a value outside 0–23. The weekly scan simply never runs, the grid quietly goes
stale, and reminders are computed from a scan that keeps getting older. For a tool whose
purpose is noticing expired lifeguard certifications, silent staleness is the worst
available failure mode, and nothing surfaces it.

**Suggested fix:** Validate `0 <= value <= 23` in `load_settings` and raise
`ConfigurationError`, which is already caught and reported cleanly.

## 6. The roster can be passed through argv and the environment

**Severity:** Medium
**Where:** `lss_report/cli.py`

**What:** The CLI accepts the entire roster as a JSON string, either through the
`STAFF_JSON` environment variable or through a hidden `--staff-json` flag suppressed from
`--help`. Both are legacy paths from a GitHub Actions workflow that has since been deleted.

**Why it matters:** Command-line arguments are readable by other processes on the machine
and are written to shell history. The roster is staff names and Lifesaving Society member
IDs. This directly contradicts the design decision recorded everywhere else in the project
— that the repository and CI never hold roster data — while leaving the mechanism in place.

**Suggested fix:** Remove both. `--staff-file` covers every remaining use, and
`load_staff_json` stays as the shared implementation.

## 7. A non-numeric hour exits with a traceback instead of the clean error

**Severity:** Low
**Where:** `lss_report/web/settings.py`; `lss_report/web/server.py`

**What:** `ConfigurationError` subclasses `ValueError`, so `except ConfigurationError` in
`server.py` does not catch the bare `ValueError` that `int()` raises. `SCAN_HOUR=noon` exits
with `ValueError: invalid literal for int() with base 10: 'noon'` and a full traceback,
rather than the single-line `Configuration error: ...` message and exit code 2 that every
other bad setting produces.

**Why it matters:** Cosmetic, but it is the difference between a deployment error somebody
reads and one they escalate. Every other configuration mistake is already handled well,
which makes this one more surprising, not less.

**Suggested fix:** Parse the two hour values inside a `try` and re-raise as
`ConfigurationError`. The same change fixes issue 5.

## 8. A back-computed certification date uses only the first column's validity

**Severity:** Low
**Where:** `lss_report/scraper.py`, `parse_member_page`

**What:** A card carrying only an `Expired On` value has its certification date recovered
with `certification_date_from_expiry(columns[0][0], expired_on)` — the validity of the
*first* column the award maps to. An award mapping to two columns with different periods,
such as a combined first aid and CPR-C award (2 years and 1 year), recovers a date correct
for `FA` and a year early for `CPR-C`, so the derived CPR-C expiry is a year before the
published one.

**Why it matters:** The tool reports a wrong expiry date. The error is conservative — the
derived date is always earlier than the published one, so it can never show an expired
certification as current — and it only affects cards that are already expired, which a
recert normally supersedes. The practical cost is a confusing entry on the Diagnostics
page rather than a missed expiry.

**Suggested fix:** Store the published `Expired On` value directly for the column it was
published against, instead of round-tripping it through a certification date. Failing that,
derive a separate certification date per column.
