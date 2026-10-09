-- 记录每次问答选用的资料模式及可核查的来源；不存储向量。
ALTER TABLE messages ADD COLUMN source_mode TEXT NOT NULL DEFAULT 'legacy'
    CHECK (source_mode IN ('legacy', 'chat', 'documents'));
ALTER TABLE messages ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]';
