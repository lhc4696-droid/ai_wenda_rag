-- 仅由迁移入口在旧版 users 表上执行一次，不删除或伪造任何旧账号数据。
ALTER TABLE users ADD COLUMN email TEXT;
ALTER TABLE users ADD COLUMN email_verified_at INTEGER;
ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0;
