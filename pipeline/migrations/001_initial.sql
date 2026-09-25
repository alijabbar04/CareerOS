-- CareerOS database — migration 001: initial schema.
--
-- One SQLite file holds both the claims ledger (the anti-hallucination "brain",
-- see plan/appendix-B-architecture-detail.md B2) and the job-application tracker
-- (B3), merged with the fuller DDL in research/C_tech_stack.md section 2.1.
--
-- Conventions:
--   * timestamps are TEXT in SQLite's own datetime('now') format (UTC,
--     'YYYY-MM-DD HH:MM:SS'); booleans are INTEGER 0/1.
--   * every CREATE is IF NOT EXISTS so this file is safe to re-run by hand;
--     pipeline.db.migrate() also tracks applied versions in schema_migrations
--     so it normally only runs once per version.
--   * PRAGMA foreign_keys / journal_mode are per-connection and set by
--     pipeline.db.connect(), not here.

-- ---------------------------------------------------------------------------
-- Sources: immutable record of every document a claim or posting was read from
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sources (
  id            TEXT PRIMARY KEY,           -- slug, e.g. 'linkedin-experience-2026-09-20'
  path          TEXT NOT NULL,
  kind          TEXT,
  title         TEXT,
  date          TEXT,
  sha256        TEXT,
  ingested_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Companies, contacts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS companies (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
  domain        TEXT,
  sector        TEXT,
  size_band     TEXT,
  hq_location   TEXT,
  careers_url   TEXT,
  ats_type      TEXT,                       -- 'workday', 'greenhouse', 'lever', ...
  priority      INTEGER,
  notes         TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
  id                INTEGER PRIMARY KEY,
  company_id        INTEGER REFERENCES companies(id),
  name              TEXT NOT NULL,
  role              TEXT,
  email             TEXT,
  linkedin_url      TEXT,
  source            TEXT,
  relationship      TEXT CHECK (relationship IN
                      ('recruiter','agency','hiring_manager','employee','alumni','other')),
  last_contact_at   TEXT,
  retain_until      TEXT,
  notes             TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Postings
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS postings (
  id                INTEGER PRIMARY KEY,
  company_id        INTEGER REFERENCES companies(id),
  source            TEXT,                   -- 'greenhouse', 'lever', 'adzuna', 'pasted-url', ...
  external_id       TEXT,
  url               TEXT,
  canonical_url     TEXT UNIQUE,
  title             TEXT NOT NULL,
  location          TEXT,
  remote            TEXT,
  track             TEXT,                   -- e.g. 'ACA', 'fintech ops', 'bank ops'
  salary_min        INTEGER,
  salary_max        INTEGER,
  currency          TEXT DEFAULT 'GBP',
  description       TEXT,
  requirements      TEXT,
  fingerprint       TEXT,                   -- dedupe key (canonical URL / fuzzy title+company / embedding)
  embedding         BLOB,
  class_year_rule   TEXT,
  ai_policy_hint    TEXT,
  rule_score        INTEGER,
  llm_score         INTEGER,
  llm_reasons       TEXT,
  posted_at         TEXT,
  closes_at         TEXT,
  first_seen        TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen         TEXT NOT NULL DEFAULT (datetime('now')),
  status            TEXT NOT NULL DEFAULT 'new' CHECK (status IN
                      ('new','shortlisted','ignored','rejected','expired','applied')),
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT
);

-- ---------------------------------------------------------------------------
-- Applications (the state machine) and their append-only audit trail
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS applications (
  id                            INTEGER PRIMARY KEY,
  posting_id                    INTEGER REFERENCES postings(id),
  company_id                    INTEGER NOT NULL REFERENCES companies(id),
  role_title                    TEXT NOT NULL,
  track                         TEXT,
  cycle                         TEXT,                -- e.g. '2027'
  channel                       TEXT,
  cv_version                    TEXT,
  cover_letter_path             TEXT,
  drafting_mode                 TEXT CHECK (drafting_mode IN
                                  ('drafting_assisted','proofread_only','outline_only')),
  ai_declaration_used           TEXT,        -- the confirmed wording actually shown/ticked, logged verbatim
  status                        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                                  'draft','ready-for-review','approved','submitted','acknowledged',
                                  'assessment','interview','offer','accepted','rejected',
                                  'withdrawn','ghosted')),
  submitted_by                  TEXT CHECK (submitted_by IN ('ali','system-with-approval')),
  confirmation_ref              TEXT,
  submitted_at                  TEXT,
  deadline                      TEXT,
  priority                      INTEGER DEFAULT 3,
  outcome_reason                TEXT,
  next_action                   TEXT,
  next_action_due               TEXT,
  -- Carried into the applications_status_event trigger below so a single UPDATE
  -- statement (see pipeline.db.transition_application) can attach who/what caused
  -- a transition without relying on last_insert_rowid() across statements, which
  -- SQLite resets as soon as the trigger that set it finishes running. Also useful
  -- on its own as "why is this application in its current state".
  last_transition_source        TEXT,
  last_transition_detail_json   TEXT,
  created_at                    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at                    TEXT
);

-- Append-only audit log for every entity's lifecycle (applications, postings, ...).
-- entity_id is polymorphic (paired with `entity`), so it is deliberately not a
-- foreign key to any single table.
CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY,
  occurred_at   TEXT NOT NULL DEFAULT (datetime('now')),
  entity        TEXT NOT NULL,             -- 'application', 'posting', 'assessment', ...
  entity_id     INTEGER NOT NULL,
  type          TEXT NOT NULL,             -- 'status_change', 'note', 'browser_action', ...
  from_status   TEXT,
  to_status     TEXT,
  source        TEXT,                      -- 'ali', 'agent', 'email', 'system', ...
  email_id      INTEGER REFERENCES emails(id),
  detail_json   TEXT
);

-- ---------------------------------------------------------------------------
-- Emails, assessments, documents, question bank, follow-ups, free notes
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS emails (
  id                INTEGER PRIMARY KEY,
  provider          TEXT,                  -- 'gmail', 'outlook', ...
  message_id        TEXT UNIQUE,
  thread_id         TEXT,
  received_at       TEXT,
  from_addr         TEXT,
  from_domain       TEXT,
  subject           TEXT,
  snippet           TEXT,
  body_path         TEXT,
  category          TEXT CHECK (category IN
                      ('assessment_invite','interview_invite','application_ack','rejection','offer','other')),
  vendor            TEXT,                  -- 'SHL', 'HireVue', 'Cappfinity', ...
  classifier        TEXT,
  confidence        REAL,
  extracted_deadline TEXT,
  application_id    INTEGER REFERENCES applications(id),
  processed_at      TEXT,
  handled           INTEGER DEFAULT 0,
  needs_review      INTEGER DEFAULT 0,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assessments (
  id                    INTEGER PRIMARY KEY,
  application_id        INTEGER NOT NULL REFERENCES applications(id),
  kind                  TEXT CHECK (kind IN
                          ('numerical','verbal','sjt','game','video_interview','case','coding','personality','other')),
  vendor                TEXT,
  invite_email_id       INTEGER REFERENCES emails(id),
  invited_at            TEXT,
  deadline              TEXT,
  deadline_confidence   TEXT CHECK (deadline_confidence IN ('explicit','inferred','unknown')),
  link                  TEXT,
  status                TEXT NOT NULL DEFAULT 'invited' CHECK (status IN
                          ('invited','scheduled','completed','expired','cancelled')),
  completed_at          TEXT,
  result                TEXT,
  calendar_event_id     TEXT,
  notes                 TEXT,
  created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
  id                INTEGER PRIMARY KEY,
  application_id    INTEGER REFERENCES applications(id),
  doc_type          TEXT CHECK (doc_type IN ('cv','cover_letter','answers','portfolio','other')),
  path              TEXT NOT NULL,
  version           INTEGER DEFAULT 1,
  sha256            TEXT,
  generated_by      TEXT,
  model             TEXT,
  submitted         INTEGER DEFAULT 0,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS qa_bank (
  id                INTEGER PRIMARY KEY,
  question          TEXT NOT NULL,
  answer            TEXT NOT NULL,
  application_id    INTEGER REFERENCES applications(id),
  competency        TEXT,
  word_limit        INTEGER,
  reuse_ok          INTEGER DEFAULT 1,
  quality           INTEGER,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS followups (
  id                INTEGER PRIMARY KEY,
  application_id    INTEGER REFERENCES applications(id),
  contact_id        INTEGER REFERENCES contacts(id),
  due_at            TEXT NOT NULL,
  action            TEXT NOT NULL,
  done_at           TEXT,
  notes             TEXT
);

CREATE TABLE IF NOT EXISTS notes (
  id            INTEGER PRIMARY KEY,
  entity_type   TEXT NOT NULL,
  entity_id     INTEGER NOT NULL,
  body          TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Claims ledger: the anti-hallucination backbone (see appendix-B section B2)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS claims (
  id                TEXT PRIMARY KEY,          -- e.g. 'C-0042'
  text              TEXT NOT NULL,
  category          TEXT NOT NULL CHECK (category IN (
                      'education','experience','skill','achievement','project','interest',
                      'fact','story','preference','motivation')),
  subject           TEXT,
  source_id         TEXT NOT NULL REFERENCES sources(id),
  locator           TEXT NOT NULL,
  quote             TEXT,
  valid_from        TEXT,
  valid_to          TEXT,
  strength          TEXT,                      -- strongest verb the evidence supports, e.g. 'led'
  sensitivity       TEXT NOT NULL CHECK (sensitivity IN ('free','use-with-approval','never-use')),
  tier              TEXT NOT NULL DEFAULT 'safe' CHECK (tier IN ('safe','specific')),
  confidence        TEXT NOT NULL CHECK (confidence IN ('verified','user_asserted','inferred')),
  status            TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN
                      ('candidate','confirmed','retired','disputed')),
  conflicts_with    TEXT,                      -- JSON array of claim ids
  usage_notes       TEXT,
  created_by        TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_at       TEXT
);

CREATE TABLE IF NOT EXISTS claim_tags (
  claim_id  TEXT NOT NULL REFERENCES claims(id),
  tag       TEXT NOT NULL,
  PRIMARY KEY (claim_id, tag)
);

CREATE TABLE IF NOT EXISTS claim_citations (
  document_id   INTEGER NOT NULL REFERENCES documents(id),
  claim_id      TEXT NOT NULL REFERENCES claims(id),
  paragraph     INTEGER NOT NULL,
  PRIMARY KEY (document_id, claim_id, paragraph)
);

-- ---------------------------------------------------------------------------
-- Inbox classifier support, source health
-- ---------------------------------------------------------------------------

-- Self-learning allowlist (research/C_tech_stack.md section 3.2): every confirmed
-- invite adds its sender domain, display name or link domain here.
CREATE TABLE IF NOT EXISTS sender_patterns (
  id                  INTEGER PRIMARY KEY,
  pattern_type        TEXT NOT NULL CHECK (pattern_type IN ('from_domain','display_name','link_domain')),
  value               TEXT NOT NULL,
  vendor              TEXT,
  category            TEXT,
  confirmed_count     INTEGER NOT NULL DEFAULT 1,
  last_seen_at        TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (pattern_type, value)
);

CREATE TABLE IF NOT EXISTS source_health (
  source                  TEXT PRIMARY KEY,
  last_run                TEXT,
  last_ok                 TEXT,
  consecutive_failures    INTEGER NOT NULL DEFAULT 0,
  notes                   TEXT
);

-- ---------------------------------------------------------------------------
-- Migration bookkeeping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migrations (
  version       TEXT PRIMARY KEY,
  applied_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- FTS5 (external content) full-text search, with the standard sync triggers
-- ---------------------------------------------------------------------------

CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
  text, quote, subject, content='claims', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS claims_ai AFTER INSERT ON claims BEGIN
  INSERT INTO claims_fts(rowid, text, quote, subject)
  VALUES (new.rowid, new.text, new.quote, new.subject);
END;

CREATE TRIGGER IF NOT EXISTS claims_ad AFTER DELETE ON claims BEGIN
  INSERT INTO claims_fts(claims_fts, rowid, text, quote, subject)
  VALUES ('delete', old.rowid, old.text, old.quote, old.subject);
END;

CREATE TRIGGER IF NOT EXISTS claims_au AFTER UPDATE ON claims BEGIN
  INSERT INTO claims_fts(claims_fts, rowid, text, quote, subject)
  VALUES ('delete', old.rowid, old.text, old.quote, old.subject);
  INSERT INTO claims_fts(rowid, text, quote, subject)
  VALUES (new.rowid, new.text, new.quote, new.subject);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS postings_fts USING fts5(
  title, description, requirements, content='postings', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS postings_ai AFTER INSERT ON postings BEGIN
  INSERT INTO postings_fts(rowid, title, description, requirements)
  VALUES (new.id, new.title, new.description, new.requirements);
END;

CREATE TRIGGER IF NOT EXISTS postings_ad AFTER DELETE ON postings BEGIN
  INSERT INTO postings_fts(postings_fts, rowid, title, description, requirements)
  VALUES ('delete', old.id, old.title, old.description, old.requirements);
END;

CREATE TRIGGER IF NOT EXISTS postings_au AFTER UPDATE ON postings BEGIN
  INSERT INTO postings_fts(postings_fts, rowid, title, description, requirements)
  VALUES ('delete', old.id, old.title, old.description, old.requirements);
  INSERT INTO postings_fts(rowid, title, description, requirements)
  VALUES (new.id, new.title, new.description, new.requirements);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS qa_fts USING fts5(
  question, answer, content='qa_bank', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS qa_bank_ai AFTER INSERT ON qa_bank BEGIN
  INSERT INTO qa_fts(rowid, question, answer) VALUES (new.id, new.question, new.answer);
END;

CREATE TRIGGER IF NOT EXISTS qa_bank_ad AFTER DELETE ON qa_bank BEGIN
  INSERT INTO qa_fts(qa_fts, rowid, question, answer) VALUES ('delete', old.id, old.question, old.answer);
END;

CREATE TRIGGER IF NOT EXISTS qa_bank_au AFTER UPDATE ON qa_bank BEGIN
  INSERT INTO qa_fts(qa_fts, rowid, question, answer) VALUES ('delete', old.id, old.question, old.answer);
  INSERT INTO qa_fts(rowid, question, answer) VALUES (new.id, new.question, new.answer);
END;

-- ---------------------------------------------------------------------------
-- updated_at stamping (guarded so the nested UPDATE cannot recurse even if a
-- future connection turns PRAGMA recursive_triggers on; SQLite's own default
-- is off, so plain re-entry is already impossible)
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS companies_set_updated_at AFTER UPDATE ON companies
WHEN NEW.updated_at IS OLD.updated_at
BEGIN
  UPDATE companies SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS postings_set_updated_at AFTER UPDATE ON postings
WHEN NEW.updated_at IS OLD.updated_at
BEGIN
  UPDATE postings SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS applications_set_updated_at AFTER UPDATE ON applications
WHEN NEW.updated_at IS OLD.updated_at
BEGIN
  UPDATE applications SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- ---------------------------------------------------------------------------
-- Application state machine: guard invalid transitions, log every valid one
-- ---------------------------------------------------------------------------

-- Allowed graph: draft -> ready-for-review -> approved -> submitted
--   -> {acknowledged, assessment, interview, rejected, ghosted}
-- assessment -> {interview, rejected}; interview -> {offer, rejected};
-- offer -> {accepted, rejected}; any status -> {withdrawn, rejected}.
CREATE TRIGGER IF NOT EXISTS applications_status_transition_guard
BEFORE UPDATE OF status ON applications
WHEN NEW.status IS NOT OLD.status
BEGIN
  SELECT RAISE(ABORT, 'invalid application status transition')
  WHERE NOT (
    (OLD.status = 'draft' AND NEW.status = 'ready-for-review') OR
    (OLD.status = 'ready-for-review' AND NEW.status = 'approved') OR
    (OLD.status = 'approved' AND NEW.status = 'submitted') OR
    (OLD.status = 'submitted' AND NEW.status IN ('acknowledged','assessment','interview','rejected','ghosted')) OR
    (OLD.status = 'assessment' AND NEW.status IN ('interview','rejected')) OR
    (OLD.status = 'interview' AND NEW.status IN ('offer','rejected')) OR
    (OLD.status = 'offer' AND NEW.status IN ('accepted','rejected')) OR
    NEW.status = 'withdrawn' OR
    NEW.status = 'rejected'
  );
END;

-- Fires only once the guard above has allowed the update through. Reads
-- NEW.last_transition_source / NEW.last_transition_detail_json, which
-- pipeline.db.transition_application sets in the same UPDATE statement.
CREATE TRIGGER IF NOT EXISTS applications_status_event
AFTER UPDATE OF status ON applications
WHEN NEW.status IS NOT OLD.status
BEGIN
  INSERT INTO events (occurred_at, entity, entity_id, type, from_status, to_status, source, detail_json)
  VALUES (datetime('now'), 'application', NEW.id, 'status_change', OLD.status, NEW.status,
          NEW.last_transition_source, NEW.last_transition_detail_json);
END;

-- ---------------------------------------------------------------------------
-- Indexes on the foreign keys and columns the pipeline filters/joins on most
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_postings_company        ON postings(company_id);
CREATE INDEX IF NOT EXISTS idx_postings_status          ON postings(status);
CREATE INDEX IF NOT EXISTS idx_applications_company     ON applications(company_id);
CREATE INDEX IF NOT EXISTS idx_applications_posting     ON applications(posting_id);
CREATE INDEX IF NOT EXISTS idx_applications_status      ON applications(status);
CREATE INDEX IF NOT EXISTS idx_events_entity            ON events(entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_contacts_company         ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_emails_application       ON emails(application_id);
CREATE INDEX IF NOT EXISTS idx_assessments_application  ON assessments(application_id);
CREATE INDEX IF NOT EXISTS idx_documents_application    ON documents(application_id);
CREATE INDEX IF NOT EXISTS idx_qa_bank_application      ON qa_bank(application_id);
CREATE INDEX IF NOT EXISTS idx_followups_application    ON followups(application_id);
CREATE INDEX IF NOT EXISTS idx_claims_source            ON claims(source_id);
CREATE INDEX IF NOT EXISTS idx_claims_status             ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claim_tags_tag            ON claim_tags(tag);
