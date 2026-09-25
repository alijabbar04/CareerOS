-- T-015: incremental Gmail poller state and inspectable classifier metadata.

ALTER TABLE emails ADD COLUMN from_name TEXT;
ALTER TABLE emails ADD COLUMN link_domains_json TEXT;
ALTER TABLE emails ADD COLUMN gmail_labels_json TEXT;
ALTER TABLE emails ADD COLUMN deadline_phrase TEXT;
ALTER TABLE emails ADD COLUMN deadline_confidence TEXT CHECK (
  deadline_confidence IN ('explicit','inferred','unknown')
);
ALTER TABLE emails ADD COLUMN model_json TEXT;

CREATE TABLE IF NOT EXISTS inbox_state (
  provider          TEXT PRIMARY KEY,
  account           TEXT,
  history_id        TEXT,
  last_polled_at    TEXT,
  last_full_sync_at TEXT,
  notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_emails_category_received
  ON emails(category, received_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_assessments_invite_kind
  ON assessments(invite_email_id, kind)
  WHERE invite_email_id IS NOT NULL;

-- An acknowledgement is a real state, not a terminal one. The initial graph
-- omitted its normal next steps, which the confirmed inbox workflow needs.
DROP TRIGGER IF EXISTS applications_status_transition_guard;
CREATE TRIGGER applications_status_transition_guard
BEFORE UPDATE OF status ON applications
WHEN NEW.status IS NOT OLD.status
BEGIN
  SELECT RAISE(ABORT, 'invalid application status transition')
  WHERE NOT (
    (OLD.status = 'draft' AND NEW.status = 'ready-for-review') OR
    (OLD.status = 'ready-for-review' AND NEW.status = 'approved') OR
    (OLD.status = 'approved' AND NEW.status = 'submitted') OR
    (OLD.status = 'submitted' AND NEW.status IN ('acknowledged','assessment','interview','rejected','ghosted')) OR
    (OLD.status = 'acknowledged' AND NEW.status IN ('assessment','interview','rejected')) OR
    (OLD.status = 'assessment' AND NEW.status IN ('interview','rejected')) OR
    (OLD.status = 'interview' AND NEW.status IN ('offer','rejected')) OR
    (OLD.status = 'offer' AND NEW.status IN ('accepted','rejected')) OR
    NEW.status = 'withdrawn' OR
    NEW.status = 'rejected'
  );
END;
