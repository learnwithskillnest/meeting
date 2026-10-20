-- Automatic risk flag: set when a member receives reports from several distinct
-- people in a short window. Flagged members are removed from discovery until
-- a moderator reviews them. Existing matches are unaffected.
ALTER TABLE users ADD COLUMN flagged_at TEXT;
ALTER TABLE users ADD COLUMN flag_reason TEXT;
CREATE INDEX idx_users_flagged ON users(flagged_at);
