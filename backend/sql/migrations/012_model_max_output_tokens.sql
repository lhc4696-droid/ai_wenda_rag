-- 为用户模型配置添加自定义输出上限支持
-- 允许不同模型配置不同的 max_output_tokens

ALTER TABLE user_model_settings ADD COLUMN max_output_tokens INTEGER;

-- 默认值为 NULL，表示使用全局配置
-- 用户可以根据模型设置合适的值（如 GPT-4 设置为 8192，Claude 设置为 4096）
