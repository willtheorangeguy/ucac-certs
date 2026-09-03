# Getting started

By the end of this page you have the web application running locally, signed in as a
manager, with at least one staff member on the roster and a scan behind you.

## Prerequisites

| Requirement | Minimum version | Check with |
|---|---|---|
| Python | 3.11 | `python --version` |
| pip | any recent | `python -m pip --version` |
| Network access to `lifesaving.org` | — | `curl -I https://www.lifesaving.org` |

Windows users can substitute `py -3.11` for `python` throughout.

## Install

From the repository root:

```bash
python -m venv .venv
.venv/bin/pip install '.[test]'
```

On Windows the interpreter lives at `.venv\Scripts\python.exe` instead. Docker and the
other install paths are covered in [Installation](installation.md).

## First run

1. Create a settings file from the example and fill in the two required values.

    ```bash
    cp .env.example .env
    ```

    `SESSION_SECRET` must be at least 32 characters, and `MANAGER_EMAILS` must list at
    least one address. Anything else can stay at its default for now. Both are validated
    at startup — see [Configuration](configuration.md).

2. Start the server.

    ```bash
    .venv/bin/python -m lss_report.web.server --env-file .env
    ```

    ```text
    2026-08-30 22:36:41,651 WARNING lss_report.web.app RESEND_API_KEY is unset; login links will be written to the log instead.
    INFO:     Started server process [10928]
    INFO:     Waiting for application startup.
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
    ```

    That warning is expected locally. It is how you sign in without an email provider.

3. Open `http://127.0.0.1:8000`, which redirects to the sign-in page. Enter one of the
    addresses from `MANAGER_EMAILS` and submit.

4. Read the sign-in link out of the server log and open it.

    ```text
    WARNING lss_report.web.notify Email not configured; would send to manager@example.org: Sign in here (valid 15 minutes, single use):

    http://127.0.0.1:8000/auth?token=3jw2o5jSQu3iL42DtOUhDI_gr__lvxxxfkMPSfd5WpE
    ```

    The link is valid for 15 minutes and works once. Opening it sets a session cookie
    that lasts 30 days and lands you on the dashboard.

    !!! warning
        The link is built from `BASE_URL`, not from the port you passed on the command
        line. If you started the server on a non-default port, `BASE_URL` must match or
        the link points at the wrong host.

5. On the **Staff** page, select **Add a staff member**. Give a real name and a real
    Lifesaving Society member ID.

    If they hold first aid through the Canadian Red Cross, put the certificate number in
    the same panel. Below the details is a date field per column, for a certification
    earned through a third party — leave those blank for now.

    Both numbers are checked before the row is saved, so this step takes a second or two.
    A bad member ID is rejected with `Member ID was not found.`, a certificate number that
    does not match the person's last name is rejected too, and nothing is written either
    way. The pencil on the row reopens the same panel to change any of it later.

6. Return to the dashboard and select **Run scan**.

    The scan runs on a background thread and the page returns immediately. Lookups are
    deliberately rate-limited to one every 1.1 seconds, so a full 45-person roster takes
    roughly a minute. Reload the dashboard to see the grid fill in.

## What just happened

The scan fetched each roster member's award history from the Society — and, for anyone
with a certificate number on file, their Red Cross certificate — mapped every award title
onto one of the six tracked columns, and computed an expiry date as the certification date
plus that column's validity period. Neither source publishes an expiry this tool can use as
printed, so this computation is the whole point of it.

Results were written to the SQLite database as a numbered scan, which is what the
dashboard, the Excel export, the PDF export, and the reminder schedule all read from.
Nothing is fetched again until the next scan.

## Next steps

- [Configuration](configuration.md) — every environment variable, and the schedule
- [Architecture](architecture.md) — how a scan turns into a grid
- [API](api.md) — the HTTP routes, the CLI, and the Python package
- [Deployment](deployment.md) — putting it on Fly.io with real email

{{ support() }}
