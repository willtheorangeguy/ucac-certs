PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS staff (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    society_name  TEXT,
    member_code   TEXT NOT NULL,
    email         TEXT,
    phone         TEXT,
    away          INTEGER NOT NULL DEFAULT 0,
    sms_consent_at TEXT,
    removed_at    TEXT,
    created_at    TEXT NOT NULL
);

-- Member codes are unique only among staff still on the roster, so a removed
-- member can be re-added later without colliding with their own soft-deleted row.
CREATE UNIQUE INDEX IF NOT EXISTS staff_active_code
    ON staff (member_code) WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS scan (
    id           INTEGER PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    detail       TEXT
);

CREATE TABLE IF NOT EXISTS scan_result (
    id           INTEGER PRIMARY KEY,
    scan_id      INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    staff_id     INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    column_code  TEXT NOT NULL,
    expiry_date  TEXT,
    status       TEXT NOT NULL,
    source_award TEXT,
    provisional  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS scan_result_scan ON scan_result (scan_id);

CREATE TABLE IF NOT EXISTS scan_note (
    id       INTEGER PRIMARY KEY,
    scan_id  INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,
    detail   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_token (
    id         INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    email      TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempt (
    id         INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS login_attempt_lookup ON login_attempt (identifier, created_at);

CREATE TABLE IF NOT EXISTS notification_log (
    id          INTEGER PRIMARY KEY,
    staff_id    INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    column_code TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    threshold   INTEGER NOT NULL,
    channel     TEXT NOT NULL,
    sent_at     TEXT NOT NULL
);

-- The dedupe key: one reminder per staff member, award, expiry, ladder step and
-- channel. Re-running a scan or enabling SMS later cannot replay old reminders.
CREATE UNIQUE INDEX IF NOT EXISTS notification_once
    ON notification_log (staff_id, column_code, expiry_date, threshold, channel);

CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT,
    created_at TEXT NOT NULL
);
