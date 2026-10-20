-- HeartSync initial schema.
-- Timestamps are ISO-8601 UTC strings (lexicographically sortable).
-- Sensitive contact data (phone numbers) lives in user_contacts, separate from
-- the publicly readable profile.

CREATE TABLE users (
    id                TEXT PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,
    password_hash     TEXT NOT NULL,
    date_of_birth     TEXT NOT NULL,              -- YYYY-MM-DD, never exposed publicly
    role              TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','moderator','admin')),
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','deactivated','suspended','deleted')),
    email_verified_at TEXT,
    terms_accepted_at TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_active_at    TEXT,
    deleted_at        TEXT,
    suspended_reason  TEXT
);
CREATE INDEX idx_users_status ON users(status);
CREATE INDEX idx_users_created ON users(created_at);
CREATE INDEX idx_users_active ON users(last_active_at);
CREATE INDEX idx_users_dob ON users(date_of_birth);

CREATE TABLE user_contacts (
    user_id           TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    phone_e164        TEXT UNIQUE,
    phone_verified_at TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE sessions (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,
    csrf_token    TEXT NOT NULL,
    user_agent    TEXT,
    ip_hash       TEXT,
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    revoked_at    TEXT
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

CREATE TABLE auth_tokens (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL CHECK (purpose IN ('verify_email','reset_password')),
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);
CREATE INDEX idx_auth_tokens_user ON auth_tokens(user_id, purpose);

CREATE TABLE otp_codes (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phone_e164   TEXT NOT NULL,
    code_hash    TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT
);
CREATE INDEX idx_otp_user ON otp_codes(user_id, created_at);

CREATE TABLE profiles (
    user_id             TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    display_name        TEXT NOT NULL,
    gender              TEXT NOT NULL,
    pronouns            TEXT,
    interested_in       TEXT NOT NULL DEFAULT '[]',   -- JSON array of genders
    headline            TEXT,
    about               TEXT,
    country             TEXT,
    region              TEXT,
    city                TEXT,
    lat_approx          REAL,                          -- rounded to ~10km; never exact GPS
    lng_approx          REAL,
    languages           TEXT NOT NULL DEFAULT '[]',
    height_cm           INTEGER,
    education           TEXT,
    profession          TEXT,
    industry            TEXT,
    work_status         TEXT,
    work_schedule       TEXT,
    religion            TEXT,
    relationship_status TEXT,
    smoking             TEXT,
    drinking            TEXT,
    diet                TEXT,
    exercise            TEXT,
    pets                TEXT,
    sleep_habits        TEXT,
    travel_interests    TEXT NOT NULL DEFAULT '[]',
    relationship_goals  TEXT NOT NULL DEFAULT '[]',
    partner_qualities   TEXT NOT NULL DEFAULT '[]',
    communication_style TEXT,
    family_plans        TEXT,
    future_goals        TEXT,
    open_to_relocate    INTEGER,
    field_visibility    TEXT NOT NULL DEFAULT '{}',    -- JSON {field: everyone|matches|hidden}
    visibility          TEXT NOT NULL DEFAULT 'visible' CHECK (visibility IN ('visible','hidden')),
    show_online_status  INTEGER NOT NULL DEFAULT 1,
    show_last_seen      INTEGER NOT NULL DEFAULT 1,
    photo_verified      INTEGER NOT NULL DEFAULT 0,
    completeness        INTEGER NOT NULL DEFAULT 0,
    onboarded_at        TEXT,
    boost_until         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_profiles_discover ON profiles(visibility, gender);
CREATE INDEX idx_profiles_city ON profiles(country, region, city);
CREATE INDEX idx_profiles_geo ON profiles(lat_approx, lng_approx);
CREATE INDEX idx_profiles_height ON profiles(height_cm);

CREATE TABLE dating_preferences (
    user_id          TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    age_min          INTEGER NOT NULL DEFAULT 18,
    age_max          INTEGER NOT NULL DEFAULT 60,
    max_distance_km  INTEGER,
    nearby_enabled   INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL
);

CREATE TABLE interests (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    slug     TEXT NOT NULL UNIQUE,
    label    TEXT NOT NULL,
    category TEXT NOT NULL
);

CREATE TABLE profile_interests (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    interest_id INTEGER NOT NULL REFERENCES interests(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, interest_id)
);
CREATE INDEX idx_profile_interests_interest ON profile_interests(interest_id);

CREATE TABLE profile_photos (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    storage_key  TEXT NOT NULL,
    position     INTEGER NOT NULL DEFAULT 0,
    width        INTEGER,
    height       INTEGER,
    status       TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('approved','pending','rejected')),
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_photos_user ON profile_photos(user_id, position);

CREATE TABLE photo_verifications (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    storage_key  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    reviewer_id  TEXT REFERENCES users(id) ON DELETE SET NULL,
    notes        TEXT,
    created_at   TEXT NOT NULL,
    reviewed_at  TEXT
);
CREATE INDEX idx_photo_verif_status ON photo_verifications(status, created_at);

CREATE TABLE swipes (
    from_user   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('like','superlike','pass')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (from_user, to_user),
    CHECK (from_user <> to_user)
);
CREATE INDEX idx_swipes_to ON swipes(to_user, kind);
CREATE INDEX idx_swipes_from_time ON swipes(from_user, created_at);

CREATE TABLE matches (
    id           TEXT PRIMARY KEY,
    user_a       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_b       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    unmatched_at TEXT,
    unmatched_by TEXT,
    UNIQUE (user_a, user_b),
    CHECK (user_a < user_b)
);
CREATE INDEX idx_matches_b ON matches(user_b);

CREATE TABLE conversations (
    id               TEXT PRIMARY KEY,
    match_id         TEXT NOT NULL UNIQUE REFERENCES matches(id) ON DELETE CASCADE,
    created_at       TEXT NOT NULL,
    last_message_at  TEXT
);

CREATE TABLE conversation_participants (
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_read_id     INTEGER NOT NULL DEFAULT 0,
    muted            INTEGER NOT NULL DEFAULT 0,
    archived         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (conversation_id, user_id)
);
CREATE INDEX idx_participants_user ON conversation_participants(user_id);

CREATE TABLE messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id        TEXT REFERENCES users(id) ON DELETE SET NULL,
    kind             TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text','image','system')),
    body             TEXT,
    reply_to_id      INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    client_id        TEXT,                               -- idempotency key from sender
    created_at       TEXT NOT NULL,
    delivered_at     TEXT,
    read_at          TEXT,
    edited_at        TEXT,
    deleted_at       TEXT,
    UNIQUE (sender_id, client_id)
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, id);

CREATE TABLE message_attachments (
    id           TEXT PRIMARY KEY,
    message_id   INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    storage_key  TEXT NOT NULL,
    mime         TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    width        INTEGER,
    height       INTEGER
);
CREATE INDEX idx_attachments_msg ON message_attachments(message_id);

CREATE TABLE message_reactions (
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    emoji       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (message_id, user_id)
);

CREATE TABLE phone_shares (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    match_id     TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    revoked_at   TEXT
);
CREATE INDEX idx_phone_shares_pair ON phone_shares(owner_id, recipient_id);

CREATE TABLE blocks (
    blocker_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id),
    CHECK (blocker_id <> blocked_id)
);
CREATE INDEX idx_blocks_blocked ON blocks(blocked_id);

CREATE TABLE reports (
    id                TEXT PRIMARY KEY,
    reporter_id       TEXT REFERENCES users(id) ON DELETE SET NULL,
    reported_user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id        INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    category          TEXT NOT NULL,
    details           TEXT,
    evidence_snapshot TEXT,           -- JSON copy of reported content, admin-only
    status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reviewing','resolved','dismissed')),
    resolution        TEXT,
    assigned_to       TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL,
    resolved_at       TEXT
);
CREATE INDEX idx_reports_status ON reports(status, created_at);
CREATE INDEX idx_reports_target ON reports(reported_user_id);

CREATE TABLE moderation_actions (
    id              TEXT PRIMARY KEY,
    actor_id        TEXT REFERENCES users(id) ON DELETE SET NULL,
    target_user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action          TEXT NOT NULL,
    reason          TEXT,
    report_id       TEXT REFERENCES reports(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_modactions_target ON moderation_actions(target_user_id);

CREATE TABLE appeals (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','upheld','overturned')),
    reviewer_id  TEXT REFERENCES users(id) ON DELETE SET NULL,
    decision     TEXT,
    created_at   TEXT NOT NULL,
    reviewed_at  TEXT
);

CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT,
    link        TEXT,
    dedupe_key  TEXT,
    created_at  TEXT NOT NULL,
    read_at     TEXT,
    UNIQUE (user_id, dedupe_key)
);
CREATE INDEX idx_notifications_user ON notifications(user_id, id);

CREATE TABLE notification_preferences (
    user_id         TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    inapp_likes     INTEGER NOT NULL DEFAULT 1,
    inapp_messages  INTEGER NOT NULL DEFAULT 1,
    email_matches   INTEGER NOT NULL DEFAULT 1,
    email_messages  INTEGER NOT NULL DEFAULT 0,
    email_marketing INTEGER NOT NULL DEFAULT 0,
    quiet_start     TEXT,          -- HH:MM local
    quiet_end       TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE subscriptions (
    id                        TEXT PRIMARY KEY,
    user_id                   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan                      TEXT NOT NULL,
    status                    TEXT NOT NULL CHECK (status IN ('active','past_due','canceled','expired')),
    provider                  TEXT NOT NULL,
    provider_subscription_id  TEXT UNIQUE,
    current_period_end        TEXT,
    cancel_at_period_end      INTEGER NOT NULL DEFAULT 0,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);
CREATE INDEX idx_subscriptions_user ON subscriptions(user_id, status);

CREATE TABLE payments (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id      TEXT REFERENCES subscriptions(id) ON DELETE SET NULL,
    provider             TEXT NOT NULL,
    provider_payment_id  TEXT NOT NULL UNIQUE,
    amount_minor         INTEGER NOT NULL,
    currency             TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('succeeded','failed','refunded','pending')),
    description          TEXT,
    created_at           TEXT NOT NULL
);
CREATE INDEX idx_payments_user ON payments(user_id, created_at);

CREATE TABLE checkout_sessions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan         TEXT NOT NULL,
    provider     TEXT NOT NULL,
    provider_ref TEXT,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','completed','expired')),
    created_at   TEXT NOT NULL
);

CREATE TABLE webhook_events (
    id           TEXT PRIMARY KEY,           -- provider event id (idempotency)
    provider     TEXT NOT NULL,
    type         TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE boosts (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at  TEXT NOT NULL,
    ends_at     TEXT NOT NULL
);
CREATE INDEX idx_boosts_user ON boosts(user_id, started_at);

CREATE TABLE saved_profiles (
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    saved_user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (user_id, saved_user_id)
);

CREATE TABLE filter_presets (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    filters     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE profile_views (
    viewer_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    viewed_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    seen_at    TEXT NOT NULL,
    PRIMARY KEY (viewer_id, viewed_id)
);

CREATE TABLE audit_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id     TEXT REFERENCES users(id) ON DELETE SET NULL,
    action       TEXT NOT NULL,
    target_type  TEXT,
    target_id    TEXT,
    metadata     TEXT,
    ip_hash      TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_audit_created ON audit_logs(created_at);
CREATE INDEX idx_audit_target ON audit_logs(target_type, target_id);

CREATE TABLE content_pages (
    slug        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT REFERENCES users(id) ON DELETE SET NULL
);

-- Development-only outbox so email/SMS can be inspected locally without a provider.
CREATE TABLE dev_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    subject     TEXT,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
