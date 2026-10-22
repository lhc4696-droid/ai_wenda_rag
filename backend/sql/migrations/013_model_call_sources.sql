-- Old records have no reliable provider ownership. Preserve them explicitly.
ALTER TABLE model_calls ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'
    CHECK (source IN ('legacy', 'default', 'custom', 'embedding'));
CREATE INDEX IF NOT EXISTS model_calls_source_time ON model_calls(source, created_at);
