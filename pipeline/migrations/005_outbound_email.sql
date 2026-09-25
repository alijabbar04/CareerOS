-- T-023: an inspectable, fail-closed record for Gmail drafts and send attempts.
-- The message body stays in an approved application file, not in this table or events.

CREATE TABLE IF NOT EXISTS outbound_emails (
  id                 INTEGER PRIMARY KEY,
  application_id     INTEGER NOT NULL REFERENCES applications(id),
  posting_id         INTEGER NOT NULL REFERENCES postings(id),
  contact_id         INTEGER NOT NULL REFERENCES contacts(id),
  sender             TEXT NOT NULL,
  recipient          TEXT NOT NULL,
  action             TEXT NOT NULL CHECK (action IN ('draft', 'send')),
  content_sha256     TEXT NOT NULL,
  status             TEXT NOT NULL CHECK (status IN ('pending', 'drafted', 'sent', 'uncertain')),
  gmail_id           TEXT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at       TEXT,
  UNIQUE (application_id, contact_id, action, content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_outbound_emails_action_day
  ON outbound_emails(action, created_at, status);
