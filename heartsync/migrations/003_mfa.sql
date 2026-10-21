-- Optional two-factor sign-in with an authenticator app (TOTP, RFC 6238).
-- The shared secret is encrypted at rest (Fernet); recovery codes are stored
-- only as keyed hashes and can each be used once.
CREATE TABLE user_mfa (
    user_id         TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    secret_enc      TEXT NOT NULL,
    enabled_at      TEXT,                 -- NULL while setup is pending confirmation
    last_used_step  INTEGER NOT NULL DEFAULT 0,  -- blocks replay of an already-used code
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE mfa_recovery_codes (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL UNIQUE,
    used_at    TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_mfa_recovery_user ON mfa_recovery_codes(user_id);

-- Short-lived "password OK, second factor pending" state between the two
-- sign-in steps. No session exists until the challenge is completed.
CREATE TABLE mfa_challenges (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);
CREATE INDEX idx_mfa_challenges_user ON mfa_challenges(user_id);
