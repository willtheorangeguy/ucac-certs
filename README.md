# Lifesaving certification report

This project checks authorized staff records through the Lifesaving Society Alberta and
Northwest Territories verification page and produces a certification grid — one row per
staff member, one column per award type — as an Excel workbook and a PDF.

## How expiry is determined

**The Society does not publish expiry dates.** A member page lists only the award title
and its certification date, so every expiry here is *computed* as certification date plus
a validity period. Some expired cards carry an "Expired On" value instead of a
certification date; those are worked backwards to a certification date so all columns
derive the same way.

| Column | Award | Valid for |
| --- | --- | --- |
| NL | National Lifeguard | 2 years |
| SI | Swim Instructor | 2 years |
| LSI | Lifesaving Instructor | 2 years |
| FA | First Aid | 2 years |
| CPR-C | CPR Level C | 1 year |
| O2 | O2 Administration | 2 years |

First aid is deliberately stricter than the certificate: the issuing body grants three
years, the Aquatic Centre honours two. Because house policy for FA and CPR-C is shorter
than the Society's, those two columns are not cross-checked against the Society's own
current/expired flag — the other four are, and disagreements land in Diagnostics.

Where a member has several awards in one column (an original plus recerts), the latest
expiry wins. An award issued for a different purpose that still counts — first aid
credited from "Lifesaving CPR C & AED" — is marked provisional and loses to a
purpose-issued award. Red Cross first aid is not yet collected; those cells fall back to
the provisional Society award or show grey.

## Cell colours

| Colour | Meaning |
| --- | --- |
| Red `#FF6565` | Expired |
| Yellow `#FFD13F` | Expires within 30 days |
| Grey `#808080` | No award on record |
| Pink `#FFC7CE` | Member lookup failed — the row carries no data |

## Local setup and test run

Python 3.11 or newer is required.

```powershell
.\scripts\run-local.ps1
```

The script creates `.venv`, installs the application and test dependencies, performs the
live lookups, and writes `report.pdf` and `report.xlsx`. Subsequent runs can skip
installation with `-SkipInstall`. To run the tests independently:

```powershell
.venv\Scripts\python.exe -m pytest
```

The collector deliberately sends requests one at a time with a delay — a full roster takes
about a minute. Only use Member IDs that staff have supplied for verification, and stop
the run if the Society asks you to.

## Roster

`staff.json` is a JSON array of `name`, `memberCode`, and an optional `away` flag that
moves the member into the "Away Spring / Summer" section. It is ignored by Git.

To rebuild it from a certification form PDF:

```powershell
.venv\Scripts\python.exe scripts\extract_roster.py "Cert Form June 2024.pdf" staff.json
```

Names are only a cross-check — lookups key off the Member ID. A name that differs from the
Society's record is reported as a warning and the Society's spelling is used; a Member ID
that returns someone else is a hard error.

## Diagnostics

The workbook's second sheet lists anything needing a human:

- **Lookup error** — the Member ID was not found. Verify it against the Society.
- **Name warning** — the roster spelling differs from the Society's.
- **Unmapped award** — an award title matching no rule. If it should count towards a
  column, add it to `_RULES` in `lss_report/awards.py`.
- **Status disagreement** — our computed expiry contradicts the Society's own flag.
- **Provisional** — a first aid cell credited from a CPR award.

## Email delivery

`--email` still sends both attachments over SMTP if `.env` is configured (copy
`.env.example`). This path is being replaced by the web application's Resend integration
and is not being developed further.

## CI

GitHub Actions runs the tests only. No scraping, roster data, or credentials belong in the
repository — the deployed server performs all certification work.
