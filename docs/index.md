# UCAC Certifications

UCAC Certifications tracks lifeguard certification expiry for the University of Calgary
Aquatic Centre. It reads each staff member's award history from the Lifesaving Society
Alberta and Northwest Territories member directory, computes when every certification
lapses, and presents the result as a colour-coded grid that managers can read at a glance,
download as Excel or PDF, and have emailed to staff before an award runs out.

The Society publishes award titles and certification dates but **not** expiry dates. Every
expiry on this grid is computed, which is the single fact that explains most of how the
project is built.

## Key features

- One row per staff member, one column per award type — National Lifeguard, Swim
  Instructor, Lifesaving Instructor, Standard First Aid, CPR-C, and O2 Administration.
- Expiry computed from the certification date plus a per-column validity period, so the
  grid is accurate even though the source publishes no expiry.
- Red for expired, yellow for expiring within 30 days, grey for nothing on record.
- A manager web application with magic-link sign-in, roster management, and one-click
  Excel and PDF export.
- Member IDs verified against the Society at the moment a staff member is added, so a
  typo is caught at entry instead of surfacing as a blank row a week later.
- Automatic email reminders at 30, 14, and 7 days before expiry, deduplicated so a
  restart or a repeat scan cannot send the same reminder twice.
- A diagnostics page listing failed lookups, name mismatches, unrecognised awards, and
  places where the computed expiry disagrees with the Society's own current/expired flag.

## Quick start

```bash
pip install '.[test]'
cp .env.example .env
python -m lss_report.web.server --env-file .env
```

Open `http://127.0.0.1:8000`. Without a `RESEND_API_KEY`, the sign-in link is written to
the log rather than emailed, which is how you sign in locally.

See [Getting started](getting-started.md) for the full walkthrough.

## Where to next

<div class="wt-grid" markdown>

[:material-rocket-launch: **Getting started**<br>From zero to a running dashboard](getting-started.md){ .wt-card }

[:material-download: **Installation**<br>Every supported install method](installation.md){ .wt-card }

[:material-tune: **Configuration**<br>Environment variables and schedule settings](configuration.md){ .wt-card }

[:material-sitemap: **Architecture**<br>How the scan, grid, and web app fit together](architecture.md){ .wt-card }

[:material-api: **API**<br>HTTP routes, CLI flags, and the Python package](api.md){ .wt-card }

[:material-rocket: **Deployment**<br>Running it on Fly.io with Resend](deployment.md){ .wt-card }

</div>

## Support

{{ support() }}
