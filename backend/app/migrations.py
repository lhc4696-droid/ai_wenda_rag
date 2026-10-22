"""新库初始化和旧库原子迁移；禁止用删库重建的方式升级。"""

from pathlib import Path
import sqlite3
import time


SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
MIGRATIONS = ((2, "002_model_calls.sql"), (3, "003_conversation_history.sql"), (4, "004_attachments.sql"), (5, "005_rag_modes.sql"), (6, "006_coding_agent.sql"), (7, "007_local_coding.sql"), (8, "008_local_coding_conversations.sql"), (9, "009_user_model_settings.sql"), (10, "010_local_coding_projects.sql"), (11, "011_local_project_names.sql"), (12, "012_model_max_output_tokens.sql"), (13, "013_model_call_sources.sql"))
CURRENT_VERSION = 13


def execute_statements(connection: sqlite3.Connection, source: str):
    # executescript 会隐式提交；逐条执行才能让 ALTER 和版本记录共用同一事务。
    statement = ""
    for line in source.splitlines(keepends=True):
        if line.strip().upper().startswith("PRAGMA "):
            continue
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip() and not all(not line.strip() or line.strip().startswith("--") for line in statement.splitlines()):
        raise ValueError("SQL 脚本缺少语句结束符")


def initialize_database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)")
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            if applied and max(applied) > CURRENT_VERSION:
                raise RuntimeError("数据库版本高于当前程序，请使用匹配的程序版本，禁止自动降级")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            if columns and "email" not in columns:
                execute_statements(connection, (SQL_DIR / "migrations/001_email_verification.sql").read_text(encoding="utf-8"))
            elif columns and not {"email", "email_verified_at", "auth_version"}.issubset(columns):
                raise RuntimeError("账号表结构不完整，请先备份数据库并检查迁移状态")
            if not columns:
                execute_statements(connection, (SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
            connection.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1, ?)", (int(time.time()),))
            for version, filename in MIGRATIONS:
                if version not in applied:
                    if version == 5:
                        message_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
                        if {"source_mode", "sources_json"}.issubset(message_columns):
                            # 新库已由 schema.sql 创建完整结构，不重复 ALTER。
                            pass
                        elif message_columns & {"source_mode", "sources_json"}:
                            raise RuntimeError("RAG 消息字段结构不完整，请先备份数据库并检查迁移状态")
                        else:
                            execute_statements(connection, (SQL_DIR / "migrations" / filename).read_text(encoding="utf-8"))
                    elif version == 8:
                        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(local_coding_tasks)")}
                        if {"conversation_id", "turn_index"}.issubset(task_columns):
                            # Fresh schema already contains the complete conversation model.
                            pass
                        elif task_columns & {"conversation_id", "turn_index"}:
                            raise RuntimeError("编程会话结构不完整，请先备份数据库并检查迁移状态")
                        else:
                            execute_statements(connection, (SQL_DIR / "migrations" / filename).read_text(encoding="utf-8"))
                    elif version == 10:
                        columns = {row[1] for row in connection.execute("PRAGMA table_info(local_coding_conversations)")}
                        if "project_id" not in columns:
                            execute_statements(connection, (SQL_DIR / "migrations" / filename).read_text(encoding="utf-8"))
                    elif version == 11:
                        columns = {row[1] for row in connection.execute("PRAGMA table_info(local_coding_projects)")}
                        if {"name", "name_request"}.issubset(columns):
                            pass
                        elif columns & {"name", "name_request"}:
                            raise RuntimeError("项目名称字段不完整，请先备份数据库并检查迁移状态")
                        else:
                            execute_statements(connection, (SQL_DIR / "migrations" / filename).read_text(encoding="utf-8"))
                    elif version == 12:
                        columns = {row[1] for row in connection.execute("PRAGMA table_info(user_model_settings)")}
                        if "max_output_tokens" not in columns:
                            execute_statements(connection, (SQL_DIR / "migrations" / filename).read_text(encoding="utf-8"))
                    else:
                        execute_statements(connection, (SQL_DIR / "migrations" / filename).read_text(encoding="utf-8"))
                    connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, int(time.time())))
            execute_statements(connection, (SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    finally:
        connection.close()
