# FAQ

Questions about why the tool behaves the way it does. Error messages with a fix are in
[Configuration](configuration.md#troubleshooting).

## The dates

???+ question "Where do the expiry dates come from? I can't find them on the Society's site."

    They aren't there. A Society member page publishes a name, a member ID, and each
    award's title and certification date — and nothing else. Every expiry on this grid is
    computed as the certification date plus that column's validity period.

    This is the single most important thing to know about the tool. It is also why an
    award title that no rule recognises is a real problem rather than a cosmetic one: an
    unmapped award contributes no expiry to any column, and the cell goes grey.

??? question "Why does a first aid date here not match my certificate?"

    Deliberately. The issuing body grants first aid for three years; the Aquatic Centre
    honours two. The grid shows the date the Aquatic Centre stops accepting the
    certification, which is a year earlier than the one printed on the card.

    This holds for a Red Cross card too, and it is the reason the Red Cross expiry is not
    used as printed: the validator publishes an expiry three years out, that expiry is
    worked back to the course date, and the two-year house policy is applied to it.

    It is one number in `lss_report/awards.py` if the policy changes, and one in
    `lss_report/redcross.py` if the Red Cross changes how long a card runs.

??? question "Why is a cell grey instead of red?"

    Grey means no award on record for that column at all, which is different from an award
    that has lapsed. A red cell is a certification the tool found and computed as expired;
    a grey cell is one it never found.

    For first aid, check the roster entry has a Red Cross certificate number on it. Without
    one there is nothing to validate, and the cell falls back to a provisional credit from a
    CPR award, or shows grey.

??? question "Someone's first aid is through a course neither the Society nor the Red Cross knows about."

    Open the pencil on their roster row and put the date the course was passed into that
    column. The expiry is computed from it the same way a scanned award's is.

    It is an extra source rather than an override, so it only shows where it beats what the
    scan found. That is deliberate: a stale hand-entered date cannot hide a current award.
    Clearing the field removes the entry.

??? question "A Red Cross certificate number is on the roster but the cell is still grey."

    Check **Diagnostics** for a `redcross` note. Either the number and the last name do not
    go together — the validator matches on both — or the validator was unreachable when the
    scan ran.

    Neither case affects the rest of the row. The Society's awards are collected
    independently, and a Red Cross outage cannot fail a scan.

??? question "What does a status disagreement on the Diagnostics page mean?"

    The computed expiry says one thing and the Society's own current-or-expired flag says
    another. Every disagreement is worth reading, because the check has caught real
    mapping bugs — an award titled "2023 National Lifeguard Update" being read as a
    National Lifeguard certification, for one.

    First aid and CPR-C are excluded from the check. House policy is shorter than the
    Society's for both, so they are *expected* to disagree and including them would bury
    the real signal under dozens of false ones.

## Scans

??? question "Why does a scan take a minute?"

    One request per staff member, spaced 1.1 seconds apart, on purpose, plus a second one
    for each member who holds a Red Cross certificate. Forty-five people is about fifty
    seconds of deliberate waiting, more if most of them have both.

    The tool reads two third parties' websites on behalf of staff who have supplied their
    member IDs and certificate numbers for verification. Going slowly is the courtesy that
    keeps that reasonable. If either body asks you to stop, stop.

??? question "The page came straight back — did the scan actually run?"

    Yes. The scan runs on a background thread and the request returns immediately, because
    holding an HTTP request open for a minute would be worse. Reload the dashboard, or
    check `/scan/status`, to see progress.

    Only one scan runs at a time. Pressing the button again while one is running does
    nothing.

??? question "Can I run a scan more than once a day?"

    Manually, yes, as often as you like. The scheduled Monday scan is what is limited to
    once a day — a repeat on the same date is skipped, so a restart cannot cause a second
    one.

    Each scan replaces the results it stores, and the dashboard always reads the most
    recent completed scan.

## Reminders

??? question "Why didn't someone get their reminder?"

    Three possibilities, and all three are silent by design:

    - They have no email address on their roster entry. Reminders skip them. The
      **Reminders** page flags this in red on the forward schedule.
    - `RESEND_API_KEY` is unset, so nothing is sent at all.
    - The reminder was already sent. Each combination of person, award, expiry, ladder
      step, and channel sends exactly once, ever.

??? question "Can I resend a reminder, or send one early?"

    No. The **Reminders** page is read-only: the schedule for the next 60 days above,
    everything already sent below. Sending is entirely automatic, at 30, 14, and 7 days
    before expiry.

    That is a deliberate simplification, not an oversight. A manual send button would need
    its own answer to the deduplication question, and the automatic ladder covers the real
    need.

??? question "Someone's certification expires in 40 days but they aren't on the schedule. Why?"

    The forward schedule looks 60 days ahead and lists a row for each ladder step that
    falls inside that window. A certification expiring in 40 days has its 30-day reminder
    scheduled 10 days from now, so it should appear.

    If it doesn't, the likely cause is that the schedule is drawn from the most recent
    completed scan. A certification renewed since the last scan, or a person added since,
    won't appear until the next scan runs.

    A date entered by hand is the exception: it is folded in on read, so it moves the
    schedule the moment it is saved.

## The roster

??? question "I removed the wrong person. Can I undo it?"

    Not from the interface. Removal is a soft delete, so their historical scan results are
    still in the database and past reports stay reproducible, but there is no restore
    button.

    Re-adding them works and their member code is free again — but it creates a new roster
    entry, and the old scan results stay attached to the old one.

??? question "Why does adding a staff member take a couple of seconds?"

    The member ID is checked against the Society before the row is saved. A typo is
    rejected immediately with `Member ID was not found.` rather than surfacing as a blank
    row after the next scan, which is how four bad IDs went unnoticed in an earlier
    version of this tool.

    A Red Cross certificate number is checked the same way, against the holder's last
    name. Adding a member runs both checks; editing one runs only the checks whose field
    actually changed, so correcting a phone number is instant.

??? question "What can I upload as a copy of a certificate?"

    A PDF or an image — PNG, JPEG, GIF, WebP, or the HEIC a phone camera produces — up to
    10 MB. The file button on a roster row opens the copies kept for that person.

    The check is on the file's own leading bytes, not its name, so renaming something to
    `.pdf` does not get it past. A copy is evidence for whoever asks to see the card; it is
    never read for dates. Entering a date from a third-party course is still the manual
    date field in the edit panel.

??? question "Where do the uploaded copies actually live?"

    On disk, in an `uploads/` directory beside the database — on the Fly volume in
    production — under a generated random name, with the database holding the original file
    name and who uploaded it. A volume snapshot covers both; a database backup on its own
    does not.

    Deleting a copy is immediate and permanent. Removing a staff member is not: that is a
    soft delete, and their copies stay.

??? question "Why is there a phone field if nothing uses it?"

    An SMS channel was built and then removed. The `phone` and `sms_consent_at` columns
    were left in the schema because dropping a column means a migration against a live
    database, and there is no migration system.

    Nothing reads them. The delivery layer is a `Channel` protocol with one implementation,
    so a second channel can be added later without touching the scheduler or the
    deduplication logic.

## Access

??? question "Why was I signed out?"

    Manager membership is re-checked on every request, not just at sign-in. If your address
    was removed from `MANAGER_EMAILS`, your existing cookie stops working immediately.

    If *everyone* was signed out at once, `SESSION_SECRET` changed. Every cookie is signed
    with it, so rotating it invalidates all of them.

??? question "Why is there no password?"

    There is no password to leak, reuse, or reset. Sign-in is a single-use six-digit code,
    valid for 15 minutes, sent to an address on the manager allowlist. That allowlist is the
    entire security boundary — anyone on it can sign in, and nobody else can.

    It used to be a link in the email. Institutional mail filters treat a link to an
    unfamiliar host as phishing and drop the message, so a code replaced it: there is
    nothing in the mail for a scanner to follow. Six digits is only a million values, so
    redeeming a code is rate limited exactly as requesting one is — five tries per fifteen
    minutes, per address and per IP.

??? question "The sign-in form told me my address has no access. Doesn't that leak who the managers are?"

    Yes, and that is a known trade. Telling someone their address is not approved is much
    clearer than a message that could mean either "check your email" or "you have no
    access", and this is an internal tool for a handful of people.

    What makes it defensible is the rate limit: five attempts per fifteen minutes, counted
    both per address and per IP. Probing the allowlist is possible but slow, and a rejected
    address never has a code created for it. Reverting to a single identical response for
    both cases closes the oracle, and is a two-line change.

{{ support() }}
