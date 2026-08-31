# Installation

The project is not published to PyPI. Every supported path installs from a checkout of
the repository or builds the Docker image from it.

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11 or newer | Declared as `requires-python` in `pyproject.toml` |
| pip | any recent | Used to install the package and its dependencies |
| Docker | any recent | Only for the container path |
| Outbound HTTPS to `lifesaving.org` | — | Every scan reads the Society member directory |

Runtime dependencies are resolved by pip and need no separate installation: `requests`
and `beautifulsoup4` for the scan, `openpyxl` for Excel, `reportlab` for PDF, and
`fastapi`, `uvicorn`, `jinja2`, `itsdangerous`, and `python-multipart` for the web
application.

## From source

The recommended path, and the one the rest of these pages assume.

=== "Windows"

    ```powershell
    git clone https://github.com/willtheorangeguy/ucac-certs
    cd ucac-certs
    py -3.11 -m venv .venv
    .venv\Scripts\python.exe -m pip install ".[test]"
    ```

=== "macOS / Linux"

    ```bash
    git clone https://github.com/willtheorangeguy/ucac-certs
    cd ucac-certs
    python3 -m venv .venv
    .venv/bin/pip install '.[test]'
    ```

Dropping `[test]` installs the runtime only. Keep it if you intend to run the suite.

Installing the package registers two console scripts:

| Script | Runs |
|---|---|
| `lss-web` | The web application — the primary entrypoint |
| `lss-report` | The command-line report generator, for maintenance and debugging |

## With the PowerShell helper

`scripts/run-local.ps1` creates the virtual environment, installs the package, performs a
live scan against `staff.json`, and writes `report.pdf` and `report.xlsx`:

```powershell
.\scripts\run-local.ps1
```

Pass `-SkipInstall` on subsequent runs to skip the pip step. The script fails with a
clear message if `staff.json` is absent — copy `staff.example.json` and fill it in first.

!!! warning
    This helper performs live lookups against the Society for every roster entry. Only
    use member IDs that staff have supplied for verification.

## With Docker

The image installs the package and runs `lss-web` as an unprivileged user. It expects a
writable volume at `/data`, where SQLite and its write-ahead log live.

```bash
docker build -t ucac-certs .
```

```bash
docker run --rm -p 8000:8000 \
  -v ucac-certs-data:/data \
  -e SESSION_SECRET=replace-with-at-least-32-random-characters \
  -e MANAGER_EMAILS=manager@example.org \
  -e BASE_URL=http://127.0.0.1:8000 \
  ucac-certs
```

The container starts as root only long enough to take ownership of the mounted volume,
then drops to the `app` user before running the server. Fly.io mounts volumes root-owned,
which is why that step exists.

## Verify the installation

```bash
.venv/bin/python -m pytest
```

```text
131 passed, 1 warning in 9.63s
```

The suite runs entirely against `tests/fixtures/` and makes no network calls, so it is
safe to run anywhere. Both entry points should also answer:

```bash
.venv/bin/lss-web --help
```

```text
usage: lss-web [-h] [--host HOST] [--port PORT] [--env-file ENV_FILE]
               [--seed SEED] [--seed-only]

Run the certification web application.
```

## Upgrading

Pull and reinstall. The database schema is created with `CREATE TABLE IF NOT EXISTS`
statements applied at every startup, so an existing SQLite file is opened in place and
new tables appear on their own.

```bash
git pull
.venv/bin/pip install --upgrade '.[test]'
```

!!! danger
    There is no migration system. A change that alters or drops an existing column will
    not be applied to a database that already has data, and the mismatch surfaces as a
    runtime SQL error rather than a startup failure. Back up `data/lss.sqlite3` — or the
    Fly volume — before upgrading across a schema change.

## Uninstalling

```bash
.venv/bin/pip uninstall lss-certification-report
```

The database is a plain file and is not removed by pip. Delete `data/lss.sqlite3` and its
`-wal` and `-shm` companions to discard the roster, scan history, and audit trail.

!!! warning
    That file holds staff names, Lifesaving Society member IDs, and email addresses.
    Delete it deliberately rather than leaving stray copies around. See
    [Deployment](deployment.md#privacy) for the retention obligations.

{{ support() }}
