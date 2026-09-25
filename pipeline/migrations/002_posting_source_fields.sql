-- CareerOS database — migration 002: extra posting fields for T-011 discovery fetchers.
--
-- The normalised posting schema (research/D_job_sources.md section 4;
-- pipeline/sources/common.py normalize_posting()) carries three fields
-- 001_initial.sql's postings table had no column for: employment_type,
-- department and a truncated raw_json snapshot of the source API response
-- (kept for debugging a fetcher and for reprocessing without re-polling).
--
-- Unlike 001_initial.sql, ALTER TABLE ... ADD COLUMN is not safe to re-run by
-- hand (SQLite has no "ADD COLUMN IF NOT EXISTS"); pipeline.db.migrate() is
-- still idempotent because it tracks applied versions in schema_migrations
-- and never re-executes a version it has already applied.

ALTER TABLE postings ADD COLUMN employment_type TEXT;
ALTER TABLE postings ADD COLUMN department       TEXT;
ALTER TABLE postings ADD COLUMN raw_json         TEXT;
