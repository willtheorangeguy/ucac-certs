# Deployment

The production deployment is a single always-on Fly.io machine in Toronto with a 1 GB
volume for SQLite, sending mail through Resend. Everything the application does — scanning,
exporting, and sending reminders — happens on that machine. The repository only holds code
and runs tests.

## Prerequisites

| Requirement | Why |
| --- | --- |
| A Fly.io account and `flyctl` | Hosts the application and the volume |
| A Resend account with a verified sending domain | Sign-in codes and reminder emails |
| A domain you control | Resend verifies ownership through DNS records |

## Configuration on Fly

`fly.toml` is checked in and carries the machine shape, the mount, and the health check.
Secrets are never in the repository.

```toml title="fly.toml"
app = "ucac-certs"
primary_region = "yyz"

[mounts]
  source = "lss_data"
  destination = "/data"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1
```

Toronto keeps Canadian staff personal information on Canadian infrastructure, which matters
under PIPEDA for a Canadian institution. Seattle is physically closer to Calgary but
US-hosted.

## First deployment

1. Create the application without deploying, so the checked-in `fly.toml` is used as-is.

    ```bash
    fly launch --no-deploy
    ```

2. Create the volume in the same region as the app.

    ```bash
    fly volumes create lss_data --size 1 --region yyz
    ```

3. Set the secrets. `SESSION_SECRET` should be freshly generated, not typed.

    ```bash
    fly secrets set \
      SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
      MANAGER_EMAILS="manager@example.org,second@example.org" \
      RESEND_API_KEY="re_..." \
      MAIL_FROM="certifications@example.org" \
      BASE_URL="https://ucac-certs.fly.dev"
    ```

    !!! danger
        `BASE_URL` must match the deployed origin exactly. An `https` value is what marks
        the session cookie `Secure`, so a wrong value quietly weakens every session.

4. Deploy.

    ```bash
    fly deploy
    ```

5. Confirm the machine is healthy before trusting it.

    ```bash
    curl -s https://ucac-certs.fly.dev/healthz
    ```

    ```json
    {"ok":true}
    ```

6. Turn on volume snapshots and check they exist **before** the first real scan lands.

    ```bash
    fly volumes snapshots list <volume-id>
    ```

## Seeding the roster

The database is the roster of record. `staff.json` is only a one-time import, and it is
gitignored because it carries staff names and member IDs, so it cannot ride along in the
image.

Copy it onto the volume and import it in place:

```bash
fly ssh console -C "lss-web --seed /data/staff.json --seed-only"
```

Seeding is ignored once the database has any staff, so the command is safe to repeat. The
alternative is to add everyone through the **Staff** page, which has the advantage of
verifying each member ID against the Society as it goes.

## Email with Resend

Resend needs a verified sending domain before it will deliver anything. Add the DNS records
it gives you at your registrar, wait for verification, then set `MAIL_FROM` to an address
on that domain.

| Limit | Value |
| --- | --- |
| Free tier | 3,000 emails per month |
| Daily cap | 100 emails per day |
| Recipients | Counted individually |

Because each recipient counts separately, the application sends one message per person
rather than batching a reminder to several addresses.

With `RESEND_API_KEY` unset the application still runs, but nothing is sent and nobody can
sign in on a deployed instance — the code is only written to the log, and production logs
deliberately omit it.

### Deliverability

A brand-new sending domain mailing an institutional address fits the shape of a phishing
message, and it will be filtered before anyone reports a bug. The sign-in mail carries a
six-digit code and no URL at all, which is the single biggest thing that helps: a link
scanner has nothing to follow. Three more:

- Add a DMARC record. Start permissive and tighten later:

    ```text
    _dmarc  TXT  "v=DMARC1; p=none; rua=mailto:you@example.org"
    ```

- Warm the domain up by sending a handful of real messages before the first bulk reminder
  pass.
- Ask the recipient organisation's IT to allowlist the sending domain. For a small internal
  tool this is usually a short conversation and it is more reliable than tuning records.

## Continuous deployment

Pushing to `main` deploys, provided the commit touches something outside the documentation
and the tests pass. `.github/workflows/deploy.yml` runs the suite first and deploys only on
success, authenticating with a `FLY_API_TOKEN` repository secret — a deploy token scoped to
this application rather than a personal organisation token.

```bash
fly tokens create deploy -x 8760h
```

Add the output as the `FLY_API_TOKEN` secret in the repository settings.

A deployment replaces the machine, so a scan running at that moment loses its thread. The
application closes out such a scan on the next startup, marking it `failed` with
"Interrupted by a restart." rather than leaving it stuck in progress. Re-run it from the
dashboard.

## Why the machine stays on

`auto_stop_machines` is `false` deliberately. The scheduler is a thread inside the
application process, so a stopped machine runs no weekly scan and sends no reminders.

Moving the scheduler to a second Fly machine does not solve it either: a Fly volume attaches
to exactly one machine at a time, and both machines would need the same SQLite file.

Scale-to-zero is possible, but it means moving the schedule out to an external cron that
calls an authenticated trigger endpoint — the inbound request is what wakes the machine —
and running with `DISABLE_SCHEDULER=1` so the in-process timer does not double-fire.

The checked-in configuration uses a `shared-cpu-1x` with 1 GB RAM to accommodate the
web application and one headless Firefox session. Monitor memory during a full scan
before reducing that allocation. The image includes the browser and its Linux libraries;
the build checks browser launch as the unprivileged app user. `SOCIETY_TRANSPORT=firefox`
is available for server-side diagnostics. Production uses `SOCIETY_TRANSPORT=worker`
because Cloudflare challenges Firefox from Fly's network. Check current Fly pricing for
the compute allocation and persistent volume; both contribute to hosting costs.

A successful local Firefox scan does not establish access from Fly's network. Validate
a deployed scan before relying on the schedule. Cloudflare can still challenge Firefox
on Fly; the app records that failure and keeps the latest completed scan available.
If verification remains blocked, arrange approved automated access with the Society.

## Local scanner

The production scan button and Monday scheduler delegate Society navigation to a local
Windows computer. The app continues to parse pages, save scan results, render exports,
and send reminders. The local process makes outbound HTTPS requests only; no inbound
port or firewall exception is needed.

Configure `LOCAL_WORKER_TOKEN` as a separate random secret of at least 32 characters in
Fly secrets and in an ignored local `.env.worker`. Keep the application's session secret
and manager credentials on Fly. Restrict the local file's permissions to its user.

Start from the repository's credential-free template:

```powershell
Copy-Item worker.example.env .env.worker
```

Fill in the dedicated worker credential before starting the scanner. The template's
empty token is intentionally rejected by the worker.

```dotenv title=".env.worker"
WORKER_BASE_URL=https://ucac-certs.fly.dev
LOCAL_WORKER_TOKEN=replace-with-a-separate-random-secret
```

Install and start the scanner, with automatic startup at Windows sign-in:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-worker.ps1 -Install -EnableStartup
```

This creates `.venv-worker`, installs the pinned Firefox version, and adds a
**UCAC Local Scanner** shortcut to the current user's Startup folder. The process runs
without a console window and reuses Firefox while work is arriving. After 60 idle
seconds it closes Firefox; polling continues. Logs rotate at 1 MB in
`data/local-worker.log` and contain status information rather than member IDs or pages.

To restart the scanner after it stops:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-worker.ps1
```

Only one local scanner instance can hold the local lock. Removing the Startup shortcut
disables automatic startup. Keep the computer awake and signed in during a scan. A
scheduled Monday scan also requires the worker to be available at the configured hour;
run a manual scan after reconnecting if the scheduled attempt failed while offline.
The server never silently falls back to Fly-based Society lookups.

## Operations

| Task | Command |
| --- | --- |
| Tail logs | `fly logs` |
| Open a shell | `fly ssh console` |
| Check machine state | `fly status` |
| List secrets, names only | `fly secrets list` |
| Rotate a secret | `fly secrets set NAME=value` — this restarts the machine |
| Restart | `fly apps restart ucac-certs` |
| List snapshots | `fly volumes snapshots list <volume-id>` |

Rotating `SESSION_SECRET` invalidates every existing session cookie and signs all managers
out. That is the correct response to a suspected leak, and worth knowing before doing it
casually.

### Backups

The volume holds the only copy of the roster, the scan history, the reminder log, the audit
trail, and the uploaded copies of certificates. Fly's scheduled snapshots cover machine
loss; they do not cover a bad write, because a corrupted database is faithfully snapshotted
too. For a copy you control:

```bash
fly ssh console -C "cat /data/lss.sqlite3" > backup.sqlite3
fly ssh console -C "tar -cf - -C /data uploads" > uploads.tar
```

The uploads directory sits beside the database on the same volume, so both are on one
snapshot — but a database backup taken on its own leaves the copies behind.

!!! danger
    That file contains staff names, Lifesaving Society member IDs, and email addresses.
    Treat a local backup as the personal information it is: store it encrypted, and delete
    it when you are done.

## Privacy

The application holds staff personal information under PIPEDA — names, Society member IDs,
email addresses, and uploaded copies of their certificates. The controls that exist:

- Access is limited to the `MANAGER_EMAILS` allowlist, which is re-checked on every request
  rather than only at sign-in.
- Every roster change is written to the `audit` table with the actor and a timestamp.
- Data is hosted in Canada.
- Session cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` whenever `BASE_URL` is
  `https`.

There is no automatic retention limit. Scan results accumulate indefinitely, and removed
staff keep their historical rows — and their uploaded copies — so past reports stay
reproducible. Deciding how long that
should be kept is a policy question, not a code one.

{{ support() }}
