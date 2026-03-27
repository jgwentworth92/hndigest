-- 001_initial_schema.sql
-- Creates all seven tables from SPEC-000 section 6.

CREATE TABLE stories (
    id          INTEGER PRIMARY KEY,  -- HN item ID (not auto-increment)
    title       TEXT    NOT NULL,
    url         TEXT,                 -- nullable for Ask HN
    hn_text     TEXT,                 -- nullable, Ask HN / Show HN descriptions
    score       INTEGER NOT NULL DEFAULT 0,
    comments    INTEGER NOT NULL DEFAULT 0,
    author      TEXT    NOT NULL,
    posted_at   TEXT    NOT NULL,     -- ISO 8601 UTC
    hn_type     TEXT    NOT NULL,     -- "story", "show", "ask", "job"
    endpoints   TEXT    NOT NULL DEFAULT '[]',  -- JSON array of endpoint names
    first_seen  TEXT    NOT NULL,     -- ISO 8601 UTC
    last_updated TEXT   NOT NULL      -- ISO 8601 UTC
);

CREATE INDEX idx_stories_posted_at ON stories (posted_at);
CREATE INDEX idx_stories_hn_type ON stories (hn_type);
CREATE INDEX idx_stories_first_seen ON stories (first_seen);
CREATE INDEX idx_stories_last_updated ON stories (last_updated);

CREATE TABLE score_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id),
    score       INTEGER NOT NULL,
    comments    INTEGER NOT NULL,
    snapshot_at TEXT    NOT NULL      -- ISO 8601 UTC
);

CREATE INDEX idx_score_snapshots_story_id ON score_snapshots (story_id);
CREATE INDEX idx_score_snapshots_snapshot_at ON score_snapshots (snapshot_at);

CREATE TABLE articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id      INTEGER NOT NULL REFERENCES stories(id),
    text          TEXT    NOT NULL,
    text_hash     TEXT    NOT NULL,   -- SHA-256 of extracted text
    fetch_status  TEXT    NOT NULL,   -- "success", "failed", "paywall", "timeout", "no_url"
    fetched_at    TEXT    NOT NULL    -- ISO 8601 UTC
);

CREATE INDEX idx_articles_story_id ON articles (story_id);
CREATE INDEX idx_articles_fetched_at ON articles (fetched_at);

CREATE TABLE categories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id        INTEGER NOT NULL REFERENCES stories(id),
    category        TEXT    NOT NULL,  -- assigned category tag
    method          TEXT    NOT NULL,  -- "domain", "keyword", "hn_type", "uncategorized"
    categorized_at  TEXT    NOT NULL   -- ISO 8601 UTC
);

CREATE INDEX idx_categories_story_id ON categories (story_id);
CREATE INDEX idx_categories_category ON categories (category);
CREATE INDEX idx_categories_categorized_at ON categories (categorized_at);

CREATE TABLE scores (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id             INTEGER NOT NULL REFERENCES stories(id),
    score_velocity       REAL    NOT NULL,  -- points per hour
    comment_velocity     REAL    NOT NULL,  -- comments per hour
    front_page_presence  INTEGER NOT NULL,  -- number of endpoints
    recency              REAL    NOT NULL,  -- decay-weighted recency score
    composite            REAL    NOT NULL,  -- weighted composite score (0-100)
    scored_at            TEXT    NOT NULL    -- ISO 8601 UTC
);

CREATE INDEX idx_scores_story_id ON scores (story_id);
CREATE INDEX idx_scores_composite ON scores (composite);
CREATE INDEX idx_scores_scored_at ON scores (scored_at);

CREATE TABLE summaries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id         INTEGER NOT NULL REFERENCES stories(id),
    summary_text     TEXT    NOT NULL,
    source_text_hash TEXT    NOT NULL,  -- SHA-256 of article text used
    status           TEXT    NOT NULL,  -- "pending_validation", "validated", "rejected", "no_summary"
    generated_at     TEXT    NOT NULL   -- ISO 8601 UTC
);

CREATE INDEX idx_summaries_story_id ON summaries (story_id);
CREATE INDEX idx_summaries_status ON summaries (status);
CREATE INDEX idx_summaries_generated_at ON summaries (generated_at);

CREATE TABLE validations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_id    INTEGER NOT NULL REFERENCES summaries(id),
    result        TEXT    NOT NULL,  -- "pass", "fail"
    details       TEXT,              -- JSON per-claim citation check results
    validated_at  TEXT    NOT NULL   -- ISO 8601 UTC
);

CREATE INDEX idx_validations_summary_id ON validations (summary_id);
CREATE INDEX idx_validations_validated_at ON validations (validated_at);

CREATE TABLE digests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start  TEXT    NOT NULL,  -- ISO 8601 UTC
    period_end    TEXT    NOT NULL,  -- ISO 8601 UTC
    content_json  TEXT    NOT NULL,  -- structured digest data (JSON)
    content_md    TEXT    NOT NULL,  -- rendered markdown digest
    story_count   INTEGER NOT NULL,
    created_at    TEXT    NOT NULL   -- ISO 8601 UTC
);

CREATE INDEX idx_digests_period_start ON digests (period_start);
CREATE INDEX idx_digests_period_end ON digests (period_end);
CREATE INDEX idx_digests_created_at ON digests (created_at);
