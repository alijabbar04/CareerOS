-- CareerOS database — migration 003: T-012 dedupe and fit-scoring state.
--
-- `rule_score` was temporarily used by T-011 as a 1/0 registry-regex match.
-- Preserve that signal in its own column before T-012 starts writing a 0-100
-- rule score. Existing values are then cleared so a legacy `1` cannot be
-- mistaken for a completed fit score.

ALTER TABLE postings ADD COLUMN source_match          INTEGER;
ALTER TABLE postings ADD COLUMN duplicate_of          INTEGER REFERENCES postings(id);
ALTER TABLE postings ADD COLUMN dedupe_signals        TEXT;
ALTER TABLE postings ADD COLUMN rule_reasons          TEXT;
ALTER TABLE postings ADD COLUMN local_model_score     INTEGER;
ALTER TABLE postings ADD COLUMN local_model_reasons   TEXT;
ALTER TABLE postings ADD COLUMN missing_requirements  TEXT;
ALTER TABLE postings ADD COLUMN cited_claims          TEXT;
ALTER TABLE postings ADD COLUMN score_version         TEXT;
ALTER TABLE postings ADD COLUMN model_fingerprint     TEXT;
ALTER TABLE postings ADD COLUMN scored_at             TEXT;

UPDATE postings SET source_match = rule_score WHERE source_match IS NULL;
UPDATE postings SET rule_score = NULL;

CREATE INDEX IF NOT EXISTS idx_postings_source_match ON postings(source_match);
CREATE INDEX IF NOT EXISTS idx_postings_duplicate_of ON postings(duplicate_of);
CREATE INDEX IF NOT EXISTS idx_postings_scores ON postings(status, llm_score, rule_score);
